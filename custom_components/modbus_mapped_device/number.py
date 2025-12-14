from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ModbusMappedCoordinator, MappedEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: ModbusMappedCoordinator = hass.data[DOMAIN][entry.entry_id]
    ents = [e for e in coordinator.mapping.entities if e.platform == "number"]
    async_add_entities([MappedNumber(coordinator, entry, e) for e in ents])


class MappedNumber(NumberEntity):
    _attr_mode = "box"

    def __init__(self, coordinator: ModbusMappedCoordinator, entry: ConfigEntry, ent: MappedEntity) -> None:
        self.coordinator = coordinator
        self.entry = entry
        self.ent = ent

        self._attr_unique_id = f"{entry.entry_id}:{ent.key}"
        self._attr_name = ent.name
        self._attr_native_unit_of_measurement = ent.unit
        self._attr_icon = ent.icon

        self._attr_native_min_value = float(getattr(ent, "min", 0))
        self._attr_native_max_value = float(getattr(ent, "max", 100))
        self._attr_native_step = float(getattr(ent, "step", 1))

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
    def native_value(self):
        return self.coordinator.data.get(self.ent.key)

    async def async_set_native_value(self, value: float) -> None:
        w = self.ent.write
        if not w:
            return
        await self.coordinator.async_write_holding(
            address=w["address"],
            data_type=w.get("data_type", "uint16"),
            value=value,
            scale=w.get("scale"),
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))
