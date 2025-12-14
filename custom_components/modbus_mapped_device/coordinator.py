from __future__ import annotations

import json
import os
import struct
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    CONF_TRANSPORT, CONF_MAPPING,
    CONF_HOST, CONF_PORT,
    CONF_PORT_DEVICE, CONF_BAUDRATE, CONF_BYTESIZE, CONF_PARITY, CONF_STOPBITS,
    CONF_SLAVE_ID, CONF_SCAN_INTERVAL,
)
from .modbus_client import ModbusClientWrapper, TcpParams, RtuParams

RegType = Literal["holding", "input", "coil", "discrete"]
DataType = Literal["uint16", "int16", "uint32", "int32", "float32"]

@dataclass(frozen=True)
class MappedEntity:
    platform: Literal["sensor", "binary_sensor"]
    key: str
    name: str
    reg_type: RegType
    address: int
    data_type: DataType | None = None
    unit: str | None = None
    scale: float | None = None
    device_class: str | None = None
    state_class: str | None = None
    icon: str | None = None

    # binary-only
    bit: int | None = None

@dataclass(frozen=True)
class MappingDefinition:
    device_name: str
    manufacturer: str | None
    model: str | None
    entities: list[MappedEntity]

def _integration_dir() -> str:
    return os.path.dirname(__file__)

def list_mapping_files() -> list[str]:
    mdir = os.path.join(_integration_dir(), "mappings")
    if not os.path.isdir(mdir):
        return []
    files = [f for f in os.listdir(mdir) if f.endswith(".json")]
    files.sort()
    return files

def load_mapping(filename: str) -> MappingDefinition:
    path = os.path.join(_integration_dir(), "mappings", filename)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    entities: list[MappedEntity] = []
    for e in data.get("entities", []):
        entities.append(MappedEntity(
            platform=e["platform"],
            key=e["key"],
            name=e.get("name", e["key"]),
            reg_type=e["register"]["type"],
            address=int(e["register"]["address"]),
            data_type=e.get("data_type"),
            unit=e.get("unit"),
            scale=e.get("scale"),
            device_class=e.get("device_class"),
            state_class=e.get("state_class"),
            icon=e.get("icon"),
            bit=e.get("bit"),
        ))

    return MappingDefinition(
        device_name=data.get("device", {}).get("name", "Modbus Device"),
        manufacturer=data.get("device", {}).get("manufacturer"),
        model=data.get("device", {}).get("model"),
        entities=entities,
    )

def _regs_needed_for_entity(ent: MappedEntity) -> int:
    if ent.reg_type in ("coil", "discrete"):
        return 1
    # 16-bit reg based
    if ent.data_type in ("uint16", "int16"):
        return 1
    if ent.data_type in ("uint32", "int32", "float32"):
        return 2
    return 1

def _decode_registers(data_type: DataType, regs: list[int]) -> float | int:
    # Big-endian word order, typical Modbus.
    if data_type == "uint16":
        return regs[0] & 0xFFFF
    if data_type == "int16":
        v = regs[0] & 0xFFFF
        return v - 0x10000 if v & 0x8000 else v

    hi = regs[0] & 0xFFFF
    lo = regs[1] & 0xFFFF
    raw = (hi << 16) | lo

    if data_type == "uint32":
        return raw
    if data_type == "int32":
        return raw - 0x100000000 if raw & 0x80000000 else raw
    if data_type == "float32":
        b = struct.pack(">I", raw)
        return struct.unpack(">f", b)[0]

    return regs[0]

class ModbusMappedCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry

        scan_min = int(entry.data.get(CONF_SCAN_INTERVAL, 5))
        super().__init__(
            hass,
            logger=__import__("logging").getLogger(__name__),
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(minutes=scan_min),
        )

        self.mapping = load_mapping(entry.data[CONF_MAPPING])

        transport = entry.data[CONF_TRANSPORT]
        tcp = None
        rtu = None
        if transport == "tcp":
            tcp = TcpParams(
                host=entry.data[CONF_HOST],
                port=int(entry.data[CONF_PORT]),
            )
        else:
            rtu = RtuParams(
                port=entry.data[CONF_PORT_DEVICE],
                baudrate=int(entry.data[CONF_BAUDRATE]),
                bytesize=int(entry.data[CONF_BYTESIZE]),
                parity=str(entry.data[CONF_PARITY]),
                stopbits=int(entry.data[CONF_STOPBITS]),
            )

        self._slave_id = int(entry.data[CONF_SLAVE_ID])
        self._client = ModbusClientWrapper(transport=transport, tcp=tcp, rtu=rtu)

    async def async_close(self) -> None:
        await self.hass.async_add_executor_job(self._client.close)

    async def _async_update_data(self) -> dict[str, Any]:
        # Connect (sync) inside executor
        ok = await self.hass.async_add_executor_job(self._client.connect)
        if not ok:
            raise UpdateFailed("Modbus connection failed")

        try:
            return await self._read_all()
        except Exception as e:
            raise UpdateFailed(str(e)) from e

    async def _read_all(self) -> dict[str, Any]:
        # Group reads per reg_type and contiguous ranges (simple strategy)
        # Output dict: entity_key -> value
        result: dict[str, Any] = {}

        # Create per type list
        by_type: dict[RegType, list[MappedEntity]] = {"holding": [], "input": [], "coil": [], "discrete": []}
        for ent in self.mapping.entities:
            by_type[ent.reg_type].append(ent)

        for reg_type, ents in by_type.items():
            if not ents:
                continue
            ents_sorted = sorted(ents, key=lambda e: e.address)

            # Build ranges: contiguous enough, also consider needed length
            ranges: list[tuple[int, int, list[MappedEntity]]] = []
            cur_start = None
            cur_end = None
            cur_ents: list[MappedEntity] = []

            for ent in ents_sorted:
                need = _regs_needed_for_entity(ent)
                start = ent.address
                end = ent.address + need - 1

                if cur_start is None:
                    cur_start, cur_end = start, end
                    cur_ents = [ent]
                    continue

                # merge if overlapping/contiguous and range not too big
                if start <= (cur_end + 1) and (end - cur_start) <= 120:
                    cur_end = max(cur_end, end)
                    cur_ents.append(ent)
                else:
                    ranges.append((cur_start, cur_end, cur_ents))
                    cur_start, cur_end = start, end
                    cur_ents = [ent]

            if cur_start is not None:
                ranges.append((cur_start, cur_end, cur_ents))

            # Read each range
            for start, end, range_ents in ranges:
                count = end - start + 1

                if reg_type == "holding":
                    rr = await self.hass.async_add_executor_job(
                        self._client.read_holding_registers, start, count, self._slave_id
                    )
                    regs = getattr(rr, "registers", None)
                elif reg_type == "input":
                    rr = await self.hass.async_add_executor_job(
                        self._client.read_input_registers, start, count, self._slave_id
                    )
                    regs = getattr(rr, "registers", None)
                elif reg_type == "coil":
                    rr = await self.hass.async_add_executor_job(
                        self._client.read_coils, start, count, self._slave_id
                    )
                    regs = getattr(rr, "bits", None)
                else:
                    rr = await self.hass.async_add_executor_job(
                        self._client.read_discrete_inputs, start, count, self._slave_id
                    )
                    regs = getattr(rr, "bits", None)

                if regs is None:
                    # pymodbus sets .isError() on errors; keep it generic
                    raise RuntimeError(f"Modbus read failed ({reg_type}) at {start} len {count}")

                # extract each entity from regs
                for ent in range_ents:
                    offset = ent.address - start

                    if ent.reg_type in ("coil", "discrete"):
                        val = bool(regs[offset])
                        result[ent.key] = val
                        continue

                    dt = ent.data_type or "uint16"
                    need = _regs_needed_for_entity(ent)
                    slice_regs = [int(r) for r in regs[offset: offset + need]]
                    val_num = _decode_registers(dt, slice_regs)

                    if ent.scale is not None:
                        val_num = float(val_num) * float(ent.scale)

                    # binary_sensor from register/bit optional
                    if ent.platform == "binary_sensor" and ent.bit is not None:
                        # interpret first reg as uint16 bitfield
                        raw16 = int(slice_regs[0]) & 0xFFFF
                        val = bool((raw16 >> int(ent.bit)) & 1)
                        result[ent.key] = val
                    else:
                        result[ent.key] = val_num

        return result
