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
    CONF_COUNTRY,
    CONF_FUEL_TYPES,
    DEFAULT_COUNTRY,
    country_of,
    MAPS_URLS,
    CONF_NEARBY_TRACKER,
    DEFAULT_SIMULATION_INTERVAL_S,
    DEFAULT_SIMULATION_SPEED_KMH,
    DEFAULT_SIMULATION_TIME_SCALE,
    MAX_SIMULATION_TIME_SCALE,
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
    price_decimals,
    price_unit,
    spoken_currency,
    NEARBY_LIMIT_MAX,
    NEARBY_MAX_STATIONS,
    SPOKEN_STATIONS,
)
from .coordinator import (
    TankpriserData,
    async_station_pool,
    credentials_of,
    discounts_of,
    entries_for_position,
    entry_coordinator,
    entry_for_position,
    pool_target,
)
from .nearby import (
    destination,
    infer_motion,
    nearest_fix,
    rank_nearby,
    search_plan,
    searched_km,
    should_extend,
    leading_group,
    spoken_by_country,
    spoken_cheapest,
    spoken_no_source,
    spoken_sentence,
    unconfigured_here,
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
ATTR_TIME_SCALE = "time_scale"

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
ATTR_LIMIT = "limit"

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
        # How many stations come back. The spoken sentence names three
        # whatever this says, so a caller that wants a map full of
        # forecourts and a caller that wants one sentence are the same
        # request with a different number.
        vol.Optional(ATTR_LIMIT, default=NEARBY_MAX_STATIONS): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=NEARBY_LIMIT_MAX)
        ),
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
        vol.Optional(
            ATTR_TIME_SCALE, default=DEFAULT_SIMULATION_TIME_SCALE
        ): vol.All(vol.Coerce(float), vol.Range(min=1, max=MAX_SIMULATION_TIME_SCALE)),
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
    """Say so when NOTHING is watching the car being driven.

    Driving a tracker nothing is configured to follow produces a perfectly
    quiet, perfectly wrong test: the positions are written, and every sensor
    ignores them. That is a confusing half-hour to debug, and one log line
    prevents it.

    The test is "does ANY entry follow this car", asked once — not "does this
    entry follow it", asked per entry. Once a second country exists the two
    differ: driving through Germany, the Danish entry is supposed to be
    watching a Danish car, and warning about it fired on every single call
    while nothing at all was wrong. A warning that cries wolf on a correct
    setup is a warning that gets read past, which costs more than it ever saved.
    """
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        return
    if any(
        str(entry.options.get(CONF_NEARBY_TRACKER, "") or "") == entity_id
        for entry in entries
    ):
        return  # something follows this car, so the drive is visible

    # Nothing follows it. Say what each entry IS pointed at, because the fix is
    # to change one of them and the log line may be all there is to go on.
    state = ", ".join(
        f"{entry.title} follows {watched}"
        if (watched := str(entry.options.get(CONF_NEARBY_TRACKER, "") or ""))
        else f"{entry.title} has none set"
        for entry in entries
    )
    _LOGGER.warning(
        "Tankpriser is driving %s, but no entry is set to follow it, so the "
        "nearby sensors and the motion inference will not see this drive (%s). "
        "Point one of them at %s under Configure -> Area & fuel types.",
        entity_id,
        state,
        entity_id,
    )


def _circle_km(country: str, requested_km: float) -> float:
    """The radius of one circle in the search.

    Where the source only answers about a circle it also caps how big that
    circle may be, and one request buys the same answer whatever size is asked
    for — so there is nothing to gain by asking for less than the maximum.
    """
    cap_km = max_radius_km(country)
    return float(cap_km) if cap_km else float(requested_km)


def _fix_of(hass: HomeAssistant, entity_id: str) -> tuple[dict, float] | None:
    """One tracker's last fix, and the age of the state that carries it."""
    state = hass.states.get(entity_id) if entity_id else None
    if state is None:
        return None
    attrs = state.attributes
    fix = {
        "latitude": attrs.get("latitude"),
        "longitude": attrs.get("longitude"),
        "speed": attrs.get("speed"),
        "course": attrs.get("course"),
    }
    # `last_updated` moves on any state write; the position we are comparing
    # against is as old as the last one that actually changed something.
    return fix, (dt_util.utcnow() - state.last_updated).total_seconds()


def _tracker_candidates(hass: HomeAssistant, coordinator) -> list[str]:
    """Every tracker any entry follows, the answering entry's first."""
    own = str(getattr(coordinator, "nearby_tracker", "") or "") if coordinator else ""
    candidates = [own] if own else []
    for entry in hass.config_entries.async_entries(DOMAIN):
        watched = str(entry.options.get(CONF_NEARBY_TRACKER, "") or "")
        if watched and watched not in candidates:
            candidates.append(watched)
    return candidates


def _motion(hass: HomeAssistant, coordinator, latitude: float, longitude: float):
    """How the caller is moving, from whichever tracker is actually here.

    Not the answering entry's tracker, which is what this used to read: the
    entry that answers is chosen by country, so the device it follows may be in
    another country entirely. `nearest_fix` holds the reasoning and the
    measurement; this end only gathers the states.

    No tracker near enough to be the caller means no fix — no heading, search
    around — the same honest answer given for a tracker with nothing usable on
    it. That also closes the louder half of the bug: a *reported* speed and
    course are believed outright, deliberately, but only from the device that
    is actually here. Another car's reported heading laid the corridor out of
    the caller's position, and unlike the parked case it pointed confidently
    the wrong way.
    """
    fixes = []
    for entity_id in _tracker_candidates(hass, coordinator):
        found = _fix_of(hass, entity_id)
        if found is not None:
            fixes.append(found)

    chosen = nearest_fix(fixes, latitude, longitude)
    if chosen is None:
        return infer_motion(latitude, longitude)
    fix, elapsed = chosen
    return infer_motion(latitude, longitude, fix, elapsed)


def _step_out(latitude, longitude, motion, circle_km: float, index: int):
    """One more circle centre, further along the heading than the last.

    Spaced exactly as `search_plan` spaces them, so the extra circle continues
    the corridor instead of overlapping the one before it.
    """
    offset = circle_km * 0.8 + index * circle_km * 1.8
    return destination(latitude, longitude, motion.course_deg or 0.0, offset)


def _country_of_entry(entry) -> str:
    """The country code an entry covers, defaulting for a caller with none."""
    if entry is None:
        return DEFAULT_COUNTRY
    return str(entry.data.get(CONF_COUNTRY, DEFAULT_COUNTRY)).lower()


def _urls_for(listed: list[dict], template: str) -> list[str]:
    """Navigation links, index-aligned with the stations they belong to.

    An estimated position gets an empty string rather than being skipped:
    dropping it would silently shift every station after it up one, and
    navigate you to the wrong forecourt.
    """
    return [
        ""
        if station["coord_approx"]
        else template.format(lat=station["latitude"], lon=station["longitude"])
        for station in listed
    ]


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


async def nearby_answer(
    hass: HomeAssistant,
    latitude: float,
    longitude: float,
    fuel: str | None = None,
    radius_km: float = float(DEFAULT_NEARBY_RADIUS_KM),
    maps: str = "google",
    limit: int = NEARBY_MAX_STATIONS,
) -> dict:
    """The cheapest stations around a point, the way the `nearby` service says it.

    Lifted out of the service handler so the Assist intent can answer with the
    same sentence. CarPlay shows no sensors at all, so a spoken answer is the
    only way a fuel price reaches an Apple car screen — and one sentence built
    twice by two code paths is one sentence that will drift.
    """
    # Where the caller is decides which countries answer, so the position
    # has to be read before anything is defaulted from an entry. Near a
    # border more than one is genuinely in reach; the first is the one the
    # single-country fields below describe.
    entries = entries_for_position(hass, latitude, longitude)
    primary = entries[0] if entries else None
    fuel = fuel or _default_fuel(hass, primary)
    if fuel is None:
        raise HomeAssistantError(
            "No fuel given, and no Tankpriser area is configured to take a "
            "default from."
        )

    # Motion is a fact about the car, not about a country: inferred once,
    # from the entry that answers, and then used to shape every search.
    # Inferring it per country would let the same car be parked in Denmark
    # and driving in Germany inside one answer.
    coordinator = entry_coordinator(hass, latitude, longitude)
    motion = _motion(hass, coordinator, latitude, longitude)

    language = str(getattr(hass.config, "language", "") or "")
    danish = language.lower().startswith("da")
    template = _MAPS_URL[maps]

    groups = []
    for entry in entries or [None]:
        country = _country_of_entry(entry)
        # The shape of the search is inferred, never asked for: at 110 km/h
        # nobody says a radius, and the right answer is not a bigger circle
        # but a corridor along the road ahead.
        circle_km = _circle_km(country, radius_km)
        centres = search_plan(latitude, longitude, motion, circle_km)
        reach_km = searched_km(latitude, longitude, centres, circle_km)
        stations = await _pool_for(hass, country, centres, circle_km)
        ranked = _rank(stations, latitude, longitude, reach_km, fuel, motion)

        # The cheapest sitting out at the rim suggests the good prices carry
        # on past it, and one more circle is the only way to find out.
        # Affordable: the source answers a burst of these in a few seconds.
        if motion.moving and should_extend(ranked, reach_km):
            centres = centres + [
                _step_out(latitude, longitude, motion, circle_km, len(centres))
            ]
            reach_km = searched_km(latitude, longitude, centres, circle_km)
            stations = await _pool_for(hass, country, centres, circle_km)
            ranked = _rank(stations, latitude, longitude, reach_km, fuel, motion)

        groups.append(
            {
                "country": country,
                "circles": len(centres),
                "searched_km": reach_km,
                "ranked": ranked,
            }
        )

    # Which group the single-country fields describe: the one with the nearest
    # forecourt to the asker. A border ask where only the far side has
    # anything must not report the near side's emptiness — "nothing within 25
    # kilometres" while a German forecourt sits 6 km away is the worst kind of
    # wrong answer — and where both sides have something, the near one leads.
    # The rule lives in `nearby.leading_group`, which explains why it is
    # distance and no longer the nearest anchor.
    filled = [group for group in groups if group["ranked"]]
    lead = leading_group(groups) or groups[0]
    lead_country = lead["country"]
    ranked = lead["ranked"]
    reach_km = lead["searched_km"]
    listed = ranked[:limit]

    # Nothing anywhere. Before saying "no stations within N kilometres" —
    # true of the pool we searched, a lie about the place — ask whether the
    # position is simply somewhere nobody has set up. Boxes overspill their
    # borders on purpose, so a pin can be inside a configured country's box
    # and still be standing in another country entirely.
    missing = (
        unconfigured_here(
            {
                _country_of_entry(other)
                for other in hass.config_entries.async_entries(DOMAIN)
            },
            latitude,
            longitude,
        )
        if not filled
        else []
    )

    # Two countries in reach and both with something to show is the only
    # case that needs them named; one country keeps the exact wording the
    # documented Shortcut has always spoken.
    # Lead first, then the rest in the order they were searched. The sentence
    # names every country in reach either way; this only decides which one is
    # heard first, and hearing about a forecourt 29 km away before the one
    # 1.4 km away is the same wrong emphasis the flat fields used to have.
    ordered = [lead] + [group for group in groups if group is not lead]

    if missing:
        spoken_one = spoken_no_source(missing[0].spoken_name(danish), danish)
    elif len(filled) > 1:
        spoken_one = spoken_by_country(
            [
                {
                    "name": country_of(group["country"]).spoken_name(danish),
                    "ranked": group["ranked"],
                    "currency": spoken_currency(group["country"]),
                }
                for group in ordered
            ],
            danish=danish,
            searched_km=reach_km,
        )
    else:
        spoken_one = spoken_cheapest(
            ranked,
            danish=danish,
            currency=spoken_currency(lead_country),
            searched_km=reach_km,
            # Only ever reaches the sentence when nothing was found, and then
            # it is the whole point: which country came up empty.
            country_name=country_of(lead_country).spoken_name(danish),
        )

    return {
        "fuel": fuel,
        "country": lead_country,
        # The country the single-country fields describe, spelled out. The
        # per-country blocks have carried one since the border answer; the top
        # level needs it too, so that an empty answer can name the country it
        # searched instead of quoting a range and leaving the rest to be
        # guessed at.
        "country_name": country_of(lead_country).spoken_name(danish),
        # Set only when the answer is empty *because* the position is in a
        # country with no area configured. The card puts this in the pin's
        # bubble, so the map can say "that is France" instead of implying the
        # forecourts there do not exist.
        "no_source_country": missing[0].name if missing else "",
        "fuel_type": fuel_label(fuel, lead_country),
        "unit": price_unit(lead_country, fuel),
        # What was actually searched, so an answer of "nothing" can be
        # told apart from "nothing was looked at", and so a Shortcut can
        # say the range out loud without knowing how it was chosen.
        "searched_km": reach_km,
        "circles": lead["circles"],
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
        "spoken_cheapest": spoken_one,
        # The list sentence answers the same question as `spoken_cheapest`,
        # so an empty one has to be as honest: a country with no source said
        # plainly, and otherwise the country the range was searched in.
        "spoken": (
            spoken_no_source(missing[0].spoken_name(danish), danish)
            if missing
            else spoken_sentence(
                ranked,
                danish=danish,
                currency=spoken_currency(lead_country),
                searched_km=reach_km,
                country_name=country_of(lead_country).spoken_name(danish),
            )
        ),
        "spoken_count": min(len(ranked), SPOKEN_STATIONS),
        "stations": listed,
        # Index-aligned with `stations`, so "the third one she named" is
        # urls[3] in a Shortcut. An estimated position gets an empty string
        # rather than being skipped: dropping it would silently shift every
        # station after it up one, and navigate you to the wrong forecourt.
        "urls": _urls_for(listed, template),
        # Everything in reach, one block per country, the one the fields
        # above describe first. The top level stays single-country on
        # purpose: a Shortcut reading `urls[0]` must keep getting a station
        # priced in the currency `unit` just named, and prices in two
        # currencies cannot share one ranked list. Callers that want to
        # *show* the border — the map, the price list — read this instead.
        "countries": [
            {
                "country": group["country"],
                "country_name": country_of(group["country"]).spoken_name(danish),
                "fuel_type": fuel_label(fuel, group["country"]),
                "unit": price_unit(group["country"], fuel),
                # Germany signs to three decimals and Denmark to
                # two, so a card showing both needs the figure
                # per country rather than one card-wide setting.
                "decimals": price_decimals(group["country"]),
                "searched_km": group["searched_km"],
                "circles": group["circles"],
                "count": len(group["ranked"]),
                "stations": group["ranked"][:limit],
                "urls": _urls_for(group["ranked"][:limit], template),
            }
            for group in ordered
        ],
    }


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register the Tankpriser services once."""
    if hass.services.has_service(DOMAIN, SERVICE_NEARBY):
        return

    async def _nearby(call: ServiceCall) -> ServiceResponse:
        return await nearby_answer(
            hass,
            call.data[ATTR_LATITUDE],
            call.data[ATTR_LONGITUDE],
            fuel=call.data.get(ATTR_FUEL),
            radius_km=call.data[ATTR_RADIUS_KM],
            limit=call.data[ATTR_LIMIT],
            maps=call.data[ATTR_MAPS],
        )

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
            call.data[ATTR_TIME_SCALE],
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
