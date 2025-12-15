from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import (
    DOMAIN,
    CONF_TRANSPORT,
    CONF_MAPPING,
    CONF_HOST,
    CONF_PORT,
    CONF_PORT_DEVICE,
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_PARITY,
    CONF_STOPBITS,
    CONF_SLAVE_ID,
    CONF_SCAN_INTERVAL,
    DEFAULT_TCP_PORT,
    DEFAULT_SLAVE_ID,
    DEFAULT_SCAN_INTERVAL,  # now interpreted as SECONDS (default should be 60)
)
from .coordinator import list_mapping_files

TRANSPORTS = ["tcp", "rtu"]


def _mapping_selector() -> selector.SelectSelector:
    files = list_mapping_files()
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=files,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


def _scan_interval_selector(default_value: int) -> selector.NumberSelector:
    # seconds, not minutes
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=1,
            max=3600,
            step=1,
            mode=selector.NumberSelectorMode.BOX,
            unit_of_measurement="s",
        )
    )


class ModbusMappedDeviceConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    async def async_step_user(self, user_input=None):
        if user_input:
            self.data.update(user_input)
            if user_input[CONF_TRANSPORT] == "tcp":
                return await self.async_step_tcp()
            return await self.async_step_rtu()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_TRANSPORT): vol.In(TRANSPORTS),
                }
            ),
        )

    async def async_step_tcp(self, user_input=None):
        if user_input:
            self.data.update(user_input)
            return await self.async_step_mapping()

        return self.async_show_form(
            step_id="tcp",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Required(CONF_PORT, default=DEFAULT_TCP_PORT): int,
                    vol.Required(CONF_SLAVE_ID, default=DEFAULT_SLAVE_ID): int,
                    # seconds
                    vol.Required(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): _scan_interval_selector(
                        DEFAULT_SCAN_INTERVAL
                    ),
                }
            ),
        )

    async def async_step_rtu(self, user_input=None):
        if user_input:
            self.data.update(user_input)
            return await self.async_step_mapping()

        return self.async_show_form(
            step_id="rtu",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PORT_DEVICE): str,
                    vol.Required(CONF_BAUDRATE, default=9600): int,
                    vol.Required(CONF_BYTESIZE, default=8): vol.In([7, 8]),
                    vol.Required(CONF_PARITY, default="N"): vol.In(["N", "E", "O"]),
                    vol.Required(CONF_STOPBITS, default=1): vol.In([1, 2]),
                    vol.Required(CONF_SLAVE_ID, default=DEFAULT_SLAVE_ID): int,
                    # seconds
                    vol.Required(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): _scan_interval_selector(
                        DEFAULT_SCAN_INTERVAL
                    ),
                }
            ),
        )

    async def async_step_mapping(self, user_input=None):
        files = list_mapping_files()

        if not files:
            return self.async_abort(reason="no_mapping_files")

        if user_input:
            self.data.update(user_input)
            return self.async_create_entry(
                title=f"Modbus ({self.data[CONF_MAPPING]})",
                data=self.data,
            )

        return self.async_show_form(
            step_id="mapping",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MAPPING): _mapping_selector(),
                }
            ),
        )

    async def async_step_options(self, user_input=None):
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        files = list_mapping_files()

        if not files:
            return self.async_abort(reason="no_mapping_files")

        if user_input:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="options",
            data_schema=vol.Schema(
                {
                    # seconds
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=entry.data[CONF_SCAN_INTERVAL],
                    ): _scan_interval_selector(int(entry.data[CONF_SCAN_INTERVAL])),
                    vol.Required(CONF_MAPPING, default=entry.data[CONF_MAPPING]): _mapping_selector(),
                }
            ),
        )
