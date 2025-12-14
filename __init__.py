from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS, CONF_MAPPING
from .coordinator import ModbusMappedCoordinator, load_mapping_sync


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Mapping file I/O MUST NOT run in the event loop -> run in executor
    mapping_file = entry.data[CONF_MAPPING]
    device, entities = await hass.async_add_executor_job(load_mapping_sync, mapping_file)

    coordinator = ModbusMappedCoordinator(hass, entry, device=device, entities=entities)
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator: ModbusMappedCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
    await coordinator.async_close()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
