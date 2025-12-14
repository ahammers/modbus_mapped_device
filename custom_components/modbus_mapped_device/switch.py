from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ModbusMappedCoordinator, MappedEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: ModbusMappedCoordinator = hass.data[DOMAIN][entry.entry_id]
    ents = [e for e in coordinator.mapping.entities if e.platform == "switch"]
    async_add_entities([MappedSwitch(coordinator, entry, e) for e in ents])


class MappedSwitch(SwitchEntity):
    def __init__(self, coordinator: ModbusMappedCoordinator, entry: ConfigEntry, ent: MappedEntity) -> None:
        self.coordinator = coordinator
        self.entry = entry
        self.ent = ent

        self._attr_unique_id = f"{entry.entry_id}:{ent.key}"
        self._attr_name = ent.name
        self._attr_icon = ent.icon

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
    def is_on(self):
        v = self.coordinator.data.get(self.ent.key)
        return None if v is None else bool(v)

    async def async_turn_on(self, **kwargs) -> None:
        await self._write(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._write(False)

    async def _write(self, state: bool) -> None:
        w = self.ent.write
        if not w:
            return

        if w["type"] == "coil":
            await self.coordinator.async_write_coil(w["address"], state)
            return

        # Switch on holding: write 0/1
        await self.coordinator.async_write_holding(
            address=w["address"],
            data_type=w.get("data_type", "uint16"),
            value=1 if state else 0,
            scale=w.get("scale"),
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))
