from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ModbusMappedCoordinator, MappedEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: ModbusMappedCoordinator = hass.data[DOMAIN][entry.entry_id]
    ents = [e for e in coordinator.mapping.entities if e.platform == "select"]
    async_add_entities([MappedSelect(coordinator, entry, e) for e in ents])


class MappedSelect(SelectEntity):
    def __init__(self, coordinator: ModbusMappedCoordinator, entry: ConfigEntry, ent: MappedEntity) -> None:
        self.coordinator = coordinator
        self.entry = entry
        self.ent = ent

        self._attr_unique_id = f"{entry.entry_id}:{ent.key}"
        self._attr_name = ent.name
        self._attr_icon = ent.icon

        self._opts = getattr(ent, "options", [])
        self._attr_options = [o["label"] for o in self._opts]

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
    def current_option(self):
        raw = self.coordinator.data.get(self.ent.key)
        if raw is None:
            return None
        for o in self._opts:
            if int(o["value"]) == int(raw):
                return o["label"]
        return None

    async def async_select_option(self, option: str) -> None:
        w = self.ent.write
        if not w:
            return
        match = next((o for o in self._opts if o["label"] == option), None)
        if match is None:
            return
        await self.coordinator.async_write_holding(
            address=w["address"],
            data_type=w.get("data_type", "uint16"),
            value=int(match["value"]),
            scale=w.get("scale"),
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))
