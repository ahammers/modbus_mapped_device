from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ModbusMappedCoordinator, MappedEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    coordinator: ModbusMappedCoordinator = hass.data[DOMAIN][entry.entry_id]
    ents = [e for e in coordinator.mapping.entities if e.platform == "button"]
    async_add_entities([MappedButton(coordinator, entry, e) for e in ents])


class MappedButton(CoordinatorEntity[ModbusMappedCoordinator], ButtonEntity):
    def __init__(self, coordinator: ModbusMappedCoordinator, entry: ConfigEntry, ent: MappedEntity) -> None:
        super().__init__(coordinator)

        self._entry = entry
        self._ent = ent

        self._attr_unique_id = f"{entry.entry_id}:{ent.key}"
        self._attr_name = ent.name

        icon = getattr(ent, "icon", None)
        if icon:
            self._attr_icon = icon

        description = getattr(ent, "description", None)
        if description:
            self._attr_entity_description = description

        self._press_value = getattr(ent, "press_value", None)
        if self._press_value is None:
            self._press_value = 1

        self._attr_extra_state_attributes = {"key": ent.key}
        if description:
            self._attr_extra_state_attributes["description"] = description

    async def async_press(self) -> None:
        if not getattr(self._ent, "write", None):
            return
        await self.coordinator.write_holding(self._ent, self._press_value)
