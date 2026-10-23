"""Adaptive Lighting integration in Home-Assistant."""

import logging
from functools import partial
from pathlib import Path
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import CONF_SOURCE, Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import service

from .const import (
    _DOMAIN_SCHEMA,  # pyright: ignore[reportPrivateUsage]
    ATTR_ADAPTIVE_LIGHTING_MANAGER,
    CONF_NAME,
    DOMAIN,
    SERVICE_APPLY,
    SERVICE_CHANGE_SWITCH_SETTINGS,
    SERVICE_SET_MANUAL_CONTROL,
    SET_MANUAL_CONTROL_SCHEMA,
    UNDO_UPDATE_LISTENER,
    apply_service_schema,
    change_switch_settings_schema,
)
from .switch import (
    handle_apply_service,
    handle_change_switch_settings,
    handle_set_manual_control_service,
)
from .websocket import async_register_websockets

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SWITCH, Platform.LIGHT]
FRONTEND_URL = f"/{DOMAIN}-curves"
PANEL_URL = "/adaptive-lighting-curves"
_FRONTEND_REGISTERED = False


def _all_unique_names(value: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate that all entities have a unique profile name."""
    hosts = [device[CONF_NAME] for device in value]
    schema = vol.Schema(vol.Unique())
    schema(hosts)
    return value


CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.All(cv.ensure_list, [_DOMAIN_SCHEMA], _all_unique_names)},
    extra=vol.ALLOW_EXTRA,
)


async def reload_configuration_yaml(event: Event) -> None:
    """Reload configuration.yaml."""
    hass: HomeAssistant | None = event.data.get("hass")
    if hass is not None:
        await hass.services.async_call("homeassistant", "check_config", {})
    else:
        _LOGGER.error("HomeAssistant instance not found in event data.")


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Serve the curve editor and register a sidebar panel once."""
    global _FRONTEND_REGISTERED  # noqa: PLW0603
    if _FRONTEND_REGISTERED:
        return

    frontend_dir = Path(__file__).parent / "frontend"
    try:
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    FRONTEND_URL,
                    str(frontend_dir),
                    cache_headers=False,
                )
            ]
        )
    except Exception:  # noqa: BLE001 — older HA fallbacks
        _LOGGER.debug("StaticPathConfig registration failed; trying legacy API")
        register = getattr(hass.http, "async_register_static_path", None) or getattr(
            hass.http, "register_static_path", None
        )
        if register is not None:
            result = register(FRONTEND_URL, str(frontend_dir), False)
            if hasattr(result, "__await__"):
                await result

    try:
        # Use keyword args: newer HA inserted sidebar_default_visible before
        # frontend_url_path, which broke the previous positional call. Also
        # hass.components was removed — import frontend directly.
        frontend.async_register_built_in_panel(
            hass,
            component_name="iframe",
            sidebar_title="AL Curves",
            sidebar_icon="mdi:chart-bell-curve-cumulative",
            frontend_url_path=PANEL_URL.lstrip("/"),
            config={"url": f"{FRONTEND_URL}/index.html?panel=1"},
            require_admin=True,
        )
    except ValueError:
        # Panel already registered (e.g. after reload).
        pass
    except Exception:  # noqa: BLE001
        _LOGGER.warning("Could not register sidebar panel", exc_info=True)

    _FRONTEND_REGISTERED = True


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Import integration from config."""
    hass.services.async_register(
        domain=DOMAIN,
        service=SERVICE_APPLY,
        service_func=partial(handle_apply_service, hass),
        schema=apply_service_schema(),
    )

    hass.services.async_register(
        domain=DOMAIN,
        service=SERVICE_SET_MANUAL_CONTROL,
        service_func=partial(handle_set_manual_control_service, hass),
        schema=SET_MANUAL_CONTROL_SCHEMA,
    )

    if register_platform_service := getattr(
        service,
        "async_register_platform_entity_service",
        None,
    ):
        register_platform_service(
            hass,
            DOMAIN,
            SERVICE_CHANGE_SWITCH_SETTINGS,
            entity_domain=Platform.SWITCH,
            func=handle_change_switch_settings,
            schema=change_switch_settings_schema(),
        )

    async_register_websockets(hass)
    await _async_register_frontend(hass)

    if DOMAIN in config:
        for entry in config[DOMAIN]:
            hass.async_create_task(
                hass.config_entries.flow.async_init(
                    DOMAIN,
                    context={CONF_SOURCE: SOURCE_IMPORT},
                    data=entry,
                ),
            )
    return True


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up the component."""
    data = hass.data.setdefault(DOMAIN, {})

    # This will reload any changes the user made to any YAML configurations.
    # Called during 'quick reload' or hass.reload_config_entry
    hass.bus.async_listen("hass.config.entry_updated", reload_configuration_yaml)

    # Ensure websockets/frontend exist even when only config entries are used
    # (no YAML `adaptive_lighting:` block, so async_setup may not run services
    # path — HA still calls async_setup for config_flow integrations).
    async_register_websockets(hass)
    await _async_register_frontend(hass)

    undo_listener = config_entry.add_update_listener(async_update_options)
    data[config_entry.entry_id] = {UNDO_UPDATE_LISTENER: undo_listener}
    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    return True


async def async_update_options(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Update options."""
    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(
        config_entry,
        PLATFORMS,
    )
    data = hass.data[DOMAIN]
    data[config_entry.entry_id][UNDO_UPDATE_LISTENER]()
    if unload_ok:
        data.pop(config_entry.entry_id)

    if len(data) == 1 and ATTR_ADAPTIVE_LIGHTING_MANAGER in data:
        # no more config_entries
        manager = data.pop(ATTR_ADAPTIVE_LIGHTING_MANAGER)
        manager.disable()

    if not data:
        hass.data.pop(DOMAIN)

    return unload_ok
