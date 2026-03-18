"""
main.py - Entry point for the Flight Price Tracker.

Responsibilities:
  - Initialise SQLite database for price history
  - Run an immediate check on startup
  - Schedule recurring checks at the configured interval
  - Trigger email alerts when the price drops below the threshold
  - Expose CLI flags: --test-email, --check-now
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
from tracker.flight import FlightResult, fetch_cheapest_flight
from tracker.notifier import Notifier

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("tracker.main")

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DB_PATH = Path(__file__).parent.parent / "data" / "prices.db"


def init_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS price_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            checked_at  TEXT    NOT NULL,
            origin      TEXT    NOT NULL,
            destination TEXT    NOT NULL,
            outbound_date TEXT  NOT NULL,
            return_date TEXT,
            airline     TEXT,
            price_usd   REAL    NOT NULL,
            duration    TEXT,
            stops       INTEGER,
            alerted     INTEGER DEFAULT 0
        )
        """
    )
    conn.commit()
    logger.info("Database ready at %s", DB_PATH)
    return conn


def record_price(conn: sqlite3.Connection, flight: FlightResult, alerted: bool) -> None:
    conn.execute(
        """
        INSERT INTO price_history
            (checked_at, origin, destination, outbound_date, return_date,
             airline, price_usd, duration, stops, alerted)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            flight.origin,
            flight.destination,
            flight.outbound_date,
            flight.return_date,
            flight.airline,
            flight.price_usd,
            flight.duration,
            flight.stops,
            1 if alerted else 0,
        ),
    )
    conn.commit()


def get_price_history_summary(conn: sqlite3.Connection) -> str:
    """Return a short string summarising recorded prices."""
    row = conn.execute(
        """
        SELECT COUNT(*), MIN(price_usd), MAX(price_usd), AVG(price_usd)
        FROM price_history
        """
    ).fetchone()
    if not row or row[0] == 0:
        return "No price history yet."
    count, low, high, avg = row
    return (
        f"History: {count} checks | "
        f"Lowest: ${low:.0f} | Highest: ${high:.0f} | Avg: ${avg:.0f}"
    )


# ---------------------------------------------------------------------------
# Core check logic
# ---------------------------------------------------------------------------
def run_check(cfg: dict, conn: sqlite3.Connection, notifier: Notifier) -> None:
    logger.info("--- Starting flight price check ---")
    logger.info(get_price_history_summary(conn))

    flight = fetch_cheapest_flight(
        origin=cfg["origin"],
        destination=cfg["destination"],
        outbound_date=cfg["outbound_date"],
        return_date=cfg["return_date"],
        adults=cfg["adults"],
        currency=cfg["currency"],
        serpapi_key=cfg["serpapi_key"],
    )

    if flight is None:
        logger.warning("No flight data returned this check. Will retry next interval.")
        return

    logger.info("Result: %s", flight)

    threshold = float(cfg["price_threshold_usd"])
    should_alert = flight.price_usd <= threshold

    record_price(conn, flight, alerted=should_alert)

    if should_alert:
        logger.info(
            "PRICE ALERT: $%.2f is at or below threshold $%.2f. Sending email...",
            flight.price_usd,
            threshold,
        )
        notifier.send_alert(flight, threshold)
    else:
        logger.info(
            "Price $%.2f is above threshold $%.2f. No alert sent.",
            flight.price_usd,
            threshold,
        )

    logger.info("--- Check complete ---")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Miami -> Tenerife Flight Price Tracker"
    )
    parser.add_argument(
        "--test-email",
        action="store_true",
        help="Send a test email and exit.",
    )
    parser.add_argument(
        "--check-now",
        action="store_true",
        help="Run a single price check and exit.",
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

    # -- One-shot modes -------------------------------------------------------
    if args.test_email:
        logger.info("Sending test email to %s ...", cfg["alert_email"])
        ok = notifier.send_test_email()
        sys.exit(0 if ok else 1)

    if args.check_now:
        run_check(cfg, conn, notifier)
        sys.exit(0)

    # -- Continuous monitoring -----------------------------------------------
    interval_hours = float(cfg["check_interval_hours"])
    logger.info(
        "Starting price tracker | %s -> %s | threshold: $%s | interval: %gh",
        cfg["origin"],
        cfg["destination"],
        cfg["price_threshold_usd"],
        interval_hours,
    )

    # Run once immediately on startup so we don't wait a full interval
    run_check(cfg, conn, notifier)

    # Schedule recurring checks
    schedule.every(interval_hours).hours.do(run_check, cfg, conn, notifier)
    logger.info("Next check in %.1f hour(s). Tracker is running 24/7.", interval_hours)

    try:
        while True:
            schedule.run_pending()
            time.sleep(60)  # wake up every minute to check the schedule
    except KeyboardInterrupt:
        logger.info("Tracker stopped by user.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
