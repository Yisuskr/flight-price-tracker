"""
aggregator.py - Merges flight results from all enabled sources.

Deduplication strategy: for the same (origin, outbound_date, return_date, airline)
combination, keep only the cheapest result across all sources.
Final list is sorted by price ascending.
"""

import logging
from typing import Optional

from tracker.flight import FlightResult, fetch_all_combinations
from tracker.sources.kiwi import fetch_all_combinations_kiwi
from tracker.sources.skyscanner import fetch_all_combinations_skyscanner

logger = logging.getLogger(__name__)


def _dedup(results: list[FlightResult]) -> list[FlightResult]:
    """
    Deduplicates results by (origin, outbound_date, return_date, airline).
    Keeps the cheapest option when duplicates exist.
    """
    best: dict[tuple, FlightResult] = {}
    for r in results:
        key = (r.origin, r.outbound_date, r.return_date or "", r.airline.lower())
        if key not in best or r.price < best[key].price:
            best[key] = r
    return sorted(best.values(), key=lambda r: r.price)


def fetch_all_sources(cfg: dict) -> list[FlightResult]:
    """
    Queries all configured/enabled sources and returns a deduplicated,
    price-sorted list of FlightResult objects.

    Sources enabled:
    - Google Flights (SerpAPI): always enabled if SERPAPI_KEY is set
    - Kiwi.com (Tequila): enabled if KIWI_API_KEY is set in cfg
    - Skyscanner (RapidAPI): enabled if RAPIDAPI_KEY is set in cfg

    Each source is fetched independently; errors in one source do not
    prevent results from others.
    """
    kwargs = dict(
        origin_airports=cfg["origin_airports"],
        destination=cfg["destination"],
        outbound_dates=cfg["outbound_dates"],
        return_dates=cfg.get("return_dates", []),
        adults=cfg["adults"],
        currency=cfg["currency"],
    )

    all_results: list[FlightResult] = []

    # ── Google Flights via SerpAPI ─────────────────────────────────────────
    serpapi_key = cfg.get("serpapi_key")
    if serpapi_key:
        logger.info("[Aggregator] Consultando Google Flights (SerpAPI)...")
        try:
            google_results = fetch_all_combinations(
                **kwargs,
                serpapi_key=serpapi_key,
                carry_on_bags=cfg.get("carry_on_bags", 0),
                checked_bags=cfg.get("checked_bags", 0),
            )
            logger.info("[Aggregator] Google Flights: %d resultados", len(google_results))
            all_results.extend(google_results)
        except Exception as exc:
            logger.error("[Aggregator] Google Flights falló: %s", exc)
    else:
        logger.warning("[Aggregator] SERPAPI_KEY no configurado — saltando Google Flights.")

    # ── Kiwi.com (Tequila API) ─────────────────────────────────────────────
    kiwi_key = cfg.get("kiwi_api_key")
    if kiwi_key:
        logger.info("[Aggregator] Consultando Kiwi.com...")
        try:
            kiwi_results = fetch_all_combinations_kiwi(
                **kwargs,
                kiwi_api_key=kiwi_key,
            )
            logger.info("[Aggregator] Kiwi.com: %d resultados", len(kiwi_results))
            all_results.extend(kiwi_results)
        except Exception as exc:
            logger.error("[Aggregator] Kiwi.com falló: %s", exc)
    else:
        logger.info("[Aggregator] KIWI_API_KEY no configurado — saltando Kiwi.com.")

    # ── Skyscanner via RapidAPI ────────────────────────────────────────────
    rapidapi_key = cfg.get("rapidapi_key")
    if rapidapi_key:
        logger.info("[Aggregator] Consultando Skyscanner (RapidAPI)...")
        try:
            sky_results = fetch_all_combinations_skyscanner(
                **kwargs,
                rapidapi_key=rapidapi_key,
            )
            logger.info("[Aggregator] Skyscanner: %d resultados", len(sky_results))
            all_results.extend(sky_results)
        except Exception as exc:
            logger.error("[Aggregator] Skyscanner falló: %s", exc)
    else:
        logger.info("[Aggregator] RAPIDAPI_KEY no configurado — saltando Skyscanner.")

    if not all_results:
        logger.warning("[Aggregator] Ninguna fuente devolvió resultados.")
        return []

    deduped = _dedup(all_results)
    logger.info(
        "[Aggregator] Total: %d resultados brutos -> %d tras deduplicar.",
        len(all_results), len(deduped),
    )
    return deduped
