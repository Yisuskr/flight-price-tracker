"""
main.py - Entry point for the Flight Price Tracker.

Cuatro fuentes corren en ciclos independientes:
  - Google Flights (SerpAPI): cada 12h
  - Kiwi.com:                 cada 60h  (~96 llamadas/mes, límite gratuito: 500)
  - Skyscanner (RapidAPI):    cada 72h  (~80 llamadas/mes, límite gratuito: 100)
  - Aviasales (Travelpayouts):cada 24h  (sin límite declarado, caché ~48h)

Tras cada fetch se fusionan los resultados en caché, se guardan en SQLite
y se envía un email comparativo si alguna opción baja del umbral.
"""

import argparse
import logging
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import schedule

from tracker.config import load_config
from tracker.flight import FlightResult
from tracker.notifier import Notifier
from tracker.sources.aggregator import (
    fetch_google,
    fetch_kiwi,
    fetch_skyscanner,
    fetch_aviasales,
    get_merged_results,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("tracker.main")

# ---------------------------------------------------------------------------
# Base de datos
# ---------------------------------------------------------------------------
def _resolve_db_path() -> str:
    """
    Try writable locations in order. Fall back to in-memory DB (':memory:')
    if nothing is writable (e.g. Render free tier with no disk).
    """
    candidates = [
        Path(__file__).parent.parent / "data" / "prices.db",
        Path("/tmp/prices.db"),
    ]
    for path in candidates:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Test that we can actually write there
            test = path.parent / ".write_test"
            test.touch()
            test.unlink()
            return str(path)
        except OSError:
            continue
    return ":memory:"


def init_db() -> sqlite3.Connection:
    db_path = _resolve_db_path()
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS price_history (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            checked_at    TEXT    NOT NULL,
            origin        TEXT    NOT NULL,
            destination   TEXT    NOT NULL,
            outbound_date TEXT    NOT NULL,
            return_date   TEXT,
            airline       TEXT,
            price         REAL    NOT NULL,
            currency      TEXT    NOT NULL DEFAULT 'EUR',
            duration      TEXT,
            stops         INTEGER,
            layovers      TEXT,
            source        TEXT,
            alerted       INTEGER DEFAULT 0
        )
        """
    )
    conn.commit()
    logger.info("Base de datos lista en %s", db_path)
    return conn


def record_price(
    conn: sqlite3.Connection, flight: FlightResult, currency: str, alerted: bool
) -> None:
    conn.execute(
        """
        INSERT INTO price_history
            (checked_at, origin, destination, outbound_date, return_date,
             airline, price, currency, duration, stops, layovers, source, alerted)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            flight.origin,
            flight.destination,
            flight.outbound_date,
            flight.return_date,
            flight.airline,
            flight.price,
            currency,
            flight.duration,
            flight.stops,
            flight.layovers_str(),
            getattr(flight, "source", ""),
            1 if alerted else 0,
        ),
    )
    conn.commit()


def get_history_summary(conn: sqlite3.Connection, currency: str) -> str:
    row = conn.execute(
        "SELECT COUNT(*), MIN(price), MAX(price), AVG(price) FROM price_history"
    ).fetchone()
    if not row or row[0] == 0:
        return "Sin historial de precios todavía."
    count, low, high, avg = row
    sym = "€" if currency == "EUR" else currency
    return (
        f"Historial: {count} checks | "
        f"Mínimo: {sym}{low:.0f} | Máximo: {sym}{high:.0f} | Media: {sym}{avg:.0f}"
    )


# ---------------------------------------------------------------------------
# Evaluación y alerta — se llama tras cada fetch de cualquier fuente
# ---------------------------------------------------------------------------
def evaluate_and_alert(
    cfg: dict,
    conn: sqlite3.Connection,
    notifier: Notifier,
    source_label: str,
) -> None:
    """
    Fusiona los resultados del caché de todas las fuentes, guarda en BD
    y envía alerta si hay opciones por debajo del umbral.
    """
    currency = cfg["currency"]
    sym = "€" if currency == "EUR" else currency
    threshold = float(cfg["price_threshold_usd"])

    results = get_merged_results()
    if not results:
        logger.warning("[%s] Sin resultados tras fusionar fuentes.", source_label)
        return

    alerts_to_send = []
    for flight in results:
        should_alert = flight.price <= threshold
        record_price(conn, flight, currency, alerted=should_alert)
        if should_alert:
            alerts_to_send.append(flight)
        logger.info(
            "[%s] %s->%s | sal %s vuel %s | %s%s | %s | %s | %s",
            "ALERTA" if should_alert else "     ",
            flight.origin, flight.destination,
            flight.outbound_date, flight.return_date or "—",
            sym, f"{flight.price:.0f}",
            flight.airline,
            flight.layovers_str(),
            getattr(flight, "source", ""),
        )

    if alerts_to_send:
        logger.info(
            "%d opción(es) por debajo del umbral. Enviando email...",
            len(alerts_to_send),
        )
        notifier.send_alert_batch(results, alerts_to_send, threshold)
    else:
        # Siempre manda el resumen aunque nada baje del umbral
        logger.info(
            "El más barato es %s%s, por encima del umbral %s%s. Enviando resumen...",
            sym, f"{results[0].price:.0f}", sym, f"{threshold:.0f}",
        )
        notifier.send_alert_batch(results, [], threshold)

    logger.info(
        "=== [%s] Ciclo completo — %d resultados fusionados ===",
        source_label, len(results),
    )


# ---------------------------------------------------------------------------
# Jobs del scheduler (uno por fuente)
# ---------------------------------------------------------------------------
def run_google(cfg: dict, conn: sqlite3.Connection, notifier: Notifier) -> None:
    logger.info("=== [Google] Iniciando fetch ===")
    logger.info(get_history_summary(conn, cfg["currency"]))
    fetch_google(cfg)
    evaluate_and_alert(cfg, conn, notifier, "Google")


def run_kiwi(cfg: dict, conn: sqlite3.Connection, notifier: Notifier) -> None:
    logger.info("=== [Kiwi] Iniciando fetch ===")
    fetch_kiwi(cfg)
    evaluate_and_alert(cfg, conn, notifier, "Kiwi")


def run_skyscanner(cfg: dict, conn: sqlite3.Connection, notifier: Notifier) -> None:
    logger.info("=== [Skyscanner] Iniciando fetch ===")
    fetch_skyscanner(cfg)
    evaluate_and_alert(cfg, conn, notifier, "Skyscanner")


def run_aviasales(cfg: dict, conn: sqlite3.Connection, notifier: Notifier) -> None:
    logger.info("=== [Aviasales] Iniciando fetch ===")
    fetch_aviasales(cfg)
    evaluate_and_alert(cfg, conn, notifier, "Aviasales")


# ---------------------------------------------------------------------------
# Minimal HTTP health server — required by Render's free web service tier
# Runs in a background thread; does not affect the tracker logic
# ---------------------------------------------------------------------------
class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):  # noqa: A002
        pass  # silence access logs


def _start_health_server() -> None:
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Health server escuchando en puerto %d", port)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tenerife -> Miami Flight Price Tracker")
    parser.add_argument("--test-email", action="store_true", help="Envía email de prueba y sale.")
    parser.add_argument(
        "--check-now", action="store_true",
        help="Ejecuta todas las fuentes inmediatamente y sale.",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    args = parse_args()
    cfg = load_config()
    conn = init_db()
    notifier = Notifier(cfg)

    # Start health server so Render's web service tier keeps the process alive
    _start_health_server()

    if args.test_email:
        logger.info("Enviando email de prueba a %s ...", cfg["alert_email"])
        ok = notifier.send_test_email()
        sys.exit(0 if ok else 1)

    if args.check_now:
        logger.info("=== --check-now: ejecutando todas las fuentes ===")
        run_google(cfg, conn, notifier)
        run_kiwi(cfg, conn, notifier)
        run_skyscanner(cfg, conn, notifier)
        run_aviasales(cfg, conn, notifier)
        sys.exit(0)

    # ── Intervalos por fuente ──────────────────────────────────────────────
    google_h = int(cfg.get("check_interval_hours", 12))
    kiwi_h = int(cfg.get("kiwi_interval_hours", 60))
    sky_h = int(cfg.get("skyscanner_interval_hours", 72))
    avia_h = int(cfg.get("aviasales_interval_hours", 24))

    sym = "€" if cfg["currency"] == "EUR" else cfg["currency"]
    n_combos = (
        len(cfg["origin_airports"])
        * len(cfg["outbound_dates"])
        * max(len(cfg.get("return_dates", [])), 1)
    )

    logger.info(
        "Tracker iniciado | %d combos/ciclo | umbral: %s%s",
        n_combos, sym, cfg["price_threshold_usd"],
    )
    logger.info(
        "Intervalos → Google: %dh | Kiwi: %dh | Skyscanner: %dh | Aviasales: %dh",
        google_h, kiwi_h, sky_h, avia_h,
    )

    # NO ejecutamos ciclo inmediato al arrancar — los emails llegan a horas fijas
    # (06:00 y 18:00 hora España)

    # ── Programar ciclos a horas fijas (UTC) ──────────────────────────────
    # España = UTC+2 (verano) → 06:00 ES = 04:00 UTC, 18:00 ES = 16:00 UTC
    schedule.every().day.at("04:00").do(run_google, cfg, conn, notifier)
    schedule.every().day.at("16:00").do(run_google, cfg, conn, notifier)
    schedule.every().day.at("04:05").do(run_aviasales, cfg, conn, notifier)
    schedule.every(kiwi_h).hours.do(run_kiwi, cfg, conn, notifier)
    schedule.every(sky_h).hours.do(run_skyscanner, cfg, conn, notifier)

    logger.info("Próximos checks → Google: 06:00 y 18:00 ES | Aviasales: 06:00 ES")

    try:
        while True:
            schedule.run_pending()
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Tracker detenido por el usuario.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
