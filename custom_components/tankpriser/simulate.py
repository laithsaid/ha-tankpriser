"""Drive a virtual car along a route, so the driving features can be tested.

Everything that answers "cheap fuel" while you are moving — the motion
inference, the forward corridor, dropping stations behind you, the spoken
range — only behaves differently when something is actually in motion. Testing
that by driving to Munich is not a reasonable ask, and neither is trusting it
untested on the one occasion it matters.

So this moves a *tracker* rather than faking an answer. It writes positions,
speed and course onto an entity exactly as the Home Assistant companion app
would, and then gets out of the way: the service, the sensors, a Siri Shortcut
and any automation all see a car crossing Germany and cannot tell the
difference. Nothing is stubbed, which is the point — a simulation that bypassed
the code under test would prove nothing.

The entity is a plain state write, not a real entity. It costs nothing when
unused, needs no config entry, and disappears on restart, which is the right
lifetime for a test fixture.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from homeassistant.core import HomeAssistant

from .const import DOMAIN, EVENT_SIMULATION_STEP, SIMULATION_DATA_KEY
from .nearby import destination, haversine_m, initial_bearing

_LOGGER = logging.getLogger(__name__)


@dataclass
class Leg:
    """One straight section of the route, with where it starts and how long."""

    latitude: float
    longitude: float
    bearing: float
    length_km: float


def build_legs(points: list[tuple[float, float]]) -> list[Leg]:
    """Turn a list of waypoints into legs with bearings and lengths.

    Straight lines between waypoints, not real roads: what the search logic
    reads is a position, a speed and a heading, and a motorway is close enough
    to straight over the tens of kilometres a corridor covers. Add waypoints
    where a route genuinely turns.
    """
    legs: list[Leg] = []
    for (lat1, lon1), (lat2, lon2) in zip(points, points[1:]):
        length_km = haversine_m(lat1, lon1, lat2, lon2) / 1000.0
        if length_km <= 0:
            continue
        legs.append(Leg(lat1, lon1, initial_bearing(lat1, lon1, lat2, lon2), length_km))
    return legs


def position_at(legs: list[Leg], travelled_km: float) -> tuple[float, float, float]:
    """Where you are after driving ``travelled_km``, and which way you face."""
    remaining = max(0.0, travelled_km)
    for leg in legs:
        if remaining <= leg.length_km:
            latitude, longitude = destination(
                leg.latitude, leg.longitude, leg.bearing, remaining
            )
            return latitude, longitude, leg.bearing
        remaining -= leg.length_km
    last = legs[-1]
    latitude, longitude = destination(
        last.latitude, last.longitude, last.bearing, last.length_km
    )
    return latitude, longitude, last.bearing


class DriveSimulation:
    """A car being driven along a route, one tick at a time."""

    def __init__(
        self,
        hass: HomeAssistant,
        entity_id: str,
        points: list[tuple[float, float]],
        speed_kmh: float,
        interval_s: float,
        announce: bool,
        loop: bool,
        fuel: str | None,
    ) -> None:
        self.hass = hass
        self.entity_id = entity_id
        self.legs = build_legs(points)
        self.speed_kmh = speed_kmh
        self.interval_s = interval_s
        self.announce = announce
        self.loop = loop
        self.fuel = fuel
        self.total_km = sum(leg.length_km for leg in self.legs)
        self.travelled_km = 0.0
        self._task: asyncio.Task | None = None

    @property
    def step_km(self) -> float:
        return self.speed_kmh * self.interval_s / 3600.0

    @property
    def steps(self) -> int:
        return max(1, int(self.total_km / self.step_km) + 1)

    def start(self) -> None:
        """Begin driving, replacing any drive already in progress."""
        stop_simulation(self.hass)
        self.hass.data[SIMULATION_DATA_KEY] = self
        self._task = self.hass.async_create_task(self._run())

    def stop(self) -> None:
        """Stop driving and leave the car parked where it got to."""
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
        self._park()

    def _park(self) -> None:
        """Write a final position at a standstill and forget this drive.

        Speed zero matters: a simulation that simply stopped writing would
        leave the last reading saying 130 km/h, and everything downstream would
        go on searching a corridor ahead of a car that is not moving.
        """
        latitude, longitude, bearing = position_at(self.legs, self.travelled_km)
        self._write(latitude, longitude, bearing, 0.0)
        if self.hass.data.get(SIMULATION_DATA_KEY) is self:
            self.hass.data.pop(SIMULATION_DATA_KEY, None)

    async def _run(self) -> None:
        _LOGGER.info(
            "Tankpriser simulation: %s driving %.0f km at %.0f km/h, a step every "
            "%.0fs (%d steps). %s",
            self.entity_id,
            self.total_km,
            self.speed_kmh,
            self.interval_s,
            self.steps,
            (
                "Each step asks for an answer, which for a source that is "
                "queried by area costs a handful of requests against your key."
                if self.announce
                else "Positions only; nothing is fetched unless something asks."
            ),
        )
        try:
            while True:
                latitude, longitude, bearing = position_at(
                    self.legs, self.travelled_km
                )
                self._write(latitude, longitude, bearing, self.speed_kmh)
                if self.announce:
                    await self._announce(latitude, longitude)
                self.hass.bus.async_fire(
                    EVENT_SIMULATION_STEP,
                    {
                        "entity_id": self.entity_id,
                        "latitude": latitude,
                        "longitude": longitude,
                        "course": round(bearing, 1),
                        "speed_kmh": self.speed_kmh,
                        "travelled_km": round(self.travelled_km, 1),
                        "total_km": round(self.total_km, 1),
                    },
                )

                if self.travelled_km >= self.total_km:
                    if not self.loop:
                        _LOGGER.info(
                            "Tankpriser simulation: arrived after %.0f km", self.total_km
                        )
                        # Park directly rather than through `stop`: cancelling
                        # the task we are currently running inside is a needless
                        # way to end a drive that has simply finished.
                        self._task = None
                        self._park()
                        return
                    self.travelled_km = 0.0
                else:
                    self.travelled_km = min(
                        self.total_km, self.travelled_km + self.step_km
                    )
                await asyncio.sleep(self.interval_s)
        except asyncio.CancelledError:
            _LOGGER.info(
                "Tankpriser simulation stopped after %.0f of %.0f km",
                self.travelled_km,
                self.total_km,
            )
            raise

    def _write(
        self, latitude: float, longitude: float, bearing: float, speed_kmh: float
    ) -> None:
        """Write one position, shaped like the companion app's."""
        self.hass.states.async_set(
            self.entity_id,
            "not_home",
            {
                "friendly_name": "Tankpriser simulated drive",
                "source_type": "gps",
                "latitude": latitude,
                "longitude": longitude,
                "gps_accuracy": 10,
                # Metres per second and compass degrees, the units the app uses
                # — the whole point is that nothing downstream can tell this
                # apart from a real phone.
                "speed": round(speed_kmh / 3.6, 2),
                "course": round(bearing, 1),
                "battery_level": 100,
                "simulated": True,
                "travelled_km": round(self.travelled_km, 1),
                "route_km": round(self.total_km, 1),
                "updated_at": time.time(),
            },
        )

    async def _announce(self, latitude: float, longitude: float) -> None:
        """Ask the real service what it would say from here, and log it."""
        payload = {"latitude": latitude, "longitude": longitude}
        if self.fuel:
            payload["fuel"] = self.fuel
        try:
            answer = await self.hass.services.async_call(
                DOMAIN, "nearby", payload, blocking=True, return_response=True
            )
        except Exception as err:  # noqa: BLE001 - a bad answer must not stop the drive
            _LOGGER.warning("Tankpriser simulation: nearby failed: %s", err)
            return
        if not answer:
            return
        _LOGGER.info(
            "Tankpriser simulation @ %.0f km (%.4f, %.4f): %s "
            "[%d found, %d circles, %s km searched, %s]",
            self.travelled_km,
            latitude,
            longitude,
            answer.get("spoken_cheapest"),
            answer.get("count", 0),
            answer.get("circles", 0),
            answer.get("searched_km"),
            "moving" if answer.get("moving") else "parked",
        )


def stop_simulation(hass: HomeAssistant) -> bool:
    """Stop the running simulation, if there is one. True if there was."""
    running: DriveSimulation | None = hass.data.get(SIMULATION_DATA_KEY)
    if running is None:
        return False
    running.stop()
    hass.data.pop(SIMULATION_DATA_KEY, None)
    return True
