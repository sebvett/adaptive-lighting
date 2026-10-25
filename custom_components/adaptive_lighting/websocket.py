"""WebSocket API for Adaptive Lighting brightness curves."""

from __future__ import annotations

import logging
import re
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
from .const import (
    CONF_BRIGHTNESS_CURVES,
    CONF_CURVE_GROUPS,
    CONF_LIGHTS,
    CONF_NAME,
    DOMAIN,
)
from .light_util import is_binary_light

_LOGGER = logging.getLogger(__name__)

_GROUP_ID_RE = re.compile(r"^cg_[a-zA-Z0-9_-]+$")


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


def _is_ha_light_group(hass: HomeAssistant, entity_id: str) -> bool:
    """True if this light entity is a Home Assistant light group.

    Light groups expose an ``entity_id`` attribute listing members (and are not
    Hue bridge groups flagged ``is_hue_group``). Old ``group.*`` domain helpers
    are not light entities and cannot appear in AL's light selector.
    """
    state = hass.states.get(entity_id)
    if state is None:
        return False
    members = state.attributes.get("entity_id")
    if not isinstance(members, (list, tuple)) or not members:
        return False
    if state.attributes.get("is_hue_group", False):
        return False
    return True


def _light_state_payload(hass: HomeAssistant, entity_id: str) -> dict[str, Any]:
    state = hass.states.get(entity_id)
    ha_group = _is_ha_light_group(hass, entity_id)
    if state is None:
        return {
            "state": STATE_UNAVAILABLE,
            "brightness": None,
            "brightness_pct": None,
            "binary": is_binary_light(hass, entity_id),
            "is_ha_light_group": ha_group,
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
        "is_ha_light_group": ha_group,
        "group_members": list(state.attributes.get("entity_id") or [])
        if ha_group
        else [],
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
        name = (reg.name if reg and reg.name else None) or state.name or entity_id
        available.append(
            {
                "entity_id": entity_id,
                "name": name,
                "is_ha_light_group": _is_ha_light_group(hass, entity_id),
            }
        )
    available.sort(key=lambda item: item["name"].lower())
    return available


def _normalize_curve_groups(
    raw: Any,
    lights: set[str],
    binary_lights: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Clean Curves-UI group metadata; drop unknown members / empty groups.

    Rejects groups that mix binary (on/off) lights with dimmable lights.
    Same-type groups (all binary or all dimmable) are allowed.
    """
    if not isinstance(raw, dict):
        return {}
    binary = binary_lights or set()
    used: set[str] = set()
    cleaned: dict[str, dict[str, Any]] = {}
    for group_id, payload in raw.items():
        if not isinstance(group_id, str) or not _GROUP_ID_RE.match(group_id):
            continue
        if not isinstance(payload, dict):
            continue
        members_in = payload.get("members") or []
        if not isinstance(members_in, list):
            continue
        members: list[str] = []
        for eid in members_in:
            if not isinstance(eid, str) or eid not in lights or eid in used:
                continue
            members.append(eid)
            used.add(eid)
        if len(members) < 2:
            continue
        member_binary = [eid in binary for eid in members]
        if any(member_binary) and not all(member_binary):
            _LOGGER.warning(
                "Dropping curve group %s: cannot mix on/off and dimmable lights (%s)",
                group_id,
                members,
            )
            for eid in members:
                used.discard(eid)
            continue
        name = payload.get("name") or f"Group ({len(members)})"
        cleaned[group_id] = {
            "name": str(name)[:64],
            "members": members,
        }
    return cleaned


def _clean_curve_payload(
    entity_id: str,
    payload: dict[str, Any],
    binary_lights: set[str],
) -> dict[str, int] | None:
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
        return binary_config_from_threshold(threshold)
    if "points" in payload:
        points = {
            str(int(p["virtual"])): int(p["real"])
            for p in payload["points"]
            if int(p.get("virtual", 0)) >= 1
        }
    else:
        points = payload
    return normalize_curve_config(points)  # type: ignore[arg-type]


def _sync_group_curves(
    cleaned: dict[str, dict[str, int]],
    groups: dict[str, dict[str, Any]],
    binary_lights: set[str],
) -> None:
    """Copy each group's primary curve onto every member entity_id."""
    for group in groups.values():
        members = list(group.get("members") or [])
        if len(members) < 2:
            continue
        primary = members[0]
        source = cleaned.get(primary)
        if source is None:
            continue
        for eid in members[1:]:
            # Binary and dimmable must not mix in one shared curve.
            if (eid in binary_lights) != (primary in binary_lights):
                continue
            cleaned[eid] = dict(source)


def _curves_payload(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    lights = _lights_for_entry(entry)
    data = {**entry.data, **entry.options}
    binary_lights = {eid for eid in lights if is_binary_light(hass, eid)}
    curves = BrightnessCurveSet.from_options(
        {CONF_BRIGHTNESS_CURVES: data.get(CONF_BRIGHTNESS_CURVES, {})},
        binary_lights=binary_lights,
    )
    light_states = {eid: _light_state_payload(hass, eid) for eid in lights}
    curve_groups = _normalize_curve_groups(
        data.get(CONF_CURVE_GROUPS, {}),
        set(lights),
        binary_lights,
    )
    return {
        "entry_id": entry.entry_id,
        "title": entry.title or entry.data.get(CONF_NAME, entry.entry_id),
        "lights": lights,
        "names": _friendly_names(hass, lights),
        "binary_lights": sorted(binary_lights),
        "curves": curves.export_for_ui(lights),
        "curve_groups": curve_groups,
        "room": _room_state_payload(hass, entry),
        "light_states": light_states,
        "available_lights": _available_lights(hass, lights),
        "ha_group_note": (
            "Home Assistant light-domain groups (helpers that create light.* with "
            "entity_id members) can be selected as a single light in Adaptive "
            "Lighting; Curves then shows that one entity. With expand_light_groups "
            "enabled (default), AL expands them to members at runtime. Old group.* "
            "domain helpers are not light entities and do not appear in the picker. "
            "Curves UI groups are separate: they share one curve/line without "
            "creating a HASS group."
        ),
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
        vol.Optional("curve_groups"): dict,
    }
)
@callback
def ws_save_curves(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Persist brightness curves (and optional Curves-UI groups) into options."""
    entry = _entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Config entry not found")
        return

    lights = set(_lights_for_entry(entry))
    binary_lights = {eid for eid in lights if is_binary_light(hass, eid)}
    raw_curves: dict[str, Any] = msg["curves"]
    cleaned: dict[str, dict[str, int]] = {}
    for entity_id, payload in raw_curves.items():
        if entity_id not in lights or not isinstance(payload, dict):
            continue
        result = _clean_curve_payload(entity_id, payload, binary_lights)
        if result is not None:
            cleaned[entity_id] = result

    if "curve_groups" in msg:
        groups = _normalize_curve_groups(msg["curve_groups"], lights, binary_lights)
    else:
        groups = _normalize_curve_groups(
            entry.options.get(CONF_CURVE_GROUPS, {}),
            lights,
            binary_lights,
        )
    _sync_group_curves(cleaned, groups, binary_lights)

    new_options = {
        **entry.options,
        CONF_BRIGHTNESS_CURVES: cleaned,
        CONF_CURVE_GROUPS: groups,
    }
    hass.config_entries.async_update_entry(entry, options=new_options)

    switch = hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get("switch")
    if switch is not None and hasattr(switch, "brightness_curves"):
        switch.brightness_curves = BrightnessCurveSet.from_options(
            {CONF_BRIGHTNESS_CURVES: cleaned},
            binary_lights=binary_lights,
        )

    connection.send_result(
        msg["id"],
        {"ok": True, "curves": cleaned, "curve_groups": groups},
    )


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

    seen: set[str] = set()
    cleaned: list[str] = []
    for entity_id in msg["lights"]:
        if not isinstance(entity_id, str) or not entity_id.startswith("light."):
            continue
        if entity_id in seen or entity_id.endswith("_room"):
            continue
        if hass.states.get(entity_id) is None:
            continue
        seen.add(entity_id)
        cleaned.append(entity_id)

    curves = dict(entry.options.get(CONF_BRIGHTNESS_CURVES, {}))
    curves = {eid: cfg for eid, cfg in curves.items() if eid in seen}
    binary_for_groups = {eid for eid in seen if is_binary_light(hass, eid)}
    groups = _normalize_curve_groups(
        entry.options.get(CONF_CURVE_GROUPS, {}),
        seen,
        binary_for_groups,
    )
    new_options = {
        **entry.options,
        CONF_LIGHTS: cleaned,
        CONF_BRIGHTNESS_CURVES: curves,
        CONF_CURVE_GROUPS: groups,
    }
    hass.config_entries.async_update_entry(entry, options=new_options)

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
