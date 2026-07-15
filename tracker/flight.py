"""
flight.py - Fetches flight prices from Google Flights via SerpAPI.

SerpAPI docs: https://serpapi.com/google-flights-api
"""

import logging
from dataclasses import dataclass, field
from itertools import product
from typing import Optional
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

SERPAPI_ENDPOINT = "https://serpapi.com/search"

# Source identifier for results from this module
SOURCE_NAME = "Google Flights"


@dataclass
class Layover:
    airport: str
    duration_minutes: int

    @property
    def duration_str(self) -> str:
        h, m = divmod(self.duration_minutes, 60)
        return f"{h}h {m:02d}m"

    def __str__(self) -> str:
        return f"{self.airport} ({self.duration_str})"


@dataclass
class FlightResult:
    origin: str
    destination: str
    outbound_date: str
    return_date: Optional[str]
    price: float
    airline: str
    duration: str
    stops: int
    layovers: list[Layover] = field(default_factory=list)
    booking_token: Optional[str] = None
    source: str = SOURCE_NAME
    raw: Optional[dict] = None

    def is_direct(self) -> bool:
        return self.stops == 0

    def origin_name(self) -> str:
        return self.origin

    def destination_name(self) -> str:
        return self.destination

    def layovers_str(self) -> str:
        if not self.layovers:
            return "Directo"
        return " -> ".join(str(lv) for lv in self.layovers)

    def booking_url(self) -> Optional[str]:
        """
        Returns a direct deep-link to book this specific flight.
        - For Google Flights results: uses the SerpAPI booking_token redirect endpoint.
        - For other sources: the token already holds a full URL.
        """
        if not self.booking_token:
            return None
        if self.source == SOURCE_NAME:
            # SerpAPI provides a booking_token we can redirect through
            params = urlencode({"engine": "google_flights_booking", "token": self.booking_token})
            return f"https://serpapi.com/search.json?{params}"
        # Kiwi / Skyscanner store a full booking URL directly in booking_token
        return self.booking_token

    def __str__(self) -> str:
        trip = f"round-trip (return {self.return_date})" if self.return_date else "one-way"
        return (
            f"{self.airline} | {self.origin} -> {self.destination} ({self.destination_name()}) | "
            f"{self.outbound_date} | {trip} | {self.layovers_str()} | "
            f"{self.duration} | {self.price:.2f}"
        )


def _parse_layovers(raw_layovers: list[dict]) -> list[Layover]:
    result = []
    for lv in raw_layovers:
        airport = lv.get("name", lv.get("id", "?"))
        duration = lv.get("duration", 0)
        result.append(Layover(airport=airport, duration_minutes=duration))
    return result


def _extract_cheapest(flights: list[dict]) -> Optional[dict]:
    """
    Returns the cheapest flight group from a SerpAPI response list.
    Prefers fewest stops when price is equal.
    """
    candidates = []

    for group in flights:
        legs = group.get("flights", [])
        if not legs:
            continue
        price = group.get("price")
        if price is None:
            continue

        total_duration = group.get("total_duration", 0)
        h, m = divmod(total_duration, 60)
        duration_str = f"{h}h {m:02d}m" if total_duration else "N/A"

        raw_layovers = group.get("layovers", [])
        layovers = _parse_layovers(raw_layovers)

        first_leg = legs[0]
        airline = first_leg.get("airline", "Unknown")

        candidates.append({
            "price": float(price),
            "airline": airline,
            "duration": duration_str,
            "stops": len(layovers),
            "layovers": layovers,
            "token": group.get("booking_token"),
            "raw": group,
        })

    if not candidates:
        return None

    candidates.sort(key=lambda x: (x["price"], x["stops"]))
    return candidates[0]


def fetch_cheapest_flight(
    origin: str,
    destination: str,
    outbound_date: str,
    return_date: Optional[str],
    adults: int,
    currency: str,
    serpapi_key: str,
    carry_on_bags: int = 0,
    checked_bags: int = 0,
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
        "show_hidden": True,
        "carry_on_bags": carry_on_bags,
        "checked_bags": checked_bags,
    }

    if return_date:
        params["return_date"] = return_date
        params["type"] = "1"  # round trip
    else:
        params["type"] = "2"  # one-way

    logger.info(
        "SerpAPI: %s -> %s | salida %s%s | mano=%d facturado=%d",
        origin, destination, outbound_date,
        f" vuelta {return_date}" if return_date else "",
        carry_on_bags, checked_bags,
    )

    try:
        response = requests.get(SERPAPI_ENDPOINT, params=params, timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.error("SerpAPI request failed: %s", exc)
        return None

    data = response.json()

    if "error" in data:
        logger.error("SerpAPI error: %s", data["error"])
        return None

    all_flights = data.get("best_flights", []) + data.get("other_flights", [])

    if not all_flights:
        logger.warning("Sin resultados para %s -> %s el %s.", origin, destination, outbound_date)
        return None

    best = _extract_cheapest(all_flights)
    if best is None:
        return None

    return FlightResult(
        origin=origin,
        destination=destination,
        outbound_date=outbound_date,
        return_date=return_date,
        price=best["price"],
        airline=best["airline"],
        duration=best["duration"],
        stops=best["stops"],
        layovers=best["layovers"],
        booking_token=best["token"],
        source=SOURCE_NAME,
        raw=best["raw"],
    )


def fetch_all_combinations(
    origin_airports: list[str],
    destination_airports: list[str],
    outbound_dates: list[str],
    return_dates: list[str],
    adults: int,
    currency: str,
    serpapi_key: str,
    carry_on_bags: int = 0,
    checked_bags: int = 0,
) -> list[FlightResult]:
    """
    Searches every configured combination of origins, destinations, outbound
    dates, and return dates. Returns results sorted by price ascending.
    """
    results = []
    return_list = return_dates if return_dates else [None]
    combos = list(product(origin_airports, destination_airports, outbound_dates, return_list))
    logger.info("Lanzando %d combinaciones de búsqueda...", len(combos))

    for origin, destination, outbound, return_date in combos:
        result = fetch_cheapest_flight(
            origin=origin,
            destination=destination,
            outbound_date=outbound,
            return_date=return_date,
            adults=adults,
            currency=currency,
            serpapi_key=serpapi_key,
            carry_on_bags=carry_on_bags,
            checked_bags=checked_bags,
        )
        if result is not None:
            results.append(result)

    results.sort(key=lambda r: r.price)
    return results
