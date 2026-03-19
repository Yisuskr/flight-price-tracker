"""
aggregator.py - Per-source fetch functions + result merging utilities.

Each source has its own fetch function so main.py can schedule them
independently at different intervals:
  - Google Flights (SerpAPI): every 12h
  - Kiwi.com:                 every 60h  (~96 calls/month, within 500 free)
  - Skyscanner (RapidAPI):    every 72h  (~80 calls/month, within 100 free)

Deduplication: for the same (origin, outbound_date, return_date, airline)
keep only the cheapest result across all sources.
"""

import logging

from tracker.flight import FlightResult, fetch_all_combinations
from tracker.sources.kiwi import fetch_all_combinations_kiwi
from tracker.sources.skyscanner import fetch_all_combinations_skyscanner

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared result cache — each source writes its latest results here.
# main.py reads this cache to build the merged email after every fetch.
# ---------------------------------------------------------------------------
_latest: dict[str, list[FlightResult]] = {
    "google": [],
    "kiwi": [],
    "skyscanner": [],
}


def _base_kwargs(cfg: dict) -> dict:
    return dict(
        origin_airports=cfg["origin_airports"],
        destination=cfg["destination"],
        outbound_dates=cfg["outbound_dates"],
        return_dates=cfg.get("return_dates", []),
        adults=cfg["adults"],
        currency=cfg["currency"],
    )


def _dedup(results: list[FlightResult]) -> list[FlightResult]:
    """
    Deduplicates by (origin, outbound_date, return_date, airline).
    Keeps cheapest per unique combo, sorted by price ascending.
    """
    best: dict[tuple, FlightResult] = {}
    for r in results:
        key = (r.origin, r.outbound_date, r.return_date or "", r.airline.lower())
        if key not in best or r.price < best[key].price:
            best[key] = r
    return sorted(best.values(), key=lambda r: r.price)


# ---------------------------------------------------------------------------
# Per-source fetch functions (called individually by the scheduler)
# ---------------------------------------------------------------------------

def fetch_google(cfg: dict) -> list[FlightResult]:
    """Fetch from Google Flights via SerpAPI and update the cache."""
    serpapi_key = cfg.get("serpapi_key")
    if not serpapi_key:
        logger.warning("[Google] SERPAPI_KEY no configurado.")
        return []
    try:
        results = fetch_all_combinations(
            **_base_kwargs(cfg),
            serpapi_key=serpapi_key,
            carry_on_bags=cfg.get("carry_on_bags", 0),
            checked_bags=cfg.get("checked_bags", 0),
        )
        logger.info("[Google] %d resultados obtenidos.", len(results))
        _latest["google"] = results
        return results
    except Exception as exc:
        logger.error("[Google] Error: %s", exc)
        return []


def fetch_kiwi(cfg: dict) -> list[FlightResult]:
    """Fetch from Kiwi.com and update the cache."""
    kiwi_key = cfg.get("kiwi_api_key")
    if not kiwi_key:
        logger.info("[Kiwi] KIWI_API_KEY no configurado — saltando.")
        return []
    try:
        results = fetch_all_combinations_kiwi(
            **_base_kwargs(cfg),
            kiwi_api_key=kiwi_key,
        )
        logger.info("[Kiwi] %d resultados obtenidos.", len(results))
        _latest["kiwi"] = results
        return results
    except Exception as exc:
        logger.error("[Kiwi] Error: %s", exc)
        return []


def fetch_skyscanner(cfg: dict) -> list[FlightResult]:
    """Fetch from Skyscanner via RapidAPI and update the cache."""
    rapidapi_key = cfg.get("rapidapi_key")
    if not rapidapi_key:
        logger.info("[Skyscanner] RAPIDAPI_KEY no configurado — saltando.")
        return []
    try:
        results = fetch_all_combinations_skyscanner(
            **_base_kwargs(cfg),
            rapidapi_key=rapidapi_key,
        )
        logger.info("[Skyscanner] %d resultados obtenidos.", len(results))
        _latest["skyscanner"] = results
        return results
    except Exception as exc:
        logger.error("[Skyscanner] Error: %s", exc)
        return []


def get_merged_results() -> list[FlightResult]:
    """
    Returns the current deduplicated merge of all cached source results.
    Called after any source fetch to build the alert email.
    """
    all_results = _latest["google"] + _latest["kiwi"] + _latest["skyscanner"]
    if not all_results:
        return []
    deduped = _dedup(all_results)
    logger.info(
        "[Aggregator] Cache: google=%d kiwi=%d skyscanner=%d → %d tras deduplicar.",
        len(_latest["google"]), len(_latest["kiwi"]),
        len(_latest["skyscanner"]), len(deduped),
    )
    return deduped
