from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ModbusMappedCoordinator, MappedEntity

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: ModbusMappedCoordinator = hass.data[DOMAIN][entry.entry_id]
    ents = [e for e in coordinator.mapping.entities if e.platform == "binary_sensor"]
    async_add_entities([MappedBinarySensor(coordinator, entry, e) for e in ents])

class MappedBinarySensor(BinarySensorEntity):
    def __init__(self, coordinator: ModbusMappedCoordinator, entry: ConfigEntry, ent: MappedEntity) -> None:
        self.coordinator = coordinator
        self.entry = entry
        self.ent = ent

        self._attr_unique_id = f"{entry.entry_id}:{ent.key}"
        self._attr_name = ent.name
        self._attr_icon = ent.icon
        if ent.device_class:
            self._attr_device_class = ent.device_class

    @property
    def device_info(self) -> DeviceInfo:
        m = self.coordinator.mapping
        return DeviceInfo(
            identifiers={(DOMAIN, self.entry.entry_id)},
            name=m.device_name,
            manufacturer=m.manufacturer,
            model=m.model,
        )

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def is_on(self) -> bool | None:
        v = self.coordinator.data.get(self.ent.key)
        return None if v is None else bool(v)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))
