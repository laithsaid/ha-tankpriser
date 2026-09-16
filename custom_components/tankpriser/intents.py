"""The Assist intent — the only fuel price that reaches an Apple car screen.

CarPlay shows four tabs and nine actionable domains (button, cover, light,
lock, scene, script and friends), and **a sensor is not one of them**. So
nothing this integration publishes as an entity can appear there, and the price
card cannot either: neither car platform lets Home Assistant draw a map.

What CarPlay *can* pin to its Quick Access tab is an **Assist prompt** — a
fixed sentence, one tap on the car's own screen, answered out loud. That is
what this intent is for. It needs no Shortcuts app, no long-lived token and no
`Get contents of URL`, which is three of the four ways the documented Shortcut
fails (see the README's "when it does not work").

Android Auto needs none of this. It lists `sensor` favourites with their state
and offers navigation to any entity carrying a location, which is exactly what
`NearbyStationsSensor` was built to be.

The *sentences* that trigger this live in the user's own configuration, under
`custom_sentences/<language>/`, because that is the only place Home Assistant
reads them from — an integration cannot ship them. Both languages are in the
README, ready to paste.
"""

from __future__ import annotations

import voluptuous as vol

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import intent

from .const import (
    CONF_COUNTRY,
    CONF_FUEL_TYPES,
    DEFAULT_COUNTRY,
    DOMAIN,
    FUEL_TYPES,
    fuel_label,
)
from .coordinator import entries_for_position, tracker_origin
from .nearby import fuel_from_words, spoken_no_position, spoken_not_followed
from .services import nearby_answer

INTENT_CHEAPEST = "TankpriserCheapest"

# The slot is free text on purpose. Assist gives us whatever the person said,
# and `fuel_from_words` decides what it meant — a list of accepted values here
# would mean maintaining the same synonyms twice, once in Python and once in
# every user's sentence file.
_SLOT_SCHEMA = {vol.Optional("fuel"): intent.non_empty_string}


def _danish(hass: HomeAssistant) -> bool:
    language = str(getattr(hass.config, "language", "") or "")
    return language.lower().startswith("da")


def _configured_fuels(hass: HomeAssistant, latitude: float, longitude: float) -> set[str]:
    """Every fuel the entries in reach of this position are set up for.

    In reach, not all of them: at the border both countries answer, and a
    German entry that follows only diesel should not make a Danish question
    about HVO100 sound answerable.
    """
    fuels: set[str] = set()
    for entry in entries_for_position(hass, latitude, longitude):
        configured = entry.options.get(
            CONF_FUEL_TYPES, entry.data.get(CONF_FUEL_TYPES, [])
        )
        fuels.update(key for key in configured if key in FUEL_TYPES)
    return fuels


def _country_here(hass: HomeAssistant, latitude: float, longitude: float) -> str:
    """The country a question asked from here is about."""
    entries = entries_for_position(hass, latitude, longitude)
    if not entries:
        return DEFAULT_COUNTRY
    return str(entries[0].data.get(CONF_COUNTRY, DEFAULT_COUNTRY))


def _spoken_labels(
    hass: HomeAssistant, latitude: float, longitude: float
) -> dict[str, str]:
    """Phrase -> fuel key, for what the countries in reach call each fuel.

    Germany sells "Super E10" where Denmark sells "Blyfri 95"; they are one key
    with two names, and near Flensburg either is a fair way to ask.
    """
    phrases: dict[str, str] = {}
    for entry in entries_for_position(hass, latitude, longitude):
        country = str(entry.data.get(CONF_COUNTRY, DEFAULT_COUNTRY))
        for key in FUEL_TYPES:
            phrases[fuel_label(key, country)] = key
    return phrases


def _origin(hass: HomeAssistant) -> tuple[float, float] | None:
    """Where to answer from, best source first.

    A nominated device beats Home, because the question is asked in a car and
    the answer is about where that car is. Home is the honest fallback — it is
    what the price sensors already describe — and is what answers when nobody
    has nominated a tracker at all.
    """
    for entry in hass.config_entries.async_entries(DOMAIN):
        coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        tracker = getattr(coordinator, "nearby_tracker", "")
        if not tracker:
            continue
        found = tracker_origin(hass, tracker)
        if found is not None:
            return found[0], found[1]

    home_lat = hass.config.latitude
    home_lon = hass.config.longitude
    if home_lat is None or home_lon is None:
        return None
    return float(home_lat), float(home_lon)


class CheapestFuelIntent(intent.IntentHandler):
    """Answer "where is the cheapest fuel" in one spoken sentence."""

    intent_type = INTENT_CHEAPEST
    slot_schema = _SLOT_SCHEMA
    description = (
        "Name the cheapest fuel station near the device Tankpriser follows, "
        "with its price and how far away it is. Optionally takes a fuel."
    )

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        hass = intent_obj.hass
        danish = _danish(hass)
        response = intent_obj.create_response()
        response.response_type = intent.IntentResponseType.QUERY_ANSWER

        origin = _origin(hass)
        if origin is None:
            response.async_set_speech(spoken_no_position(danish))
            return response
        latitude, longitude = origin

        slots = self.async_validate_slots(intent_obj.slots)
        said = slots.get("fuel", {}).get("value")
        fuel = None
        if said:
            fuel = fuel_from_words(said, _spoken_labels(hass, latitude, longitude))
            # Recognised, but not one of the fuels being followed. Saying so is
            # the whole point of telling the two cases apart: the alternative
            # is a confident price for the wrong pump. A fuel we do not
            # recognise at all falls through to the default, which is what a
            # bare "cheapest fuel" asks for anyway.
            if fuel and fuel not in _configured_fuels(hass, latitude, longitude):
                label = fuel_label(fuel, _country_here(hass, latitude, longitude))
                response.async_set_speech(spoken_not_followed(label, danish))
                return response

        answer = await nearby_answer(hass, latitude, longitude, fuel=fuel)
        response.async_set_speech(answer["spoken_cheapest"])
        # The stations ride along so a voice assistant that can show something
        # has something to show, and so an automation using this intent does
        # not have to call the service a second time to get the same answer.
        response.async_set_speech_slots(
            {
                "fuel": answer["fuel"],
                "country": answer["country"],
                "unit": answer["unit"],
                "stations": answer["stations"],
                "urls": answer["urls"],
            }
        )
        return response


@callback
def async_register_intents(hass: HomeAssistant) -> None:
    """Register the Assist intent once."""
    intent.async_register(hass, CheapestFuelIntent())
