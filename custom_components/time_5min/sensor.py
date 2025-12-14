from __future__ import annotations

from datetime import timedelta, datetime

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

SCAN_INTERVAL = timedelta(minutes=5)


async def async_setup_platform(
    hass: HomeAssistant,
    config,
    async_add_entities,
    discovery_info=None,
):
    hour = Time5MinHourSensor(hass)
    minute = Time5MinMinuteSensor(hass)
    async_add_entities([hour, minute], update_before_add=True)


class _Time5MinBase(SensorEntity):
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._value: int | None = None
        self._unsub = None

    async def async_added_to_hass(self) -> None:
        # einmal sofort setzen
        self._update_from_now()
        self.async_write_ha_state()

        # dann alle 5 Minuten aktualisieren
        self._unsub = async_track_time_interval(
            self._hass,
            self._handle_interval,
            SCAN_INTERVAL,
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    async def _handle_interval(self, _now: datetime) -> None:
        self._update_from_now()
        self.async_write_ha_state()

    def _update_from_now(self) -> None:
        now = dt_util.now()  # HA-Zeitzone
        self._value = self._compute_value(now)

    def _compute_value(self, now: datetime) -> int:
        raise NotImplementedError

    @property
    def native_value(self) -> int | None:
        return self._value


class Time5MinHourSensor(_Time5MinBase):
    _attr_unique_id = "time_5min_hour"
    _attr_name = "Time 5min Hour"
    _attr_icon = "mdi:clock-time-four-outline"

    def _compute_value(self, now: datetime) -> int:
        return now.hour


class Time5MinMinuteSensor(_Time5MinBase):
    _attr_unique_id = "time_5min_minute"
    _attr_name = "Time 5min Minute"
    _attr_icon = "mdi:clock-time-four-outline"

    def _compute_value(self, now: datetime) -> int:
        return now.minute
