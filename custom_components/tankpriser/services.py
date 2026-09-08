"""Tankpriser services.

* ``nearby`` — the cheapest stations around a point *you supply*, with a
  ready-to-speak sentence and navigation links. Returns response data, so a
  caller (a Siri Shortcut over the REST API, an automation, a voice assistant)
  gets a complete answer from one call, with no entity and no device tracker in
  between. That is the point: a phone that hands over its own position cannot
  be told about the town it left an hour ago.

Two more for the per-car prediction:

* ``seed_demo_history`` — inject synthetic tanks so the prediction shows a
  number right away, instead of waiting days for real refuel cycles.
* ``reset_history`` — clear a car's learned history (e.g. after changing the
  tank size, or to undo a demo seed).

Both of those act on all configured cars, optionally filtered by name.

* ``test_notification`` — rehearse a price drop so the notification rule and its
  delivery can be checked without waiting for the chains to move. A reload
  cannot stand in for this: it clears the comparison baseline, so the first
  refresh after one is deliberately silent.

And two for testing the driving features without driving:

* ``simulate_drive`` / ``stop_simulation`` — move a virtual tracker along a
  route so the corridor search, the direction filtering and the spoken answer
  can be watched from a desk. See ``simulate.py``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import replace

import voluptuous as vol

from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import (
    CONF_FUEL_TYPES,
    MAPS_URLS,
    CONF_NEARBY_TRACKER,
    DEFAULT_SIMULATION_INTERVAL_S,
    DEFAULT_SIMULATION_SPEED_KMH,
    MIN_SIMULATION_INTERVAL_S,
    SIMULATION_ENTITY,
    SIMULATION_ROUTES,
    CONF_NOTIFY_ENABLED,
    CONF_NOTIFY_RULE,
    CONF_NOTIFY_SERVICE,
    DEFAULT_NOTIFY_RULE,
    DEFAULT_NEARBY_RADIUS_KM,
    DOMAIN,
    FUEL_TYPES,
    fuel_label,
    price_unit,
    spoken_currency,
    NEARBY_MAX_STATIONS,
    SPOKEN_STATIONS,
)
from .coordinator import (
    TankpriserData,
    async_station_pool,
    credentials_of,
    discounts_of,
    entry_coordinator,
    entry_for_position,
    pool_target,
)
from .nearby import (
    destination,
    infer_motion,
    rank_nearby,
    search_plan,
    searched_km,
    should_extend,
    spoken_cheapest,
    spoken_sentence,
)
from .accuracy import as_dict, backtest
from .notifications import evaluate_and_notify
from .simulate import DriveSimulation, stop_simulation
from .sources import area_for, country_needs_area, max_radius_km

_LOGGER = logging.getLogger(__name__)

SERVICE_NEARBY = "nearby"
SERVICE_SEED_DEMO = "seed_demo_history"
SERVICE_RESET = "reset_history"
SERVICE_TEST_NOTIFICATION = "test_notification"
SERVICE_ACCURACY = "prediction_accuracy"
SERVICE_SIMULATE = "simulate_drive"
SERVICE_STOP_SIMULATION = "stop_simulation"

ATTR_ROUTE = "route"
ATTR_WAYPOINTS = "waypoints"
ATTR_SPEED_KMH = "speed_kmh"
ATTR_INTERVAL = "interval"
ATTR_TRACKER = "tracker"
ATTR_ANNOUNCE = "announce"
ATTR_LOOP = "loop"

ATTR_DROP_ORE = "drop_ore"

ATTR_CAR = "car"
ATTR_TANKS = "tanks"
ATTR_LITRES_PER_DAY = "litres_per_day"
ATTR_DAYS_PER_TANK = "days_per_tank"

ATTR_LATITUDE = "latitude"
ATTR_LONGITUDE = "longitude"
ATTR_FUEL = "fuel"
ATTR_RADIUS_KM = "radius_km"
ATTR_MAPS = "maps"

# Navigation links are built here rather than left to the caller: a Shortcut can
# read a string out of a response, but assembling one per station from a nested
# list is a page of actions on a phone. The templates themselves live in
# const.MAPS_URLS, shared with the fill-up notification.
_MAPS_URL = MAPS_URLS

_NEARBY_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_LATITUDE): cv.latitude,
        vol.Required(ATTR_LONGITUDE): cv.longitude,
        vol.Optional(ATTR_FUEL): vol.In(list(FUEL_TYPES)),
        vol.Optional(ATTR_RADIUS_KM, default=float(DEFAULT_NEARBY_RADIUS_KM)): vol.All(
            vol.Coerce(float), vol.Range(min=1, max=100)
        ),
        vol.Optional(ATTR_MAPS, default="google"): vol.In(list(_MAPS_URL)),
    }
)

_SIMULATE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ROUTE): vol.In(list(SIMULATION_ROUTES)),
        # [[lat, lon], [lat, lon], ...] — straight lines between them, so put
        # one wherever the road you have in mind actually turns.
        vol.Optional(ATTR_WAYPOINTS): vol.All(
            cv.ensure_list,
            vol.Length(min=2),
            [vol.All(cv.ensure_list, vol.Length(min=2, max=2), [vol.Coerce(float)])],
        ),
        vol.Optional(ATTR_SPEED_KMH, default=DEFAULT_SIMULATION_SPEED_KMH): vol.All(
            vol.Coerce(float), vol.Range(min=1, max=250)
        ),
        vol.Optional(ATTR_INTERVAL, default=DEFAULT_SIMULATION_INTERVAL_S): vol.All(
            vol.Coerce(float), vol.Range(min=MIN_SIMULATION_INTERVAL_S, max=3600)
        ),
        vol.Optional(ATTR_TRACKER): cv.entity_id,
        vol.Optional(ATTR_ANNOUNCE, default=True): cv.boolean,
        vol.Optional(ATTR_LOOP, default=False): cv.boolean,
        vol.Optional(ATTR_FUEL): vol.In(list(FUEL_TYPES)),
    }
)

_SEED_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CAR): cv.string,
        vol.Optional(ATTR_TANKS, default=3): vol.All(
            vol.Coerce(int), vol.Range(min=2, max=10)
        ),
        vol.Optional(ATTR_LITRES_PER_DAY, default=5.0): vol.All(
            vol.Coerce(float), vol.Range(min=0.1, max=50.0)
        ),
        vol.Optional(ATTR_DAYS_PER_TANK, default=7.0): vol.All(
            vol.Coerce(float), vol.Range(min=1.0, max=60.0)
        ),
    }
)
_RESET_SCHEMA = vol.Schema({vol.Optional(ATTR_CAR): cv.string})
_TEST_NOTIFICATION_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DROP_ORE, default=10): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=500)
        ),
    }
)


def _prices_before_a_drop(data: TankpriserData, ore: int) -> TankpriserData:
    """The same stations as they would have been `ore` øre/L dearer.

    Used as the "previous" half of a rehearsed comparison. Copies rather than
    mutates: `data` is the coordinator's live snapshot, and raising the prices
    the card is reading from would be a lie with a 30-minute half-life.
    """
    krone = ore / 100.0
    return TankpriserData(
        stations=[
            replace(
                station,
                prices={
                    fuel: round(price + krone, 2)
                    for fuel, price in station.prices.items()
                },
            )
            for station in data.stations
        ]
    )


def _default_fuel(hass: HomeAssistant, entry=None) -> str | None:
    """The first fuel an entry is configured for, or any entry's if none given.

    So a caller that only cares about petrol need not know the internal key.
    Preferring the entry that answers the call matters where the countries
    differ: a German entry set up for diesel alone should default to diesel,
    not to whatever Denmark happens to list first.
    """
    entries = [entry] if entry is not None else []
    entries += [
        other
        for other in hass.config_entries.async_entries(DOMAIN)
        if other is not entry
    ]
    for candidate in entries:
        fuels = candidate.options.get(
            CONF_FUEL_TYPES, candidate.data.get(CONF_FUEL_TYPES, [])
        )
        for key in fuels:
            if key in FUEL_TYPES:
                return key
    return None


def _warn_if_tracker_is_not_the_one_watched(
    hass: HomeAssistant, entity_id: str
) -> None:
    """Say so when the simulated car is not the car anything is watching.

    Driving a tracker nothing is configured to follow produces a perfectly
    quiet, perfectly wrong test: the positions are written, and every sensor
    ignores them. That is a confusing half-hour to debug, and one log line
    prevents it.
    """
    for entry in hass.config_entries.async_entries(DOMAIN):
        watched = str(entry.options.get(CONF_NEARBY_TRACKER, "") or "")
        if watched and watched != entity_id:
            _LOGGER.warning(
                "Tankpriser is driving %s, but %s is set to follow %s. The "
                "nearby sensors and the motion inference will not see this "
                "drive. Point either one at the other.",
                entity_id,
                entry.title,
                watched,
            )
        elif not watched:
            _LOGGER.warning(
                "Tankpriser is driving %s, but %s has no tracker configured, "
                "so nothing will infer motion from it. Set it under "
                "Configure -> Area & fuel types.",
                entity_id,
                entry.title,
            )


def _circle_km(country: str, requested_km: float) -> float:
    """The radius of one circle in the search.

    Where the source only answers about a circle it also caps how big that
    circle may be, and one request buys the same answer whatever size is asked
    for — so there is nothing to gain by asking for less than the maximum.
    """
    cap_km = max_radius_km(country)
    return float(cap_km) if cap_km else float(requested_km)


def _motion(hass: HomeAssistant, coordinator, latitude: float, longitude: float):
    """How the caller is moving, from the nominated tracker plus this position.

    No tracker, or one with nothing usable on it, means no heading — and a
    search around the caller rather than ahead of them, which is the right
    answer when we cannot tell.
    """
    entity_id = getattr(coordinator, "nearby_tracker", "") if coordinator else ""
    state = hass.states.get(entity_id) if entity_id else None
    if state is None:
        return infer_motion(latitude, longitude)

    attrs = state.attributes
    fix = {
        "latitude": attrs.get("latitude"),
        "longitude": attrs.get("longitude"),
        "speed": attrs.get("speed"),
        "course": attrs.get("course"),
    }
    # `last_updated` moves on any state write; the position we are comparing
    # against is as old as the last one that actually changed something.
    elapsed = (dt_util.utcnow() - state.last_updated).total_seconds()
    return infer_motion(latitude, longitude, fix, elapsed)


def _step_out(latitude, longitude, motion, circle_km: float, index: int):
    """One more circle centre, further along the heading than the last.

    Spaced exactly as `search_plan` spaces them, so the extra circle continues
    the corridor instead of overlapping the one before it.
    """
    offset = circle_km * 0.8 + index * circle_km * 1.8
    return destination(latitude, longitude, motion.course_deg or 0.0, offset)


async def _pool_for(
    hass: HomeAssistant, country: str, centres: list, circle_km: float
) -> list:
    """The stations to choose from, fetched for every circle in the plan.

    A country that publishes nationally ignores the circles entirely — one
    fetch already holds every station, and the circles only shape the ranking.
    """
    credentials = credentials_of(hass)
    discounts = discounts_of(hass)
    if not country_needs_area(country):
        return await async_station_pool(hass, credentials, discounts, country)

    merged: dict[str, object] = {}
    for lat, lon in centres:
        area = area_for(country, lat, lon, int(circle_km * 1000))
        for station in await async_station_pool(
            hass, credentials, discounts, country, area
        ):
            # Circles overlap on purpose, so the same forecourt arrives more
            # than once; the source's own id makes that exact.
            merged.setdefault(station.key, station)
    return list(merged.values())


def _rank(stations, latitude, longitude, reach_km, fuel, motion):
    """Rank the pool from here, dropping what is behind you when moving."""
    return rank_nearby(
        stations,
        latitude,
        longitude,
        reach_km * 1000.0,
        fuel,
        course_deg=motion.course_deg if motion.moving else None,
    )


def _cars(hass: HomeAssistant, name: str | None) -> Iterator:
    """Yield the car trackers across all entries, optionally filtered by name."""
    for value in hass.data.get(DOMAIN, {}).values():
        for tracker in getattr(value, "cars", {}).values():
            if not name or tracker.name == name:
                yield tracker


@callback
def async_register_accuracy(hass: HomeAssistant, enabled: bool) -> None:
    """Add or remove the prediction-accuracy service to match the setting.

    Registered on demand rather than always, so that on an installation which
    has not asked for it the service does not exist at all — not in the action
    picker, not in the API. Switching the option off removes it again on the
    reload that follows.
    """
    present = hass.services.has_service(DOMAIN, SERVICE_ACCURACY)
    if not enabled:
        if present:
            hass.services.async_remove(DOMAIN, SERVICE_ACCURACY)
        return
    if present:
        return

    async def _accuracy(call: ServiceCall) -> ServiceResponse:
        wanted = call.data.get(ATTR_CAR)
        # Both gradings, so the correction can be judged rather than trusted:
        # what the raw model scored, and what the same history would have
        # scored with the correction it had earned by then.
        reports = []
        for tracker in _cars(hass, wanted):
            raw = backtest(tracker.capacity_l, tracker.model.segments, tracker.name)
            corrected = backtest(
                tracker.capacity_l, tracker.model.segments, tracker.name, calibrate=True
            )
            report = as_dict(raw)
            report["corrected"] = as_dict(corrected)
            report["calibration_in_use"] = tracker.calibration
            reports.append(report)
        if not reports:
            raise ServiceValidationError(
                f"No car called {wanted!r} is set up for prediction."
                if wanted
                else "No cars are set up for prediction, so there is nothing to grade."
            )
        return {"cars": reports}

    hass.services.async_register(
        DOMAIN,
        SERVICE_ACCURACY,
        _accuracy,
        schema=vol.Schema({vol.Optional(ATTR_CAR): cv.string}),
        supports_response=SupportsResponse.ONLY,
    )


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register the Tankpriser services once."""
    if hass.services.has_service(DOMAIN, SERVICE_NEARBY):
        return

    async def _nearby(call: ServiceCall) -> ServiceResponse:
        latitude = call.data[ATTR_LATITUDE]
        longitude = call.data[ATTR_LONGITUDE]

        # Where the caller is decides which country answers, so the position
        # has to be read before anything is defaulted from an entry.
        entry = entry_for_position(hass, latitude, longitude)
        fuel = call.data.get(ATTR_FUEL) or _default_fuel(hass, entry)
        if fuel is None:
            raise HomeAssistantError(
                "No fuel given, and no Tankpriser area is configured to take a "
                "default from."
            )

        country, _ = pool_target(hass, latitude, longitude)
        coordinator = entry_coordinator(hass, latitude, longitude)
        # The shape of the search is inferred, never asked for: at 110 km/h
        # nobody says a radius, and the right answer is not a bigger circle but
        # a corridor along the road ahead.
        motion = _motion(hass, coordinator, latitude, longitude)
        circle_km = _circle_km(country, call.data[ATTR_RADIUS_KM])
        centres = search_plan(latitude, longitude, motion, circle_km)
        reach_km = searched_km(latitude, longitude, centres, circle_km)

        stations = await _pool_for(hass, country, centres, circle_km)
        ranked = _rank(stations, latitude, longitude, reach_km, fuel, motion)

        # The cheapest sitting out at the rim suggests the good prices carry on
        # past it, and one more circle is the only way to find out. Affordable:
        # the source answers a burst of these in a few seconds.
        if motion.moving and should_extend(ranked, reach_km):
            centres = centres + [
                _step_out(latitude, longitude, motion, circle_km, len(centres))
            ]
            reach_km = searched_km(latitude, longitude, centres, circle_km)
            stations = await _pool_for(hass, country, centres, circle_km)
            ranked = _rank(stations, latitude, longitude, reach_km, fuel, motion)
        language = str(getattr(hass.config, "language", "") or "")
        danish = language.lower().startswith("da")
        listed = ranked[:NEARBY_MAX_STATIONS]
        template = _MAPS_URL[call.data[ATTR_MAPS]]
        return {
            "fuel": fuel,
            "country": country,
            "fuel_type": fuel_label(fuel, country),
            "unit": price_unit(country),
            # What was actually searched, so an answer of "nothing" can be
            # told apart from "nothing was looked at", and so a Shortcut can
            # say the range out loud without knowing how it was chosen.
            "searched_km": reach_km,
            "circles": len(centres),
            "moving": motion.moving,
            "speed_kmh": round(motion.speed_kmh, 1),
            "course_deg": (
                round(motion.course_deg) if motion.course_deg is not None else None
            ),
            "motion_source": motion.source,
            # In range, not listed below: a count that silently equalled the cap
            # reads as "there are only 8 stations near you", which is never true.
            "count": len(ranked),
            # One station, said plainly — what the documented shortcut speaks.
            "spoken_cheapest": spoken_cheapest(
                ranked,
                danish=danish,
                currency=spoken_currency(country),
                searched_km=reach_km,
            ),
            "spoken": spoken_sentence(
                ranked,
                danish=danish,
                currency=spoken_currency(country),
                searched_km=reach_km,
            ),
            "spoken_count": min(len(ranked), SPOKEN_STATIONS),
            "stations": listed,
            # Index-aligned with `stations`, so "the third one she named" is
            # urls[3] in a Shortcut. An estimated position gets an empty string
            # rather than being skipped: dropping it would silently shift every
            # station after it up one, and navigate you to the wrong forecourt.
            "urls": [
                ""
                if s["coord_approx"]
                else template.format(lat=s["latitude"], lon=s["longitude"])
                for s in listed
            ],
        }

    async def _simulate(call: ServiceCall) -> None:
        route = call.data.get(ATTR_ROUTE)
        waypoints = call.data.get(ATTR_WAYPOINTS)
        if route and waypoints:
            raise ServiceValidationError(
                "Give either a named route or your own waypoints, not both."
            )
        points = (
            [(float(lat), float(lon)) for lat, lon in waypoints]
            if waypoints
            else SIMULATION_ROUTES.get(route or "")
        )
        if not points or len(points) < 2:
            raise ServiceValidationError(
                "Give a route name or at least two waypoints to drive between. "
                f"Known routes: {', '.join(sorted(SIMULATION_ROUTES))}."
            )

        entity_id = call.data.get(ATTR_TRACKER) or SIMULATION_ENTITY
        simulation = DriveSimulation(
            hass,
            entity_id,
            points,
            call.data[ATTR_SPEED_KMH],
            call.data[ATTR_INTERVAL],
            call.data[ATTR_ANNOUNCE],
            call.data[ATTR_LOOP],
            call.data.get(ATTR_FUEL),
        )
        simulation.start()
        _warn_if_tracker_is_not_the_one_watched(hass, entity_id)

    async def _stop_simulation(call: ServiceCall) -> None:
        if not stop_simulation(hass):
            _LOGGER.info("Tankpriser: no simulation was running")

    async def _seed(call: ServiceCall) -> None:
        for tracker in _cars(hass, call.data.get(ATTR_CAR)):
            await tracker.seed_demo(
                call.data[ATTR_TANKS],
                call.data[ATTR_LITRES_PER_DAY],
                call.data[ATTR_DAYS_PER_TANK],
            )

    async def _reset(call: ServiceCall) -> None:
        for tracker in _cars(hass, call.data.get(ATTR_CAR)):
            await tracker.reset()

    async def _test_notification(call: ServiceCall) -> None:
        ore = call.data[ATTR_DROP_ORE]
        areas = 0
        for entry in hass.config_entries.async_entries(DOMAIN):
            coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
            if coordinator is None or coordinator.data is None:
                continue
            areas += 1
            options = entry.options
            if not options.get(CONF_NOTIFY_ENABLED):
                raise ServiceValidationError(
                    f'Notifications are switched off for "{entry.title}". Turn '
                    "them on under the integration's Configure -> Notifications."
                )
            if not str(options.get(CONF_NOTIFY_SERVICE) or ""):
                raise ServiceValidationError(
                    f'No notify service is set for "{entry.title}", so there is '
                    "nowhere to send one. Pick one under Configure -> "
                    "Notifications."
                )
            sent = await evaluate_and_notify(
                hass,
                entry,
                _prices_before_a_drop(coordinator.data, ore),
                coordinator.data,
                test=True,
            )
            if not sent:
                rule = options.get(CONF_NOTIFY_RULE, DEFAULT_NOTIFY_RULE)
                raise ServiceValidationError(
                    f'Nothing was sent for "{entry.title}": a {ore} øre drop '
                    f'does not satisfy the "{rule}" rule with the current '
                    "prices. Check the rule, and the threshold if that rule "
                    "uses one — the log says which test failed."
                )
        if not areas:
            raise HomeAssistantError(
                "No Tankpriser area has prices yet, so there is nothing to "
                "compare against. Wait for the first refresh and try again."
            )

    hass.services.async_register(
        DOMAIN,
        SERVICE_NEARBY,
        _nearby,
        schema=_NEARBY_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SIMULATE, _simulate, schema=_SIMULATE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_STOP_SIMULATION, _stop_simulation, schema=vol.Schema({})
    )
    hass.services.async_register(DOMAIN, SERVICE_SEED_DEMO, _seed, schema=_SEED_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_RESET, _reset, schema=_RESET_SCHEMA)
    hass.services.async_register(
        DOMAIN,
        SERVICE_TEST_NOTIFICATION,
        _test_notification,
        schema=_TEST_NOTIFICATION_SCHEMA,
    )
