from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ModbusMappedCoordinator, MappedEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    coordinator: ModbusMappedCoordinator = hass.data[DOMAIN][entry.entry_id]
    ents = [e for e in coordinator.mapping.entities if e.platform == "sensor"]
    async_add_entities([MappedSensor(coordinator, entry, e) for e in ents])


def _get_min(ent: MappedEntity) -> float | None:
    v = getattr(ent, "minimum", None)
    if v is None:
        v = getattr(ent, "min", None)
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


def _get_max(ent: MappedEntity) -> float | None:
    v = getattr(ent, "maximum", None)
    if v is None:
        v = getattr(ent, "max", None)
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


class MappedSensor(CoordinatorEntity[ModbusMappedCoordinator], SensorEntity):
    def __init__(self, coordinator: ModbusMappedCoordinator, entry: ConfigEntry, ent: MappedEntity) -> None:
        super().__init__(coordinator)

        self._entry = entry
        self._ent = ent

        self._attr_unique_id = f"{entry.entry_id}:{ent.key}"
        self._attr_name = ent.name

        unit = getattr(ent, "unit", None)
        if unit:
            self._attr_native_unit_of_measurement = unit

        icon = getattr(ent, "icon", None)
        if icon:
            self._attr_icon = icon

        device_class = getattr(ent, "device_class", None)
        if device_class:
            self._attr_device_class = device_class

        state_class = getattr(ent, "state_class", None)
        if state_class:
            self._attr_state_class = state_class

        description = getattr(ent, "description", None)
        if description:
            self._attr_entity_description = description

        # Useful metadata as attributes (optional)
        self._attr_extra_state_attributes = {
            "key": ent.key,
        }
        mn = _get_min(ent)
        mx = _get_max(ent)
        if mn is not None:
            self._attr_extra_state_attributes["minimum"] = mn
        if mx is not None:
            self._attr_extra_state_attributes["maximum"] = mx
        if description:
            self._attr_extra_state_attributes["description"] = description

    @property
    def native_value(self) -> Any:
        return self.coordinator.data.get(self._ent.key)
