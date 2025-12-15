from __future__ import annotations

import asyncio
import logging
import os
import struct
from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import yaml as ha_yaml

from .const import *
from .modbus_client import ModbusClientWrapper, TcpParams, RtuParams

_LOGGER = logging.getLogger(__name__)


@dataclass
class MappedEntity:
    platform: str
    key: str
    name: str

    read: dict | None
    write: dict | None

    unit: str | None = None
    icon: str | None = None
    device_class: str | None = None
    state_class: str | None = None

    options: list | None = None
    min: float | None = None
    max: float | None = None
    step: float | None = None
    press_value: int | None = None


def _base_dir() -> str:
    return os.path.dirname(__file__)


def _mappings_dir() -> str:
    return os.path.join(_base_dir(), "mappings")


def list_mapping_files() -> list[str]:
    mdir = _mappings_dir()
    if not os.path.isdir(mdir):
        return []
    files: list[str] = []
    for f in os.listdir(mdir):
        fl = f.lower()
        if fl.endswith(".yaml") or fl.endswith(".yml"):
            files.append(f)
    files.sort()
    return files


def _require_dict(value: Any, what: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"Mapping YAML: '{what}' muss ein Mapping/Dict sein.")
    return value


def _require_list(value: Any, what: str) -> list:
    if not isinstance(value, list):
        raise ValueError(f"Mapping YAML: '{what}' muss eine Liste sein.")
    return value


def _parse_mapping_data(data: Any) -> tuple[dict, list[MappedEntity]]:
    data = _require_dict(data, "root")

    device = _require_dict(data.get("device", {}), "device")
    entities_raw = _require_list(data.get("entities", []), "entities")

    entities: list[MappedEntity] = []
    for e in entities_raw:
        e = _require_dict(e, "entities[]")

        platform = e["platform"]
        key = e["key"]
        name = e.get("name", key)

        ent = MappedEntity(
            platform=platform,
            key=key,
            name=name,
            read=e.get("read"),
            write=e.get("write"),
            unit=e.get("unit"),
            icon=e.get("icon"),
            device_class=e.get("device_class"),
            state_class=e.get("state_class"),
            options=e.get("options"),
            min=e.get("min"),
            max=e.get("max"),
            step=e.get("step"),
            press_value=e.get("press_value"),
        )
        entities.append(ent)

    return device, entities


def load_mapping_sync(filename: str) -> tuple[dict, list[MappedEntity]]:
    """
    Synchronous mapping loader (blocking file I/O).
    MUST be called from executor.
    """
    path = os.path.join(_mappings_dir(), filename)
    if not os.path.exists(path):
        raise ValueError(f"Mapping-Datei nicht gefunden: {path}")

    data = ha_yaml.load_yaml(path)  # uses open() -> blocking
    return _parse_mapping_data(data)


def _decode_16_32(dtype: str, regs: list[int], word_order: str) -> int | float:
    regs = [int(r) & 0xFFFF for r in regs]

    if word_order == "BA" and len(regs) == 2:
        regs = [regs[1], regs[0]]

    if dtype == "uint16":
        return regs[0]
    if dtype == "int16":
        return struct.unpack(">h", struct.pack(">H", regs[0]))[0]

    raw = ((regs[0] << 16) | regs[1]) & 0xFFFFFFFF
    if dtype == "uint32":
        return raw
    if dtype == "int32":
        return struct.unpack(">i", struct.pack(">I", raw))[0]
    if dtype == "float32":
        return struct.unpack(">f", struct.pack(">I", raw))[0]

    return regs[0]


class ModbusMappedCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass, entry):
        self.hass = hass
        self.entry = entry

        self._lock = asyncio.Lock()
        self._connected = False

        # Mapping lazy-load
        self._mapping_file = entry.data[CONF_MAPPING]
        self._mapping_loaded = False

        self.device: dict = {}
        self.entities: list[MappedEntity] = []

        # Backwards compatible view for your existing platform files
        self.mapping = SimpleNamespace(
            entities=self.entities,
            device_name="Modbus Device",
            manufacturer=None,
            model=None,
        )

        self._slave = int(entry.data[CONF_SLAVE_ID])

        transport = entry.data[CONF_TRANSPORT]
        tcp = rtu = None
        if transport == "tcp":
            tcp = TcpParams(entry.data[CONF_HOST], int(entry.data[CONF_PORT]))
        else:
            rtu = RtuParams(
                entry.data[CONF_PORT_DEVICE],
                int(entry.data[CONF_BAUDRATE]),
                int(entry.data[CONF_BYTESIZE]),
                str(entry.data[CONF_PARITY]),
                int(entry.data[CONF_STOPBITS]),
            )

        self.client = ModbusClientWrapper(transport, tcp, rtu)

        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=int(entry.data[CONF_SCAN_INTERVAL])),
        )

    async def async_close(self) -> None:
        await self.hass.async_add_executor_job(self.client.close)

    async def _ensure(self) -> None:
        if self._connected:
            return
        ok = await self.hass.async_add_executor_job(self.client.connect)
        if not ok:
            raise UpdateFailed("Connect failed")
        self._connected = True

    async def _drop(self) -> None:
        await self.hass.async_add_executor_job(self.client.close)
        self._connected = False

    async def _ensure_mapping_loaded(self) -> None:
        if self._mapping_loaded:
            return

        device, entities = await self.hass.async_add_executor_job(load_mapping_sync, self._mapping_file)
        self.device = device
        self.entities = entities

        self.mapping.entities = self.entities
        self.mapping.device_name = device.get("name", "Modbus Device")
        self.mapping.manufacturer = device.get("manufacturer")
        self.mapping.model = device.get("model")

        self._mapping_loaded = True

    async def _async_update_data(self) -> dict[str, Any]:
        await self._ensure_mapping_loaded()

        async with self._lock:
            last: Exception | None = None
            for _ in range(2):
                try:
                    await self._ensure()
                    return await self._read_all()
                except Exception as e:
                    last = e
                    await self._drop()
            raise UpdateFailed(str(last) if last else "Unknown update error")

    async def _read_all(self) -> dict[str, Any]:
        data: dict[str, Any] = {}

        for ent in self.entities:
            r = ent.read
            if not r:
                continue

            reg_type = str(r.get("type", "holding"))
            addr = int(r["address"])

            if reg_type == "coil":
                rr = await self.hass.async_add_executor_job(
                    self.client.read_coils, addr, 1, self._slave
                )
                data[ent.key] = bool(rr.bits[0])
                continue

            if reg_type == "discrete":
                rr = await self.hass.async_add_executor_job(
                    self.client.read_discrete_inputs, addr, 1, self._slave
                )
                data[ent.key] = bool(rr.bits[0])
                continue

            dtype = str(r.get("data_type", "uint16"))
            word_order = str(r.get("word_order", "AB"))
            bit = r.get("bit", None)

            count = 2 if dtype.endswith("32") else 1

            if reg_type == "holding":
                rr = await self.hass.async_add_executor_job(
                    self.client.read_holding_registers, addr, count, self._slave
                )
            elif reg_type == "input":
                rr = await self.hass.async_add_executor_job(
                    self.client.read_input_registers, addr, count, self._slave
                )
            else:
                _LOGGER.debug("Unknown read.type '%s' for key=%s (skipping)", reg_type, ent.key)
                continue

            regs = rr.registers

            if bit is not None:
                raw16 = int(regs[0]) & 0xFFFF
                data[ent.key] = bool((raw16 >> int(bit)) & 1)
                continue

            val = _decode_16_32(dtype, regs, word_order)

            scale = r.get("scale")
            if scale is not None:
                try:
                    val = float(val) * float(scale)
                except Exception:
                    pass

            data[ent.key] = val

        return data

    # ---------------------------------------------------------------------
    # Backwards-compatible API expected by your platform files
    # ---------------------------------------------------------------------

    async def async_write_holding(
        self,
        address: int,
        data_type: str,
        value: float | int,
        scale: float | None,
    ) -> None:
        dummy = MappedEntity(
            platform="number",
            key=f"_write_{address}",
            name=f"_write_{address}",
            read=None,
            write={
                "type": "holding",
                "address": int(address),
                "data_type": str(data_type),
                "scale": scale,
            },
        )
        await self.write_holding(dummy, value)

    # ---------------------------------------------------------------------
    # Preferred entity-based write API
    # ---------------------------------------------------------------------

    async def write_holding(self, ent: MappedEntity, value) -> None:
        """
        Writes holding register(s).
        IMPORTANT: Must NOT call async_request_refresh() while holding self._lock
        (would deadlock by re-entering _async_update_data()).
        """
        w = ent.write
        if not w:
            return

        w_type = str(w.get("type", "holding"))
        if w_type != "holding":
            raise UpdateFailed(f"write.type '{w_type}' wird derzeit nicht unterstützt")

        addr = int(w["address"])
        refresh_needed = False

        async with self._lock:
            last: Exception | None = None
            for _ in range(2):
                try:
                    await self._ensure()

                    if "bit" in w:
                        bit = int(w["bit"])
                        rr = await self.hass.async_add_executor_job(
                            self.client.read_holding_registers, addr, 1, self._slave
                        )
                        cur = int(rr.registers[0]) & 0xFFFF
                        if bool(value):
                            cur |= (1 << bit)
                        else:
                            cur &= ~(1 << bit)
                        await self.hass.async_add_executor_job(
                            self.client.write_register, addr, cur, self._slave
                        )
                    else:
                        scale = w.get("scale")
                        v = value
                        if scale is not None and float(scale) != 0.0:
                            v = float(value) / float(scale)

                        await self.hass.async_add_executor_job(
                            self.client.write_register, addr, int(v), self._slave
                        )

                    refresh_needed = True
                    break

                except Exception as ex:
                    last = ex
                    await self._drop()

            if not refresh_needed:
                raise UpdateFailed(str(last) if last else "Write failed")

        # OUTSIDE the lock -> no deadlock
        # Schedule refresh in background; UI write should return fast.
        self.hass.async_create_task(self.async_request_refresh())
