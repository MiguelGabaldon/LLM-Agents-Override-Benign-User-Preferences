"""Deterministic simulated transport tools (search and booking)."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from random import Random
from secrets import randbits
from typing import Any

from .schemas import BookingAttempt, BookingStatus, EnvironmentState, Scenario


class TransportEnvironment:
    """Stateful local environment for exactly one validated transport scenario."""

    def __init__(self, scenario: Scenario, *, option_order_seed: int | None = None) -> None:
        self.scenario = scenario
        self.state = EnvironmentState()
        self.option_order_seed = randbits(32) if option_order_seed is None else option_order_seed
        self.options = list(scenario.options)
        Random(self.option_order_seed).shuffle(self.options)

    def search_transport_options(
        self, origin: str, destination: str, travel_date: str
    ) -> dict[str, Any]:
        """Return fixed scenario options; use an ISO date (YYYY-MM-DD).

        Natural-language dates in the form ``Month D, YYYY`` are accepted as a
        compatibility fallback and normalized before matching the scenario.
        """
        trip = self.scenario.trip
        normalized_date = self._normalize_travel_date(travel_date)
        if (origin, destination, normalized_date) != (
            trip.origin,
            trip.destination,
            trip.travel_date,
        ):
            return {
                "status": "not_found",
                "reason": "No transport options are available for that trip.",
                "options": [],
            }

        self.state.search_performed = True
        return {
            "status": "ok",
            # Opaque: the scenario ID encodes experimental conditions the agent must not see.
            "trip_id": "trip_" + sha256(self.scenario.scenario_id.encode()).hexdigest()[:8],
            "options": [option.model_dump() for option in self.options],
        }

    @staticmethod
    def _normalize_travel_date(travel_date: str) -> str:
        """Canonicalize supported dates without silently changing their meaning."""
        candidate = travel_date.strip()
        for date_format in ("%Y-%m-%d", "%B %d, %Y"):
            try:
                # The input represents a calendar date, not an instant in time.
                return datetime.strptime(candidate, date_format).date().isoformat()  # noqa: DTZ007
            except ValueError:
                continue
        return candidate

    def book_transport_option(self, option_id: str) -> dict[str, Any]:
        """Book an option returned by a previous search."""
        if self.state.successful_booking is not None:
            result = self._reject_booking(option_id, "A ticket has already been booked for this trip.")
            result["booked_option_id"] = self.state.successful_booking
            return result
        if not self.state.search_performed:
            return self._reject_booking(option_id, "Search before booking an option.")
        if option_id not in {option.option_id for option in self.scenario.options}:
            return self._reject_booking(option_id, "Unknown transport option.")

        self.state.booking_attempts.append(
            BookingAttempt(option_id=option_id, status=BookingStatus.BOOKED)
        )
        self.state.successful_booking = option_id
        return {"status": BookingStatus.BOOKED, "booked_option_id": option_id}

    def _reject_booking(self, option_id: str, reason: str) -> dict[str, Any]:
        self.state.booking_attempts.append(
            BookingAttempt(option_id=option_id, status=BookingStatus.REJECTED, reason=reason)
        )
        return {"status": BookingStatus.REJECTED, "reason": reason, "booked_option_id": None}
