from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ModbusMappedCoordinator, MappedEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    coordinator: ModbusMappedCoordinator = hass.data[DOMAIN][entry.entry_id]
    ents = [e for e in coordinator.mapping.entities if e.platform == "binary_sensor"]
    async_add_entities([MappedBinarySensor(coordinator, entry, e) for e in ents])


class MappedBinarySensor(CoordinatorEntity[ModbusMappedCoordinator], BinarySensorEntity):
    def __init__(self, coordinator: ModbusMappedCoordinator, entry: ConfigEntry, ent: MappedEntity) -> None:
        super().__init__(coordinator)

        self._entry = entry
        self._ent = ent

        self._attr_unique_id = f"{entry.entry_id}:{ent.key}"
        self._attr_name = ent.name

        icon = getattr(ent, "icon", None)
        if icon:
            self._attr_icon = icon

        device_class = getattr(ent, "device_class", None)
        if device_class:
            self._attr_device_class = device_class

        description = getattr(ent, "description", None)
        if description:
            self._attr_entity_description = description

        self._attr_extra_state_attributes = {"key": ent.key}
        if description:
            self._attr_extra_state_attributes["description"] = description

    @property
    def is_on(self) -> bool | None:
        v = self.coordinator.data.get(self._ent.key)
        if v is None:
            return None
        return bool(v)
