from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ModbusMappedCoordinator, MappedEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    coordinator: ModbusMappedCoordinator = hass.data[DOMAIN][entry.entry_id]
    ents = [e for e in coordinator.mapping.entities if e.platform == "number"]
    async_add_entities([MappedNumber(coordinator, entry, e) for e in ents])


def _to_float(v, default: float | None = None) -> float | None:
    if v is None:
        return default
    try:
        return float(v)
    except Exception:
        return default


def _get_min(ent: MappedEntity) -> float | None:
    v = getattr(ent, "minimum", None)
    if v is None:
        v = getattr(ent, "min", None)
    return _to_float(v, None)


def _get_max(ent: MappedEntity) -> float | None:
    v = getattr(ent, "maximum", None)
    if v is None:
        v = getattr(ent, "max", None)
    return _to_float(v, None)


class MappedNumber(CoordinatorEntity[ModbusMappedCoordinator], NumberEntity):
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

        description = getattr(ent, "description", None)
        if description:
            self._attr_entity_description = description

        mn = _get_min(ent)
        mx = _get_max(ent)
        if mn is not None:
            self._attr_native_min_value = mn
        if mx is not None:
            self._attr_native_max_value = mx

        step_v = getattr(ent, "step", None)
        self._attr_native_step = _to_float(step_v, 1.0) or 1.0

        self._attr_extra_state_attributes = {"key": ent.key}
        if description:
            self._attr_extra_state_attributes["description"] = description
        if mn is not None:
            self._attr_extra_state_attributes["minimum"] = mn
        if mx is not None:
            self._attr_extra_state_attributes["maximum"] = mx

    @property
    def native_value(self) -> float | None:
        v = self.coordinator.data.get(self._ent.key)
        if v is None:
            return None
        try:
            return float(v)
        except Exception:
            return None

    async def async_set_native_value(self, value: float) -> None:
        w = self._ent.write
        if not w:
            return

        address = int(w["address"])
        data_type = str(w.get("data_type", "int16"))
        scale = w.get("scale", None)

        await self.coordinator.async_write_holding(
            address=address,
            data_type=data_type,
            value=value,
            scale=scale,
        )
