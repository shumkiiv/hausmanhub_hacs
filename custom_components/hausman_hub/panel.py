"""Sidebar panel registration for the HausmanHub admin page."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

PANEL_URL_PATH = "hausman-hub"
PANEL_WEBCOMPONENT_NAME = "hausman-hub-panel"
PANEL_STATIC_URL = "/api/hausman_hub/panel"
PANEL_JS_URL = f"{PANEL_STATIC_URL}/hausman-hub-panel.js"
PANEL_FILES = Path(__file__).parent / "frontend"
_DATA_KEY = "hausman_hub_panel"
_DATA_STATIC_REGISTERED = "static_registered"


async def async_register_hausmanhub_panel(hass: HomeAssistant) -> None:
    """Register the static module and the sidebar panel idempotently."""

    from homeassistant.components import frontend
    from homeassistant.components.http import StaticPathConfig
    from homeassistant.components.panel_custom import async_register_panel

    data = hass.data.setdefault(_DATA_KEY, {})
    if not data.get(_DATA_STATIC_REGISTERED):
        # Static paths cannot be unregistered in Home Assistant, so they are
        # registered exactly once per server lifetime. Serving stale files
        # after an unload is harmless: the panel route itself is removed.
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    PANEL_STATIC_URL,
                    str(PANEL_FILES),
                    False,
                )
            ]
        )
        data[_DATA_STATIC_REGISTERED] = True
    if frontend.async_panel_exists(hass, PANEL_URL_PATH):
        return
    await async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_WEBCOMPONENT_NAME,
        sidebar_title="HausmanHub",
        sidebar_icon="mdi:thermostat",
        module_url=PANEL_JS_URL,
        require_admin=True,
        config_panel_domain="hausman_hub",
    )


def unregister_hausmanhub_panel(hass: HomeAssistant) -> None:
    """Remove the sidebar panel while leaving already served files harmless."""

    from homeassistant.components.frontend import async_remove_panel

    async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)
