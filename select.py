from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ModbusMappedCoordinator, MappedEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    coordinator: ModbusMappedCoordinator = hass.data[DOMAIN][entry.entry_id]
    ents = [e for e in coordinator.mapping.entities if e.platform == "select"]
    async_add_entities([MappedSelect(coordinator, entry, e) for e in ents])


def _normalize_options(raw) -> list[tuple[str, int]]:
    """
    Returns list of (label, value).
    Accepts:
      - [{"label":"A","value":1}, ...]
      - ["A","B"]  (value = index)
    """
    out: list[tuple[str, int]] = []
    if not raw:
        return out

    if isinstance(raw, list):
        for idx, item in enumerate(raw):
            if isinstance(item, dict):
                label = item.get("label")
                value = item.get("value")
                if isinstance(label, str) and isinstance(value, int):
                    out.append((label, value))
            elif isinstance(item, str):
                out.append((item, idx))
    return out


class MappedSelect(CoordinatorEntity[ModbusMappedCoordinator], SelectEntity):
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

        self._options = _normalize_options(getattr(ent, "options", None))
        self._attr_options = [lbl for (lbl, _val) in self._options]

        self._attr_extra_state_attributes = {"key": ent.key}
        if description:
            self._attr_extra_state_attributes["description"] = description

    @property
    def current_option(self) -> str | None:
        v = self.coordinator.data.get(self._ent.key)
        if v is None:
            return None
        try:
            iv = int(v)
        except Exception:
            return None

        for lbl, val in self._options:
            if val == iv:
                return lbl
        return None

    async def async_select_option(self, option: str) -> None:
        # Map label -> value
        val = None
        for lbl, v in self._options:
            if lbl == option:
                val = v
                break
        if val is None:
            return

        # Requires write section
        if getattr(self._ent, "write", None):
            await self.coordinator.write_holding(self._ent, val)
