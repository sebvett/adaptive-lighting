"""WebSocket API for Adaptive Lighting brightness curves."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.components.light import ATTR_BRIGHTNESS, DOMAIN as LIGHT_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry

from .brightness_curves import (
    BrightnessCurveSet,
    binary_config_from_threshold,
    normalize_curve_config,
    threshold_from_config,
)
from .const import CONF_BRIGHTNESS_CURVES, CONF_LIGHTS, CONF_NAME, DOMAIN
from .light_util import is_binary_light

_LOGGER = logging.getLogger(__name__)


@callback
def async_register_websockets(hass: HomeAssistant) -> None:
    """Register Adaptive Lighting websocket commands."""
    if hass.data.setdefault(DOMAIN, {}).get("_ws_registered"):
        return
    websocket_api.async_register_command(hass, ws_list_instances)
    websocket_api.async_register_command(hass, ws_get_curves)
    websocket_api.async_register_command(hass, ws_save_curves)
    websocket_api.async_register_command(hass, ws_set_room_brightness)
    websocket_api.async_register_command(hass, ws_update_lights)
    hass.data[DOMAIN]["_ws_registered"] = True


def _entry(hass: HomeAssistant, entry_id: str) -> ConfigEntry | None:
    return hass.config_entries.async_get_entry(entry_id)


def _lights_for_entry(entry: ConfigEntry) -> list[str]:
    data = {**entry.data, **entry.options}
    return list(data.get(CONF_LIGHTS, []))


def _friendly_names(hass: HomeAssistant, entity_ids: list[str]) -> dict[str, str]:
    registry = async_get_entity_registry(hass)
    names: dict[str, str] = {}
    for entity_id in entity_ids:
        entry = registry.async_get(entity_id)
        state = hass.states.get(entity_id)
        if entry and entry.name:
            names[entity_id] = entry.name
        elif state and state.name:
            names[entity_id] = state.name
        else:
            names[entity_id] = entity_id
    return names


def _room_entity_id(entry: ConfigEntry) -> str:
    """Return the virtual room light entity_id for an entry."""
    slug = (entry.data.get(CONF_NAME) or entry.title or "room").lower()
    slug = "".join(ch if ch.isalnum() else "_" for ch in slug).strip("_") or "room"
    return f"light.{slug}_room"


def _light_state_payload(hass: HomeAssistant, entity_id: str) -> dict[str, Any]:
    state = hass.states.get(entity_id)
    if state is None:
        return {
            "state": STATE_UNAVAILABLE,
            "brightness": None,
            "brightness_pct": None,
            "binary": is_binary_light(hass, entity_id),
        }
    brightness = state.attributes.get(ATTR_BRIGHTNESS)
    brightness_pct = None
    if brightness is not None:
        brightness_pct = round(100 * int(brightness) / 255)
    elif state.state == STATE_ON and is_binary_light(hass, entity_id):
        brightness_pct = 100
    return {
        "state": state.state,
        "brightness": brightness,
        "brightness_pct": brightness_pct,
        "binary": is_binary_light(hass, entity_id),
    }


def _room_state_payload(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    room_id = _room_entity_id(entry)
    room_light = hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get("room_light")
    if room_light is not None:
        bri = getattr(room_light, "brightness", None)
        is_on = bool(getattr(room_light, "is_on", False))
        return {
            "entity_id": room_id,
            "state": STATE_ON if is_on else "off",
            "brightness": bri,
            "brightness_pct": round(100 * int(bri) / 255) if bri is not None else 0,
        }
    state = hass.states.get(room_id)
    if state is None:
        return {
            "entity_id": room_id,
            "state": STATE_UNAVAILABLE,
            "brightness": None,
            "brightness_pct": 0,
        }
    bri = state.attributes.get(ATTR_BRIGHTNESS)
    return {
        "entity_id": room_id,
        "state": state.state,
        "brightness": bri,
        "brightness_pct": round(100 * int(bri) / 255) if bri is not None else 0,
    }


def _available_lights(hass: HomeAssistant, current: list[str]) -> list[dict[str, str]]:
    """Return other light entities that can be added to this instance."""
    current_set = set(current)
    # Lights already claimed by another AL instance.
    claimed: set[str] = set()
    for entry in hass.config_entries.async_entries(DOMAIN):
        claimed.update(_lights_for_entry(entry))

    registry = async_get_entity_registry(hass)
    available: list[dict[str, str]] = []
    for state in hass.states.async_all(LIGHT_DOMAIN):
        entity_id = state.entity_id
        if entity_id in current_set:
            continue
        if entity_id.endswith("_room"):
            continue
        if entity_id in claimed:
            continue
        if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            continue
        reg = registry.async_get(entity_id)
        if reg and reg.disabled:
            continue
        name = (
            (reg.name if reg and reg.name else None)
            or state.name
            or entity_id
        )
        available.append({"entity_id": entity_id, "name": name})
    available.sort(key=lambda item: item["name"].lower())
    return available


def _curves_payload(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    lights = _lights_for_entry(entry)
    data = {**entry.data, **entry.options}
    binary_lights = {eid for eid in lights if is_binary_light(hass, eid)}
    curves = BrightnessCurveSet.from_options(
        {CONF_BRIGHTNESS_CURVES: data.get(CONF_BRIGHTNESS_CURVES, {})},
        binary_lights=binary_lights,
    )
    light_states = {eid: _light_state_payload(hass, eid) for eid in lights}
    return {
        "entry_id": entry.entry_id,
        "title": entry.title or entry.data.get(CONF_NAME, entry.entry_id),
        "lights": lights,
        "names": _friendly_names(hass, lights),
        "binary_lights": sorted(binary_lights),
        "curves": curves.export_for_ui(lights),
        "room": _room_state_payload(hass, entry),
        "light_states": light_states,
        "available_lights": _available_lights(hass, lights),
    }


@websocket_api.websocket_command(
    {
        vol.Required("type"): "adaptive_lighting/list_instances",
    }
)
@callback
def ws_list_instances(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """List Adaptive Lighting config entries for the curve editor."""
    instances = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        lights = _lights_for_entry(entry)
        instances.append(
            {
                "entry_id": entry.entry_id,
                "title": entry.title or entry.data.get(CONF_NAME, entry.entry_id),
                "lights": lights,
                "names": _friendly_names(hass, lights),
                "room": _room_state_payload(hass, entry),
            }
        )
    connection.send_result(msg["id"], {"instances": instances})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "adaptive_lighting/get_curves",
        vol.Required("entry_id"): str,
    }
)
@callback
def ws_get_curves(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return per-light brightness curves for an Adaptive Lighting instance."""
    entry = _entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Config entry not found")
        return

    connection.send_result(msg["id"], _curves_payload(hass, entry))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "adaptive_lighting/save_curves",
        vol.Required("entry_id"): str,
        vol.Required("curves"): dict,
    }
)
@callback
def ws_save_curves(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Persist brightness curves into config entry options."""
    entry = _entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Config entry not found")
        return

    lights = set(_lights_for_entry(entry))
    binary_lights = {eid for eid in lights if is_binary_light(hass, eid)}
    raw_curves: dict[str, Any] = msg["curves"]
    cleaned: dict[str, dict[str, int]] = {}
    for entity_id, payload in raw_curves.items():
        if entity_id not in lights:
            continue
        if not isinstance(payload, dict):
            continue
        if entity_id in binary_lights or payload.get("binary"):
            if "threshold" in payload:
                threshold = int(payload["threshold"])
            elif "points" in payload and payload["points"]:
                threshold = threshold_from_config(
                    {
                        str(int(p["virtual"])): int(p.get("real", 100))
                        for p in payload["points"]
                        if int(p.get("virtual", 0)) >= 1
                    }
                )
            else:
                threshold = threshold_from_config(payload)
            cleaned[entity_id] = binary_config_from_threshold(threshold)
            continue
        if "points" in payload:
            points = {
                str(int(p["virtual"])): int(p["real"])
                for p in payload["points"]
                if int(p.get("virtual", 0)) >= 1
            }
        else:
            points = payload
        cleaned[entity_id] = normalize_curve_config(points)  # type: ignore[arg-type]

    new_options = {**entry.options, CONF_BRIGHTNESS_CURVES: cleaned}
    hass.config_entries.async_update_entry(entry, options=new_options)

    # Hot-update the running switch if present.
    switch = hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get("switch")
    if switch is not None and hasattr(switch, "brightness_curves"):
        switch.brightness_curves = BrightnessCurveSet.from_options(
            {CONF_BRIGHTNESS_CURVES: cleaned},
            binary_lights=binary_lights,
        )

    connection.send_result(msg["id"], {"ok": True, "curves": cleaned})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "adaptive_lighting/set_room_brightness",
        vol.Required("entry_id"): str,
        vol.Required("brightness_pct"): vol.All(int, vol.Range(min=0, max=100)),
    }
)
@callback
def ws_set_room_brightness(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Set the virtual room light brightness (for curve testing)."""
    entry = _entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Config entry not found")
        return

    brightness_pct = int(msg["brightness_pct"])
    brightness = max(0, min(255, round(255 * brightness_pct / 100)))
    room_light = hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get("room_light")
    room_id = _room_entity_id(entry)

    async def _apply() -> None:
        if room_light is not None:
            if brightness <= 0:
                await room_light.async_turn_off()
            else:
                await room_light.async_turn_on(**{ATTR_BRIGHTNESS: brightness})
            return
        service = "turn_off" if brightness <= 0 else "turn_on"
        data: dict[str, Any] = {"entity_id": room_id}
        if brightness > 0:
            data[ATTR_BRIGHTNESS] = brightness
        await hass.services.async_call("light", service, data, blocking=True)

    hass.async_create_task(_apply())
    connection.send_result(
        msg["id"],
        {
            "ok": True,
            "entity_id": room_id,
            "brightness": brightness,
            "brightness_pct": brightness_pct,
        },
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "adaptive_lighting/update_lights",
        vol.Required("entry_id"): str,
        vol.Required("lights"): [str],
    }
)
@callback
def ws_update_lights(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Replace the lights list for an Adaptive Lighting instance."""
    entry = _entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Config entry not found")
        return

    # Validate entity ids exist and are lights; keep order, drop duplicates.
    seen: set[str] = set()
    cleaned: list[str] = []
    for entity_id in msg["lights"]:
        if not isinstance(entity_id, str) or not entity_id.startswith("light."):
            continue
        if entity_id in seen:
            continue
        if entity_id.endswith("_room"):
            continue
        state = hass.states.get(entity_id)
        if state is None:
            continue
        seen.add(entity_id)
        cleaned.append(entity_id)

    curves = dict(entry.options.get(CONF_BRIGHTNESS_CURVES, {}))
    curves = {eid: cfg for eid, cfg in curves.items() if eid in seen}
    new_options = {
        **entry.options,
        CONF_LIGHTS: cleaned,
        CONF_BRIGHTNESS_CURVES: curves,
    }
    hass.config_entries.async_update_entry(entry, options=new_options)

    # Hot-update configured lights on the running switch before reload lands.
    switch = hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get("switch")
    if switch is not None:
        switch._configured_lights = list(cleaned)  # noqa: SLF001
        if hasattr(switch, "brightness_curves"):
            binary_lights = {eid for eid in cleaned if is_binary_light(hass, eid)}
            switch.brightness_curves = BrightnessCurveSet.from_options(
                {CONF_BRIGHTNESS_CURVES: curves},
                binary_lights=binary_lights,
            )

    connection.send_result(
        msg["id"],
        {"ok": True, "lights": cleaned, **_curves_payload(hass, entry)},
    )
