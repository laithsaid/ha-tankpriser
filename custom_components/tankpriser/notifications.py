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
) -> None:
    """Compare two snapshots and send a notification if the rule matches."""
    options = entry.options
    if not options.get(CONF_NOTIFY_ENABLED):
        return

    service = options.get(CONF_NOTIFY_SERVICE)
    if not service or "." not in service:
        return

    rule = options.get(CONF_NOTIFY_RULE, DEFAULT_NOTIFY_RULE)
    threshold = options.get(CONF_NOTIFY_THRESHOLD)
    fuel_types = options.get("fuel_types") or entry.data.get("fuel_types", [])

    messages: list[str] = []
    for fuel_key in fuel_types:
        display = FUEL_TYPES.get(fuel_key, (fuel_key, ""))[0]
        msg = _evaluate_fuel(previous, current, fuel_key, display, rule, threshold)
        if msg:
            messages.append(msg)

    if not messages:
        return

    domain, _, object_id = service.partition(".")
    area = entry.title
    try:
        await hass.services.async_call(
            domain,
            object_id,
            {
                "title": f"Tankpriser – {area}",
                "message": "\n".join(messages),
            },
            blocking=False,
        )
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Failed to call notify service %s", service)


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
        was_below = old_cheapest is not None and old_cheapest < threshold
        is_below = new_cheapest < threshold
        if is_below and not was_below:
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
