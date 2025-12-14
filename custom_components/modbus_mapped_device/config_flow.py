from __future__ import annotations

import os
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers import selector

from .const import (
    DOMAIN,
    CONF_TRANSPORT, CONF_MAPPING,
    CONF_HOST, CONF_PORT,
    CONF_PORT_DEVICE, CONF_BAUDRATE, CONF_BYTESIZE, CONF_PARITY, CONF_STOPBITS,
    CONF_SLAVE_ID, CONF_SCAN_INTERVAL,
    DEFAULT_TCP_PORT, DEFAULT_SLAVE_ID, DEFAULT_SCAN_INTERVAL,
)
from .coordinator import list_mapping_files
from .modbus_client import ModbusClientWrapper, TcpParams, RtuParams

TRANSPORTS = ["tcp", "rtu"]
PARITIES = ["N", "E", "O"]
BYTESIZES = [5, 6, 7, 8]
STOPBITS = [1, 2]

def _mapping_selector() -> selector.SelectSelector:
    files = list_mapping_files()
    options = [{"value": f, "label": f} for f in files] if files else []
    return selector.SelectSelector(selector.SelectSelectorConfig(options=options, mode=selector.SelectSelectorMode.DROPDOWN))

class ModbusMappedDeviceConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._transport: str | None = None
        self._data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            self._transport = user_input[CONF_TRANSPORT]
            self._data.update(user_input)
            if self._transport == "tcp":
                return await self.async_step_tcp()
            return await self.async_step_rtu()

        schema = vol.Schema({
            vol.Required(CONF_TRANSPORT): vol.In(TRANSPORTS),
        })
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_tcp(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data.update(user_input)
            ok = await self._async_test_connection(self.hass, self._data)
            if not ok:
                errors["base"] = "cannot_connect"
            else:
                return await self.async_step_mapping()

        schema = vol.Schema({
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_PORT, default=DEFAULT_TCP_PORT): int,
            vol.Required(CONF_SLAVE_ID, default=DEFAULT_SLAVE_ID): vol.All(int, vol.Range(min=0, max=247)),
            vol.Required(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(int, vol.Range(min=1, max=1440)),
        })
        return self.async_show_form(step_id="tcp", data_schema=schema, errors=errors)

    async def async_step_rtu(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data.update(user_input)
            ok = await self._async_test_connection(self.hass, self._data)
            if not ok:
                errors["base"] = "cannot_connect"
            else:
                return await self.async_step_mapping()

        schema = vol.Schema({
            vol.Required(CONF_PORT_DEVICE): str,  # e.g. /dev/ttyUSB0
            vol.Required(CONF_BAUDRATE, default=9600): int,
            vol.Required(CONF_BYTESIZE, default=8): vol.In(BYTESIZES),
            vol.Required(CONF_PARITY, default="N"): vol.In(PARITIES),
            vol.Required(CONF_STOPBITS, default=1): vol.In(STOPBITS),
            vol.Required(CONF_SLAVE_ID, default=DEFAULT_SLAVE_ID): vol.All(int, vol.Range(min=0, max=247)),
            vol.Required(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(int, vol.Range(min=1, max=1440)),
        })
        return self.async_show_form(step_id="rtu", data_schema=schema, errors=errors)

    async def async_step_mapping(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        files = list_mapping_files()
        if not files:
            errors["base"] = "no_mapping_files"

        if user_input is not None and not errors:
            self._data.update(user_input)
            title = f"Modbus mapped ({self._data[CONF_MAPPING]})"
            return self.async_create_entry(title=title, data=self._data)

        schema = vol.Schema({
            vol.Required(CONF_MAPPING): _mapping_selector(),
        })
        return self.async_show_form(step_id="mapping", data_schema=schema, errors=errors)

    async def _async_test_connection(self, hass: HomeAssistant, data: dict[str, Any]) -> bool:
        transport = data[CONF_TRANSPORT]

        tcp = None
        rtu = None
        if transport == "tcp":
            tcp = TcpParams(host=data[CONF_HOST], port=int(data[CONF_PORT]))
        else:
            rtu = RtuParams(
                port=data[CONF_PORT_DEVICE],
                baudrate=int(data[CONF_BAUDRATE]),
                bytesize=int(data[CONF_BYTESIZE]),
                parity=str(data[CONF_PARITY]),
                stopbits=int(data[CONF_STOPBITS]),
            )

        client = ModbusClientWrapper(transport=transport, tcp=tcp, rtu=rtu)
        ok = await hass.async_add_executor_job(client.connect)
        await hass.async_add_executor_job(client.close)
        return ok
