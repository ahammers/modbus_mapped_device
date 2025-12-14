from __future__ import annotations

import asyncio
import os
import struct
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import yaml as ha_yaml

from .const import *
from .modbus_client import ModbusClientWrapper, TcpParams, RtuParams


@dataclass
class MappedEntity:
    platform: str
    key: str
    name: str
    read: dict | None
    write: dict | None
    unit: str | None = None
    icon: str | None = None
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


def load_mapping(filename: str) -> tuple[dict, list[MappedEntity]]:
    path = os.path.join(_mappings_dir(), filename)
    if not os.path.exists(path):
        raise ValueError(f"Mapping-Datei nicht gefunden: {path}")

    data = ha_yaml.load_yaml(path)
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
            options=e.get("options"),
            min=e.get("min"),
            max=e.get("max"),
            step=e.get("step"),
            press_value=e.get("press_value"),
        )
        entities.append(ent)

    return device, entities


class ModbusMappedCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass, entry):
        self.hass = hass
        self.entry = entry
        self._lock = asyncio.Lock()
        self._connected = False

        device, self.entities = load_mapping(entry.data[CONF_MAPPING])
        self.device = device
        self._slave = entry.data[CONF_SLAVE_ID]

        transport = entry.data[CONF_TRANSPORT]
        tcp = rtu = None
        if transport == "tcp":
            tcp = TcpParams(entry.data[CONF_HOST], entry.data[CONF_PORT])
        else:
            rtu = RtuParams(
                entry.data[CONF_PORT_DEVICE],
                entry.data[CONF_BAUDRATE],
                entry.data[CONF_BYTESIZE],
                entry.data[CONF_PARITY],
                entry.data[CONF_STOPBITS],
            )

        self.client = ModbusClientWrapper(transport, tcp, rtu)

        super().__init__(
            hass,
            name=DOMAIN,
            update_interval=timedelta(minutes=entry.data[CONF_SCAN_INTERVAL]),
        )

    async def _ensure(self):
        if not self._connected:
            if not await self.hass.async_add_executor_job(self.client.connect):
                raise UpdateFailed("Connect failed")
            self._connected = True

    async def _drop(self):
        await self.hass.async_add_executor_job(self.client.close)
        self._connected = False

    async def _async_update_data(self):
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

    async def _read_all(self):
        data: dict[str, Any] = {}

        for e in self.entities:
            if not e.read:
                continue

            r = e.read
            reg_type = r.get("type", "holding")
            addr = int(r["address"])
            dtype = r.get("data_type", "uint16")
            word_order = r.get("word_order", "AB")  # AB default, BA swapped

            # NOTE: In diesem vereinfachten Reader lesen wir Holding-Register.
            # Wenn du input/coil/discrete brauchst, kann man das analog erweitern.
            count = 2 if str(dtype).endswith("32") else 1

            rr = await self.hass.async_add_executor_job(
                self.client.read_holding_registers,
                addr,
                count,
                self._slave,
            )

            regs = rr.registers
            if word_order == "BA" and len(regs) == 2:
                regs = [regs[1], regs[0]]

            if dtype == "uint16":
                val = regs[0]
            elif dtype == "int16":
                val = struct.unpack(">h", struct.pack(">H", regs[0]))[0]
            elif dtype == "uint32":
                val = (regs[0] << 16) | regs[1]
            elif dtype == "int32":
                val = struct.unpack(">i", struct.pack(">I", (regs[0] << 16) | regs[1]))[0]
            elif dtype == "float32":
                val = struct.unpack(">f", struct.pack(">I", (regs[0] << 16) | regs[1]))[0]
            else:
                val = regs[0]

            # optional scale im read
            scale = r.get("scale")
            if scale is not None:
                try:
                    val = float(val) * float(scale)
                except Exception:
                    pass

            data[e.key] = val

        return data

    async def write_holding(self, e: MappedEntity, value):
        w = e.write
        if not w:
            return

        addr = int(w["address"])

        async with self._lock:
            last: Exception | None = None
            for _ in range(2):
                try:
                    await self._ensure()

                    # Holding-bit-switch: read-modify-write
                    if "bit" in w:
                        bit = int(w["bit"])
                        rr = await self.hass.async_add_executor_job(
                            self.client.read_holding_registers, addr, 1, self._slave
                        )
                        cur = int(rr.registers[0]) & 0xFFFF
                        if value:
                            cur = cur | (1 << bit)
                        else:
                            cur = cur & ~(1 << bit)
                        await self.hass.async_add_executor_job(
                            self.client.write_register, addr, cur, self._slave
                        )
                    else:
                        # optional scale beim write
                        scale = w.get("scale")
                        v = value
                        if scale is not None and float(scale) != 0.0:
                            v = float(value) / float(scale)

                        await self.hass.async_add_executor_job(
                            self.client.write_register, addr, int(v), self._slave
                        )

                    await self.async_request_refresh()
                    return

                except Exception as ex:
                    last = ex
                    await self._drop()

            raise UpdateFailed(str(last) if last else "Write failed")
