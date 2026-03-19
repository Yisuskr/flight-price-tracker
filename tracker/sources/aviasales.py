"""
aviasales.py - Fetches flight prices from Aviasales via the Travelpayouts API.

Endpoint: GET https://api.travelpayouts.com/aviasales/v3/prices_for_dates
Docs: https://support.travelpayouts.com/hc/en-us/articles/360027634791

Free tier: no declared call limit (~1000 req/hour).
Note: returns cached data from the last ~48h of real user searches.
      For low-traffic routes (e.g. TFS→MIA) the cache may occasionally be empty.

Set AVIASALES_TOKEN in your .env to enable this source.
Get your token at: https://app.travelpayouts.com/profile/api-token
"""

import logging
from itertools import product
from typing import Optional

import requests

from tracker.flight import FlightResult, Layover

logger = logging.getLogger(__name__)

AVIASALES_ENDPOINT = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
SOURCE_NAME = "Aviasales"
BOOKING_BASE_URL = "https://www.aviasales.com"


def _minutes_to_str(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h}h {m:02d}m"


def _parse_ticket(
    ticket: dict,
    origin: str,
    destination: str,
    outbound_date: str,
    return_date: Optional[str],
) -> Optional[FlightResult]:
    """
    Parses a single Aviasales ticket dict into a FlightResult.

    Relevant ticket fields:
      price           int     — price in requested currency
      airline         str     — operating carrier IATA code
      flight_number   str     — e.g. "IB3456"
      departure_at    str     — ISO datetime of departure
      return_at       str     — ISO datetime of return leg (if round trip)
      transfers       int     — number of stops on outbound leg
      return_transfers int    — number of stops on return leg
      duration        int     — total outbound duration in minutes
      duration_to     int     — outbound duration in minutes (alias)
      duration_back   int     — return leg duration in minutes
      link            str     — relative URL, prepend BOOKING_BASE_URL
    """
    try:
        raw_price = ticket.get("price")
        if raw_price is None:
            return None
        price = float(raw_price)

        airline_code = ticket.get("airline", "Unknown")
        # Prefer full name if available, fall back to IATA code
        airline = airline_code or "Unknown"

        # Duration — Aviasales uses "duration" (total) or "duration_to" (outbound)
        duration_minutes = ticket.get("duration_to") or ticket.get("duration") or 0
        duration_str = _minutes_to_str(duration_minutes) if duration_minutes else "N/A"

        stops = int(ticket.get("transfers", 0))

        # Layovers: the API doesn't give intermediate airport codes for free,
        # so we record stop count only.
        layovers: list[Layover] = []

        # Booking URL
        relative_link = ticket.get("link", "")
        booking_url = (
            f"{BOOKING_BASE_URL}{relative_link}" if relative_link else ""
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
            raw=ticket,
        )
    except Exception as exc:
        logger.warning("[Aviasales] Error parsing ticket: %s", exc)
        return None


def fetch_cheapest_aviasales(
    origin: str,
    destination: str,
    outbound_date: str,
    return_date: Optional[str],
    adults: int,
    currency: str,
    token: str,
    limit: int = 10,
) -> list[FlightResult]:
    """
    Query Travelpayouts for the cheapest flights for one origin/date combination.
    Returns a list of FlightResult (up to `limit` results), sorted by price.

    The API returns cached prices from the last ~48h of real Aviasales searches.
    An empty response is normal for low-traffic routes — not an error.
    """
    params: dict = {
        "origin": origin,
        "destination": destination,
        "departure_at": outbound_date,
        "currency": currency,
        "market": "es",          # Spain-based price cache
        "one_way": "false",
        "sorting": "price",
        "limit": limit,
        "token": token,
    }
    if return_date:
        params["return_at"] = return_date
    else:
        params["one_way"] = "true"

    logger.info(
        "[Aviasales] %s -> %s | salida %s%s",
        origin, destination, outbound_date,
        f" | vuelta {return_date}" if return_date else "",
    )

    try:
        response = requests.get(AVIASALES_ENDPOINT, params=params, timeout=20)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.error("[Aviasales] Request failed: %s", exc)
        return []

    data = response.json()

    # Response shape: {"success": true, "data": [...], "currency": "EUR"}
    if not data.get("success", True):
        logger.warning("[Aviasales] API returned success=false: %s", data)
        return []

    tickets = data.get("data", [])
    if not tickets:
        logger.info(
            "[Aviasales] Sin resultados en caché para %s -> %s el %s.",
            origin, destination, outbound_date,
        )
        return []

    results: list[FlightResult] = []
    for ticket in tickets:
        parsed = _parse_ticket(ticket, origin, destination, outbound_date, return_date)
        if parsed is not None:
            results.append(parsed)

    results.sort(key=lambda r: r.price)
    logger.info(
        "[Aviasales] %d resultado(s) para %s -> %s el %s.",
        len(results), origin, destination, outbound_date,
    )
    return results


def fetch_all_combinations_aviasales(
    origin_airports: list[str],
    destination: str,
    outbound_dates: list[str],
    return_dates: list[str],
    adults: int,
    currency: str,
    token: str,
) -> list[FlightResult]:
    """
    Searches all combinations of origins x outbound_dates x return_dates
    via the Aviasales/Travelpayouts API.
    Returns all results sorted by price ascending.
    """
    return_list = return_dates if return_dates else [None]
    combos = list(product(origin_airports, outbound_dates, return_list))
    logger.info("[Aviasales] Lanzando %d combinaciones...", len(combos))

    all_results: list[FlightResult] = []
    for origin, outbound, return_date in combos:
        results = fetch_cheapest_aviasales(
            origin=origin,
            destination=destination,
            outbound_date=outbound,
            return_date=return_date,
            adults=adults,
            currency=currency,
            token=token,
        )
        all_results.extend(results)

    all_results.sort(key=lambda r: r.price)
    logger.info("[Aviasales] Total: %d resultado(s) en todas las combinaciones.", len(all_results))
    return all_results
