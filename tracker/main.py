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
    fetch_aviasales,
    fetch_google,
    fetch_kiwi,
    fetch_skyscanner,
    get_merged_results,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("tracker.main")


def _resolve_db_path() -> str:
    candidates = [
        Path(__file__).parent.parent / "data" / "prices.db",
        Path("/tmp/prices.db"),
    ]
    for path in candidates:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
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
    logger.info("Database ready at %s", db_path)
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
        return "No price history yet."
    count, low, high, avg = row
    sym = "EUR " if currency == "EUR" else f"{currency} "
    return (
        f"History: {count} checks | "
        f"Low: {sym}{low:.0f} | High: {sym}{high:.0f} | Average: {sym}{avg:.0f}"
    )


def evaluate_and_alert(
    cfg: dict,
    conn: sqlite3.Connection,
    notifier: Notifier,
    source_label: str,
) -> None:
    currency = cfg["currency"]
    sym = "EUR " if currency == "EUR" else f"{currency} "
    threshold = float(cfg["price_threshold"])

    results = get_merged_results()
    if not results:
        logger.warning("[%s] No merged results available.", source_label)
        return

    alerts_to_send = []
    for flight in results:
        should_alert = flight.price <= threshold
        record_price(conn, flight, currency, alerted=should_alert)
        if should_alert:
            alerts_to_send.append(flight)
        logger.info(
            "[%s] %s->%s | out %s ret %s | %s%.0f | %s | %s | %s",
            "ALERT" if should_alert else "     ",
            flight.origin,
            flight.destination,
            flight.outbound_date,
            flight.return_date or "-",
            sym,
            flight.price,
            flight.airline,
            flight.layovers_str(),
            getattr(flight, "source", ""),
        )

    if alerts_to_send:
        logger.info("%d result(s) under threshold. Sending email.", len(alerts_to_send))
        notifier.send_alert_batch(results, alerts_to_send, threshold)
    elif cfg.get("send_summary_when_no_alert", True):
        logger.info(
            "Best result is %s%.0f, above threshold %s%.0f. Sending summary.",
            sym,
            results[0].price,
            sym,
            threshold,
        )
        notifier.send_alert_batch(results, [], threshold)
    else:
        logger.info(
            "Best result is %s%.0f, above threshold %s%.0f. No email sent.",
            sym,
            results[0].price,
            sym,
            threshold,
        )

    logger.info("[%s] Cycle complete with %d merged result(s).", source_label, len(results))


def run_google(cfg: dict, conn: sqlite3.Connection, notifier: Notifier) -> None:
    if not cfg.get("serpapi_key"):
        logger.info("[Google] Disabled because SERPAPI_KEY is not configured.")
        return
    logger.info("[Google] Starting fetch.")
    logger.info(get_history_summary(conn, cfg["currency"]))
    fetch_google(cfg)
    evaluate_and_alert(cfg, conn, notifier, "Google")


def run_kiwi(cfg: dict, conn: sqlite3.Connection, notifier: Notifier) -> None:
    if not cfg.get("kiwi_api_key"):
        logger.info("[Kiwi] Disabled because KIWI_API_KEY is not configured.")
        return
    logger.info("[Kiwi] Starting fetch.")
    fetch_kiwi(cfg)
    evaluate_and_alert(cfg, conn, notifier, "Kiwi")


def run_skyscanner(cfg: dict, conn: sqlite3.Connection, notifier: Notifier) -> None:
    if not cfg.get("rapidapi_key"):
        logger.info("[Skyscanner] Disabled because RAPIDAPI_KEY is not configured.")
        return
    logger.info("[Skyscanner] Starting fetch.")
    fetch_skyscanner(cfg)
    evaluate_and_alert(cfg, conn, notifier, "Skyscanner")


def run_aviasales(cfg: dict, conn: sqlite3.Connection, notifier: Notifier) -> None:
    if not cfg.get("aviasales_token"):
        logger.info("[Aviasales] Disabled because AVIASALES_TOKEN is not configured.")
        return
    logger.info("[Aviasales] Starting fetch.")
    fetch_aviasales(cfg)
    evaluate_and_alert(cfg, conn, notifier, "Aviasales")


SOURCE_RUNNERS = {
    "google": run_google,
    "kiwi": run_kiwi,
    "skyscanner": run_skyscanner,
    "aviasales": run_aviasales,
}


def _run_source(name: str, cfg: dict, conn: sqlite3.Connection, notifier: Notifier) -> None:
    runner = SOURCE_RUNNERS.get(name)
    if runner is None:
        logger.warning("Unknown source in config.yaml: %s", name)
        return
    runner(cfg, conn, notifier)


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):  # noqa: A002
        pass


def _start_health_server() -> None:
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Health server listening on port %d", port)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generic flight price tracker")
    parser.add_argument("--test-email", action="store_true", help="Send a test email and exit.")
    parser.add_argument(
        "--check-now",
        action="store_true",
        help="Run all configured sources immediately and exit.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config()
    conn = init_db()
    notifier = Notifier(cfg)

    _start_health_server()

    if args.test_email:
        logger.info("Sending test email to %s.", cfg["alert_email"])
        ok = notifier.send_test_email()
        sys.exit(0 if ok else 1)

    if args.check_now:
        logger.info("--check-now: running all sources once.")
        for source in SOURCE_RUNNERS:
            _run_source(source, cfg, conn, notifier)
        sys.exit(0)

    google_h = int(cfg.get("google_interval_hours", 12))
    kiwi_h = int(cfg.get("kiwi_interval_hours", 60))
    sky_h = int(cfg.get("skyscanner_interval_hours", 72))
    avia_h = int(cfg.get("aviasales_interval_hours", 24))

    sym = "EUR " if cfg["currency"] == "EUR" else f"{cfg['currency']} "
    n_combos = (
        len(cfg["origin_airports"])
        * len(cfg["destination_airports"])
        * len(cfg["outbound_dates"])
        * max(len(cfg.get("return_dates", [])), 1)
    )

    logger.info(
        "Tracker started | %d combo(s) per source | threshold: %s%s",
        n_combos,
        sym,
        cfg["price_threshold"],
    )
    logger.info(
        "Intervals | Google: %dh | Kiwi: %dh | Skyscanner: %dh | Aviasales: %dh",
        google_h,
        kiwi_h,
        sky_h,
        avia_h,
    )

    for source in cfg.get("initial_sources", []):
        _run_source(source, cfg, conn, notifier)

    schedule.every(google_h).hours.do(run_google, cfg, conn, notifier)
    schedule.every(kiwi_h).hours.do(run_kiwi, cfg, conn, notifier)
    schedule.every(sky_h).hours.do(run_skyscanner, cfg, conn, notifier)
    schedule.every(avia_h).hours.do(run_aviasales, cfg, conn, notifier)
    logger.info("Checks scheduled using hour intervals from config.yaml.")

    try:
        while True:
            schedule.run_pending()
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Tracker stopped by user.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
