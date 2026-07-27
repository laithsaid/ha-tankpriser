"""Price-change notification handling for Tankpriser."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_NOTIFY_ENABLED,
    CONF_NOTIFY_RULE,
    CONF_NOTIFY_SERVICE,
    CONF_NOTIFY_THRESHOLD,
    DEFAULT_NOTIFY_RULE,
    FUEL_TYPES,
    RULE_ANY,
    RULE_CHEAPEST,
    RULE_DECREASE,
    RULE_THRESHOLD,
)

if TYPE_CHECKING:
    from .coordinator import TankpriserData

_LOGGER = logging.getLogger(__name__)


async def evaluate_and_notify(
    hass: HomeAssistant,
    entry: ConfigEntry,
    previous: "TankpriserData",
    current: "TankpriserData",
    *,
    test: bool = False,
) -> bool:
    """Compare two snapshots and send a notification if the rule matches.

    Returns whether one was actually sent, so the manual test service can tell
    the user which of the several silences they are in rather than leaving them
    to guess. `test` only marks the title, so a rehearsal is never mistaken for
    a real price drop.
    """
    options = entry.options
    if not options.get(CONF_NOTIFY_ENABLED):
        _LOGGER.debug("Notifications are switched off for %s", entry.title)
        return False

    service = options.get(CONF_NOTIFY_SERVICE)
    if not service or "." not in service:
        # Enabled but undeliverable. A warning, not a debug line: this state
        # produces exactly the same silence as "nothing changed", and the only
        # way the user learns which one they are in is if we say so.
        _LOGGER.warning(
            "Tankpriser notifications are enabled for %s but no notify service "
            "is set, so nothing can be delivered. Set one under the "
            "integration's Configure -> Notifications.",
            entry.title,
        )
        return False

    rule = options.get(CONF_NOTIFY_RULE, DEFAULT_NOTIFY_RULE)
    threshold = options.get(CONF_NOTIFY_THRESHOLD)
    if rule == RULE_THRESHOLD and threshold is None:
        _LOGGER.warning(
            "Tankpriser rule '%s' is selected for %s but no price threshold is "
            "set, so no notification can ever fire.",
            RULE_THRESHOLD,
            entry.title,
        )
        return False
    fuel_types = options.get("fuel_types") or entry.data.get("fuel_types", [])

    messages: list[str] = []
    for fuel_key in fuel_types:
        display = FUEL_TYPES.get(fuel_key, (fuel_key, ""))[0]
        msg = _evaluate_fuel(previous, current, fuel_key, display, rule, threshold)
        if msg:
            messages.append(msg)

    if not messages:
        _LOGGER.debug(
            "Nothing to notify for %s: rule '%s' matched no fuel. Cheapest now: %s",
            entry.title,
            rule,
            {key: _cheapest_price(current, key) for key in fuel_types},
        )
        return False

    domain, _, object_id = service.partition(".")
    # The options dialog only ever offers notify.* services. Enforced again here
    # because this value is a plain string in the entry's options: anything that
    # could rewrite it would otherwise get an arbitrary service call for free.
    if domain != "notify":
        _LOGGER.warning(
            "Refusing to call %s: Tankpriser only notifies via notify.*", service
        )
        return False
    area = entry.title
    try:
        # blocking=True on purpose. Fire-and-forget hid every delivery failure —
        # a notify service that had been renamed or removed with the device it
        # belonged to raised inside a detached task, and the user saw the same
        # nothing as a quiet price day. The call is already inside the refresh's
        # try/except, so a slow or broken notifier costs one logged warning.
        await hass.services.async_call(
            domain,
            object_id,
            {
                "title": f"Tankpriser – {area}" + (" (test)" if test else ""),
                "message": "\n".join(messages),
            },
            blocking=True,
        )
    except Exception:  # noqa: BLE001
        if test:
            # A manual test is a question asked out loud; answer it with a
            # visible error rather than a line in a log they have to go find.
            raise
        _LOGGER.exception("Failed to call notify service %s", service)
        return False
    _LOGGER.debug("Notified %s via %s: %s", area, service, "; ".join(messages))
    return True


def _cheapest_price(data: "TankpriserData", fuel_key: str) -> float | None:
    station = data.cheapest(fuel_key)
    return station.prices[fuel_key] if station else None


def _evaluate_fuel(
    previous: "TankpriserData",
    current: "TankpriserData",
    fuel_key: str,
    display: str,
    rule: str,
    threshold: float | None,
) -> str | None:
    """Return a notification line for one fuel type if the rule fires."""
    old_cheapest = _cheapest_price(previous, fuel_key)
    new_cheapest = _cheapest_price(current, fuel_key)

    if new_cheapest is None:
        return None

    fmt = lambda v: f"{v:.2f}".replace(".", ",")  # noqa: E731 - local formatter

    if rule == RULE_THRESHOLD:
        if threshold is None:
            return None
        if new_cheapest >= threshold:
            return None
        # Every fall while under the line, not only the refresh that crossed it.
        # The old reading — fire once on the crossing, then never again until
        # the price climbs back over — meant that someone watching "below 17,00"
        # heard nothing when 16,19 became 16,09, which is the one thing they
        # were watching for. A rise stays silent (the label says "drops"), and
        # an unchanged price cannot repeat, so this still cannot spam.
        if old_cheapest is None or new_cheapest < old_cheapest:
            return f"{display}: {fmt(new_cheapest)} is below {fmt(threshold)}"
        return None

    if old_cheapest is None:
        return None

    if rule == RULE_DECREASE:
        if new_cheapest < old_cheapest:
            return (
                f"{display}: dropped {fmt(old_cheapest)} → {fmt(new_cheapest)}"
            )
        return None

    if rule == RULE_CHEAPEST:
        if new_cheapest != old_cheapest:
            arrow = "↓" if new_cheapest < old_cheapest else "↑"
            return (
                f"{display}: cheapest {fmt(old_cheapest)} → "
                f"{fmt(new_cheapest)} {arrow}"
            )
        return None

    if rule == RULE_ANY:
        # Any change to any station's price for this fuel.
        if _any_price_changed(previous, current, fuel_key):
            return f"{display}: prices updated (cheapest {fmt(new_cheapest)})"
        return None

    return None


def _any_price_changed(
    previous: "TankpriserData", current: "TankpriserData", fuel_key: str
) -> bool:
    """True if any station's price for the fuel changed between snapshots."""
    old = {s.name: s.prices[fuel_key] for s in previous.stations_for(fuel_key)}
    new = {s.name: s.prices[fuel_key] for s in current.stations_for(fuel_key)}
    return old != new
