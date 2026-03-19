"""
main.py - Entry point for the Flight Price Tracker.

Busca TODAS las combinaciones de fechas y aeropuertos en cada ciclo,
guarda el historial en SQLite y envía un email comparativo cuando
alguna opción baja del precio umbral.
"""

import argparse
import logging
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import schedule

from tracker.config import load_config
from tracker.flight import FlightResult, fetch_all_combinations
from tracker.notifier import Notifier

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
DB_PATH = Path(__file__).parent.parent / "data" / "prices.db"


def init_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
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
            alerted       INTEGER DEFAULT 0
        )
        """
    )
    conn.commit()
    logger.info("Base de datos lista en %s", DB_PATH)
    return conn


def record_price(
    conn: sqlite3.Connection, flight: FlightResult, currency: str, alerted: bool
) -> None:
    conn.execute(
        """
        INSERT INTO price_history
            (checked_at, origin, destination, outbound_date, return_date,
             airline, price, currency, duration, stops, layovers, alerted)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
# Lógica principal de cada ciclo
# ---------------------------------------------------------------------------
def run_check(cfg: dict, conn: sqlite3.Connection, notifier: Notifier) -> None:
    currency = cfg["currency"]
    sym = "€" if currency == "EUR" else currency
    threshold = float(cfg["price_threshold_usd"])

    logger.info("=== Iniciando ciclo de búsqueda ===")
    logger.info(get_history_summary(conn, currency))
    logger.info(
        "Combinaciones: %s -> %s | salidas: %s | vueltas: %s | umbral: %s%s",
        cfg["origin_airports"], cfg["destination"],
        cfg["outbound_dates"], cfg.get("return_dates", []),
        sym, threshold,
    )

    results = fetch_all_combinations(
        origin_airports=cfg["origin_airports"],
        destination=cfg["destination"],
        outbound_dates=cfg["outbound_dates"],
        return_dates=cfg.get("return_dates", []),
        adults=cfg["adults"],
        currency=currency,
        serpapi_key=cfg["serpapi_key"],
        carry_on_bags=cfg.get("carry_on_bags", 0),
        checked_bags=cfg.get("checked_bags", 0),
    )

    if not results:
        logger.warning("Sin resultados en este ciclo. Se reintentará en el próximo intervalo.")
        return

    alerts_to_send = []
    for flight in results:
        should_alert = flight.price <= threshold
        record_price(conn, flight, currency, alerted=should_alert)
        if should_alert:
            alerts_to_send.append(flight)
        logger.info(
            "[%s] %s->%s | sal %s vuel %s | %s%s | %s | %s",
            "ALERTA" if should_alert else "     ",
            flight.origin, flight.destination,
            flight.outbound_date, flight.return_date or "—",
            sym, f"{flight.price:.0f}",
            flight.airline,
            flight.layovers_str(),
        )

    if alerts_to_send:
        logger.info("%d opción(es) por debajo del umbral. Enviando email...", len(alerts_to_send))
        notifier.send_alert_batch(results, alerts_to_send, threshold)
    else:
        logger.info(
            "El más barato es %s%s, por encima del umbral %s%s. Sin alerta.",
            sym, f"{results[0].price:.0f}", sym, f"{threshold:.0f}",
        )

    logger.info("=== Ciclo completo — %d resultados ===", len(results))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tenerife -> Miami Flight Price Tracker")
    parser.add_argument("--test-email", action="store_true", help="Envía email de prueba y sale.")
    parser.add_argument("--check-now", action="store_true", help="Ejecuta un check y sale.")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    args = parse_args()
    cfg = load_config()
    conn = init_db()
    notifier = Notifier(cfg)

    if args.test_email:
        logger.info("Enviando email de prueba a %s ...", cfg["alert_email"])
        ok = notifier.send_test_email()
        sys.exit(0 if ok else 1)

    if args.check_now:
        run_check(cfg, conn, notifier)
        sys.exit(0)

    interval_hours = float(cfg["check_interval_hours"])
    sym = "€" if cfg["currency"] == "EUR" else cfg["currency"]
    n_combos = (
        len(cfg["origin_airports"])
        * len(cfg["outbound_dates"])
        * max(len(cfg.get("return_dates", [])), 1)
    )
    logger.info(
        "Tracker iniciado | %d combinaciones/ciclo | umbral: %s%s | intervalo: %gh",
        n_combos, sym, cfg["price_threshold_usd"], interval_hours,
    )

    run_check(cfg, conn, notifier)

    schedule.every(int(interval_hours)).hours.do(run_check, cfg, conn, notifier)
    logger.info("Próximo check en %.1f hora(s). Tracker corriendo 24/7.", interval_hours)

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
