from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ModbusMappedCoordinator, MappedEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    coordinator: ModbusMappedCoordinator = hass.data[DOMAIN][entry.entry_id]
    ents = [e for e in coordinator.mapping.entities if e.platform == "switch"]
    async_add_entities([MappedSwitch(coordinator, entry, e) for e in ents])


class MappedSwitch(CoordinatorEntity[ModbusMappedCoordinator], SwitchEntity):
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

        self._attr_extra_state_attributes = {"key": ent.key}
        if description:
            self._attr_extra_state_attributes["description"] = description

    @property
    def is_on(self) -> bool | None:
        v = self.coordinator.data.get(self._ent.key)
        if v is None:
            return None
        return bool(v)

    async def async_turn_on(self, **kwargs) -> None:
        await self._write(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._write(False)

    async def _write(self, value: bool) -> None:
        # Prefer entity-based write (holding-bit-switch etc.)
        if getattr(self._ent, "write", None):
            await self.coordinator.write_holding(self._ent, value)
            return

        # No write section -> nothing to do (read-only)
        return
