from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.discovery import async_load_platform

from .const import DOMAIN


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up via YAML."""
    hass.data.setdefault(DOMAIN, {})
    # Sensor-Plattform laden (YAML-style)
    hass.async_create_task(async_load_platform(hass, "sensor", DOMAIN, {}, config))
    return True
