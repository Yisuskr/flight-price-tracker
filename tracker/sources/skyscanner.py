"""
skyscanner.py - Fetches flight prices from Skyscanner via RapidAPI.

Free tier: ~50-100 calls/month depending on plan.
RapidAPI hub: https://rapidapi.com/3b-data-3b-data-default/api/skyscanner44

Set RAPIDAPI_KEY in your .env file to enable this source.
"""

import logging
from itertools import product
from typing import Optional

import requests

from tracker.flight import FlightResult, Layover

logger = logging.getLogger(__name__)

RAPIDAPI_HOST = "skyscanner44.p.rapidapi.com"
RAPIDAPI_SEARCH_ENDPOINT = f"https://{RAPIDAPI_HOST}/search-extended"
SOURCE_NAME = "Skyscanner"

# Skyscanner uses IATA codes but some airports need locale mapping
_LOCALE = "es-ES"
_MARKET = "ES"
_COUNTRY = "ES"


def _minutes_to_str(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h}h {m:02d}m"


def _parse_leg(leg: dict) -> tuple[str, int, list[Layover]]:
    """
    Extracts (duration_str, stops, layovers) from a Skyscanner leg object.
    """
    duration_minutes = leg.get("durationInMinutes", 0)
    duration_str = _minutes_to_str(duration_minutes) if duration_minutes else "N/A"

    stop_count = leg.get("stopCount", 0)
    layovers: list[Layover] = []

    segments = leg.get("segments", [])
    if stop_count > 0 and len(segments) > 1:
        for seg in segments[:-1]:
            arr_iata = seg.get("destination", {}).get("flightPlaceId", "?")
            # Approximate wait from segment durations (no explicit layover time in this API)
            layovers.append(Layover(airport=arr_iata, duration_minutes=0))

    return duration_str, stop_count, layovers


def _parse_skyscanner_itinerary(
    itinerary: dict,
    legs_map: dict,
    origin: str,
    destination: str,
    outbound_date: str,
    return_date: Optional[str],
) -> Optional[FlightResult]:
    """
    Parses a Skyscanner itinerary (price + leg IDs) into a FlightResult.
    legs_map: dict mapping legId -> leg object (from top-level "legs" array).
    """
    try:
        price_info = itinerary.get("price", {})
        raw_price = price_info.get("raw", 0)
        if not raw_price:
            return None
        price = float(raw_price)

        leg_ids = itinerary.get("legIds", [])
        if not leg_ids:
            return None

        outbound_leg = legs_map.get(leg_ids[0], {})
        duration_str, stops, layovers = _parse_leg(outbound_leg)

        # Carrier name from first segment of outbound leg
        segments = outbound_leg.get("segments", [])
        airline = "Unknown"
        if segments:
            carrier = segments[0].get("marketingCarrier", {})
            airline = carrier.get("name", "Unknown")

        # Booking URL: Skyscanner deeplinks are not always available in free tier;
        # fall back to a Skyscanner search URL
        booking_url = itinerary.get("deeplink") or _build_skyscanner_url(
            origin, destination, outbound_date, return_date
        )

        return FlightResult(
            origin=origin,
            destination=destination,
            outbound_date=outbound_date,
            return_date=return_date,
            price=price,
            airline=airline,
            duration=duration_str,
            stops=stops,
            layovers=layovers,
            booking_token=booking_url,
            source=SOURCE_NAME,
            raw=itinerary,
        )
    except Exception as exc:
        logger.warning("Error parsing Skyscanner itinerary: %s", exc)
        return None


def _build_skyscanner_url(
    origin: str,
    destination: str,
    outbound_date: str,
    return_date: Optional[str],
) -> str:
    """Fallback Skyscanner search URL (no deeplink available)."""
    out = outbound_date.replace("-", "")[2:]  # YYMMDD
    base = f"https://www.skyscanner.es/transporte/vuelos/{origin.lower()}/{destination.lower()}/{out}"
    if return_date:
        ret = return_date.replace("-", "")[2:]
        return f"{base}/{ret}/"
    return f"{base}/"


def fetch_cheapest_skyscanner(
    origin: str,
    destination: str,
    outbound_date: str,
    return_date: Optional[str],
    adults: int,
    currency: str,
    rapidapi_key: str,
) -> Optional[FlightResult]:
    """
    Query Skyscanner via RapidAPI for the cheapest flight for one combination.
    Returns the cheapest FlightResult or None.
    """
    headers = {
        "X-RapidAPI-Key": rapidapi_key,
        "X-RapidAPI-Host": RAPIDAPI_HOST,
    }

    trip_type = "round" if return_date else "one-way"
    params = {
        "adults": adults,
        "origin": origin,
        "destination": destination,
        "departureDate": outbound_date,
        "currency": currency,
        "market": _MARKET,
        "locale": _LOCALE,
        "cabinClass": "economy",
    }
    if return_date:
        params["returnDate"] = return_date

    logger.info(
        "Skyscanner: %s -> %s | salida %s%s",
        origin, destination, outbound_date,
        f" vuelta {return_date}" if return_date else "",
    )

    try:
        response = requests.get(
            RAPIDAPI_SEARCH_ENDPOINT, params=params, headers=headers, timeout=30
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Skyscanner/RapidAPI request failed: %s", exc)
        return None

    data = response.json()

    # Build legs map for quick lookup
    legs_list = data.get("legs", [])
    legs_map = {leg["id"]: leg for leg in legs_list if "id" in leg}

    itineraries = data.get("itineraries", [])
    if not itineraries:
        logger.warning(
            "Skyscanner: sin resultados para %s -> %s el %s.", origin, destination, outbound_date
        )
        return None

    # Sort by price ascending, pick cheapest valid
    itineraries_sorted = sorted(
        itineraries,
        key=lambda it: it.get("price", {}).get("raw", float("inf")),
    )

    for itin in itineraries_sorted:
        result = _parse_skyscanner_itinerary(
            itin, legs_map, origin, destination, outbound_date, return_date
        )
        if result:
            return result

    return None


def fetch_all_combinations_skyscanner(
    origin_airports: list[str],
    destination: str,
    outbound_dates: list[str],
    return_dates: list[str],
    adults: int,
    currency: str,
    rapidapi_key: str,
) -> list[FlightResult]:
    """
    Searches all combinations of origins x outbound_dates x return_dates via Skyscanner.
    Returns results sorted by price ascending.
    """
    results = []
    return_list = return_dates if return_dates else [None]
    combos = list(product(origin_airports, outbound_dates, return_list))
    logger.info("Skyscanner: lanzando %d combinaciones...", len(combos))

    for origin, outbound, return_date in combos:
        result = fetch_cheapest_skyscanner(
            origin=origin,
            destination=destination,
            outbound_date=outbound,
            return_date=return_date,
            adults=adults,
            currency=currency,
            rapidapi_key=rapidapi_key,
        )
        if result is not None:
            results.append(result)

    results.sort(key=lambda r: r.price)
    return results
