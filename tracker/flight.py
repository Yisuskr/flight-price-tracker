"""
flight.py - Fetches flight prices from Google Flights via SerpAPI.

SerpAPI docs: https://serpapi.com/google-flights-api
"""

import logging
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger(__name__)

SERPAPI_ENDPOINT = "https://serpapi.com/search"

# Google Flights uses specific airport codes. TFS = Tenerife South (Reina Sofia)
# TFN = Tenerife North (Los Rodeos) — we search TFS by default but support both.


@dataclass
class FlightResult:
    origin: str
    destination: str
    outbound_date: str
    return_date: Optional[str]
    price_usd: float
    airline: str
    duration: str
    stops: int
    booking_token: Optional[str] = None
    raw: Optional[dict] = None

    def is_direct(self) -> bool:
        return self.stops == 0

    def __str__(self) -> str:
        trip_type = f"round-trip (return {self.return_date})" if self.return_date else "one-way"
        stops_str = "direct" if self.is_direct() else f"{self.stops} stop(s)"
        return (
            f"{self.airline} | {self.origin} -> {self.destination} | "
            f"{self.outbound_date} | {trip_type} | {stops_str} | "
            f"{self.duration} | ${self.price_usd:.2f}"
        )


def _parse_duration(duration_str: str) -> str:
    """Normalise the duration string returned by SerpAPI."""
    return duration_str.strip() if duration_str else "N/A"


def _extract_cheapest(flights: list[dict], currency: str) -> Optional[dict]:
    """
    Walk through the SerpAPI best_flights / other_flights list and return the
    cheapest option, preferring direct flights when the price difference is
    less than 10%.
    """
    candidates = []

    for group in flights:
        # Each group has a list of individual flight legs under 'flights'
        legs = group.get("flights", [])
        if not legs:
            continue

        price = group.get("price")
        if price is None:
            continue

        total_duration = group.get("total_duration", 0)
        hours, mins = divmod(total_duration, 60)
        duration_str = f"{hours}h {mins}m" if total_duration else "N/A"

        # Count layovers
        layovers = group.get("layovers", [])
        stops = len(layovers)

        # Airline is the operating carrier of the first leg
        first_leg = legs[0]
        airline = first_leg.get("airline", "Unknown")

        candidates.append({
            "price": float(price),
            "airline": airline,
            "duration": duration_str,
            "stops": stops,
            "token": group.get("booking_token"),
            "raw": group,
        })

    if not candidates:
        return None

    # Sort by price, then prefer direct
    candidates.sort(key=lambda x: (x["price"], x["stops"]))
    best = candidates[0]

    return best  # caller builds FlightResult


def fetch_cheapest_flight(
    origin: str,
    destination: str,
    outbound_date: str,
    return_date: Optional[str],
    adults: int,
    currency: str,
    serpapi_key: str,
) -> Optional[FlightResult]:
    """
    Query SerpAPI for the cheapest flight between origin and destination.

    Returns a FlightResult or None if no flights are found / API error.
    """
    params = {
        "engine": "google_flights",
        "departure_id": origin,
        "arrival_id": destination,
        "outbound_date": outbound_date,
        "currency": currency,
        "hl": "en",
        "adults": adults,
        "api_key": serpapi_key,
        # Show all results (not just best picks) for a wider price scan
        "show_hidden": True,
    }

    # Round-trip vs one-way
    if return_date:
        params["return_date"] = return_date
        params["type"] = "1"  # 1 = round trip
    else:
        params["type"] = "2"  # 2 = one-way

    logger.info(
        "Querying SerpAPI: %s -> %s on %s%s",
        origin,
        destination,
        outbound_date,
        f" / return {return_date}" if return_date else "",
    )

    try:
        response = requests.get(SERPAPI_ENDPOINT, params=params, timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.error("SerpAPI request failed: %s", exc)
        return None

    data = response.json()

    # SerpAPI returns an 'error' key on quota exhaustion or bad keys
    if "error" in data:
        logger.error("SerpAPI error: %s", data["error"])
        return None

    # Combine best_flights and other_flights for a full picture
    all_flights = data.get("best_flights", []) + data.get("other_flights", [])

    if not all_flights:
        logger.warning("No flights returned by SerpAPI for this query.")
        return None

    best = _extract_cheapest(all_flights, currency)
    if best is None:
        return None

    return FlightResult(
        origin=origin,
        destination=destination,
        outbound_date=outbound_date,
        return_date=return_date,
        price_usd=best["price"],
        airline=best["airline"],
        duration=best["duration"],
        stops=best["stops"],
        booking_token=best["token"],
        raw=best["raw"],
    )
