"""
kiwi.py - Fetches flight prices from Kiwi.com (Tequila API).

Free tier: 500 calls/month.
Docs: https://tequila.kiwi.com/portal/docs/tequila_api/search_api
Sign up at: https://tequila.kiwi.com/

Set KIWI_API_KEY in your .env file to enable this source.
"""

import logging
from itertools import product
from typing import Optional
from urllib.parse import urlencode

import requests

from tracker.flight import FlightResult, Layover

logger = logging.getLogger(__name__)

KIWI_ENDPOINT = "https://api.tequila.kiwi.com/v2/search"
SOURCE_NAME = "Kiwi.com"


def _minutes_to_str(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h}h {m:02d}m"


def _parse_kiwi_result(itinerary: dict, origin: str, destination: str,
                       outbound_date: str, return_date: Optional[str]) -> Optional[FlightResult]:
    """
    Parses a single Kiwi itinerary dict into a FlightResult.
    """
    try:
        price = float(itinerary.get("price", 0))
        if price <= 0:
            return None

        # Duration in seconds -> minutes
        duration_secs = itinerary.get("duration", {})
        total_secs = duration_secs.get("total", 0) if isinstance(duration_secs, dict) else 0
        duration_str = _minutes_to_str(total_secs // 60) if total_secs else "N/A"

        # Route segments
        route = itinerary.get("route", [])
        # Derive airline from the first leg's operating carrier
        airline = route[0].get("airline", "Unknown") if route else "Unknown"

        # Layovers: any intermediate airport between legs
        stops = itinerary.get("transfers", 0)
        layovers: list[Layover] = []
        if stops > 0 and len(route) > 1:
            for leg in route[:-1]:
                arr_airport = leg.get("flyTo", "?")
                # Kiwi doesn't expose layover wait time directly; approximate from timestamps
                arr_ts = leg.get("aTimeUTC", 0)
                dep_next = route[route.index(leg) + 1].get("dTimeUTC", arr_ts)
                wait_minutes = max(0, (dep_next - arr_ts) // 60)
                layovers.append(Layover(airport=arr_airport, duration_minutes=wait_minutes))

        # Deep link to Kiwi booking page
        booking_link = itinerary.get("deep_link") or itinerary.get("booking_token")

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
            booking_token=booking_link,
            source=SOURCE_NAME,
            raw=itinerary,
        )
    except Exception as exc:
        logger.warning("Error parsing Kiwi itinerary: %s", exc)
        return None


def fetch_cheapest_kiwi(
    origin: str,
    destination: str,
    outbound_date: str,
    return_date: Optional[str],
    adults: int,
    currency: str,
    kiwi_api_key: str,
) -> Optional[FlightResult]:
    """
    Query Kiwi Tequila API for the cheapest flight for a given combination.
    Returns the cheapest FlightResult or None.
    """
    params = {
        "fly_from": origin,
        "fly_to": destination,
        "date_from": _fmt_kiwi_date(outbound_date),
        "date_to": _fmt_kiwi_date(outbound_date),
        "adults": adults,
        "curr": currency,
        "limit": 10,
        "sort": "price",
        "asc": 1,
        "partner_market": "es",
        "vehicle_type": "aircraft",
        # No checked bags, carry-on only
        "max_stopovers": 3,
    }

    if return_date:
        params["return_from"] = _fmt_kiwi_date(return_date)
        params["return_to"] = _fmt_kiwi_date(return_date)
        params["flight_type"] = "round"
    else:
        params["flight_type"] = "oneway"

    headers = {"apikey": kiwi_api_key}

    logger.info(
        "Kiwi: %s -> %s | salida %s%s",
        origin, destination, outbound_date,
        f" vuelta {return_date}" if return_date else "",
    )

    try:
        response = requests.get(KIWI_ENDPOINT, params=params, headers=headers, timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Kiwi request failed: %s", exc)
        return None

    data = response.json()
    itineraries = data.get("data", [])

    if not itineraries:
        logger.warning("Kiwi: sin resultados para %s -> %s el %s.", origin, destination, outbound_date)
        return None

    # Already sorted by price ascending from API, take the first valid one
    for itin in itineraries:
        result = _parse_kiwi_result(itin, origin, destination, outbound_date, return_date)
        if result:
            return result

    return None


def fetch_all_combinations_kiwi(
    origin_airports: list[str],
    destination: str,
    outbound_dates: list[str],
    return_dates: list[str],
    adults: int,
    currency: str,
    kiwi_api_key: str,
) -> list[FlightResult]:
    """
    Searches all combinations of origins x outbound_dates x return_dates via Kiwi.
    Returns results sorted by price ascending.
    """
    results = []
    return_list = return_dates if return_dates else [None]
    combos = list(product(origin_airports, outbound_dates, return_list))
    logger.info("Kiwi: lanzando %d combinaciones...", len(combos))

    for origin, outbound, return_date in combos:
        result = fetch_cheapest_kiwi(
            origin=origin,
            destination=destination,
            outbound_date=outbound,
            return_date=return_date,
            adults=adults,
            currency=currency,
            kiwi_api_key=kiwi_api_key,
        )
        if result is not None:
            results.append(result)

    results.sort(key=lambda r: r.price)
    return results


def _fmt_kiwi_date(date_str: str) -> str:
    """
    Convert YYYY-MM-DD to DD/MM/YYYY as required by Kiwi API.
    """
    parts = date_str.split("-")
    if len(parts) == 3:
        return f"{parts[2]}/{parts[1]}/{parts[0]}"
    return date_str
