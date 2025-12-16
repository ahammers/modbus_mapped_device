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

    # NEW: mapping metadata
    description: str | None = None
    minimum: float | None = None
    maximum: float | None = None

    # platform-specific extras
    options: list | None = None
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


def _require_dict(value: Any) -> bool:
    return isinstance(value, dict)


def _require_list(value: Any) -> bool:
    return isinstance(value, list)


def _pos(obj: Any) -> str:
    """
    Best-effort location (line/col) from annotatedyaml objects.
    """
    line = getattr(obj, "__line__", None)
    col = getattr(obj, "__col__", None)
    if line is None:
        line = getattr(obj, "__line", None)
    if col is None:
        col = getattr(obj, "__col", None)

    if isinstance(line, int) and isinstance(col, int):
        return f"line {line + 1}, col {col + 1}"
    if isinstance(line, int):
        return f"line {line + 1}"
    return "unknown position"


def _err(errors: list[str], filename: str, where: str, msg: str, obj_for_pos: Any | None = None) -> None:
    pos = _pos(obj_for_pos) if obj_for_pos is not None else "unknown position"
    errors.append(f"{filename}: {pos}: {where}: {msg}")


def _as_int(errors: list[str], filename: str, where: str, v: Any, obj_for_pos: Any) -> int | None:
    if isinstance(v, bool):
        _err(errors, filename, where, "must be an integer (not bool)", obj_for_pos)
        return None
    if isinstance(v, int):
        return v
    try:
        return int(v)
    except Exception:
        _err(errors, filename, where, f"must be an integer, got {type(v).__name__}", obj_for_pos)
        return None


def _as_float(errors: list[str], filename: str, where: str, v: Any, obj_for_pos: Any) -> float | None:
    if isinstance(v, bool):
        _err(errors, filename, where, "must be a number (not bool)", obj_for_pos)
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)
    except Exception:
        _err(errors, filename, where, f"must be a number, got {type(v).__name__}", obj_for_pos)
        return None


def _as_str(errors: list[str], filename: str, where: str, v: Any, obj_for_pos: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, str):
        return v
    _err(errors, filename, where, f"must be a string, got {type(v).__name__}", obj_for_pos)
    return None


def _validate_read_write(errors: list[str], filename: str, e: dict, section_name: str, allowed_types: set[str]) -> None:
    sec = e.get(section_name)
    if sec is None:
        return
    if not _require_dict(sec):
        _err(errors, filename, f"entities[].{section_name}", "must be a mapping/dict", sec)
        return

    t = sec.get("type")
    if not isinstance(t, str):
        _err(errors, filename, f"entities[].{section_name}.type", "must be a string", sec)
    elif t not in allowed_types:
        _err(errors, filename, f"entities[].{section_name}.type", f"unsupported '{t}' (allowed: {sorted(allowed_types)})", sec)

    if "address" not in sec:
        _err(errors, filename, f"entities[].{section_name}.address", "is required", sec)
    else:
        addr = _as_int(errors, filename, f"entities[].{section_name}.address", sec.get("address"), sec)
        if addr is not None and addr < 0:
            _err(errors, filename, f"entities[].{section_name}.address", "must be >= 0", sec)

    dtype = sec.get("data_type")
    if dtype is not None and not isinstance(dtype, str):
        _err(errors, filename, f"entities[].{section_name}.data_type", "must be a string", sec)

    word_order = sec.get("word_order")
    if word_order is not None:
        if not isinstance(word_order, str) or word_order not in ("AB", "BA"):
            _err(errors, filename, f"entities[].{section_name}.word_order", "must be 'AB' or 'BA'", sec)

    scale = sec.get("scale")
    if scale is not None:
        _as_float(errors, filename, f"entities[].{section_name}.scale", scale, sec)

    bit = sec.get("bit")
    if bit is not None:
        b = _as_int(errors, filename, f"entities[].{section_name}.bit", bit, sec)
        if b is not None and not (0 <= b <= 15):
            _err(errors, filename, f"entities[].{section_name}.bit", "must be in range 0..15", sec)


def _validate_mapping(errors: list[str], filename: str, data: Any) -> None:
    if not _require_dict(data):
        _err(errors, filename, "root", "must be a mapping/dict", data)
        return

    device = data.get("device")
    if device is None or not _require_dict(device):
        _err(errors, filename, "device", "is required and must be a mapping/dict", device if device is not None else data)
    else:
        if "name" not in device:
            _err(errors, filename, "device.name", "is required", device)
        else:
            if not isinstance(device.get("name"), str):
                _err(errors, filename, "device.name", "must be a string", device)

        for k in ("manufacturer", "model"):
            if k in device and device[k] is not None and not isinstance(device[k], str):
                _err(errors, filename, f"device.{k}", "must be a string or null", device)

    entities = data.get("entities")
    if entities is None or not _require_list(entities):
        _err(errors, filename, "entities", "is required and must be a list", entities if entities is not None else data)
        return

    if len(entities) == 0:
        _err(errors, filename, "entities", "must not be empty", entities)

    seen_keys: set[str] = set()
    for idx, e in enumerate(entities):
        where = f"entities[{idx}]"
        if not _require_dict(e):
            _err(errors, filename, where, "must be a mapping/dict", e)
            continue

        platform = e.get("platform")
        if not isinstance(platform, str):
            _err(errors, filename, f"{where}.platform", "is required and must be a string", e)
        key = e.get("key")
        if not isinstance(key, str) or not key:
            _err(errors, filename, f"{where}.key", "is required and must be a non-empty string", e)
        else:
            if key in seen_keys:
                _err(errors, filename, f"{where}.key", f"duplicate key '{key}'", e)
            seen_keys.add(key)

        name = e.get("name")
        if name is not None and not isinstance(name, str):
            _err(errors, filename, f"{where}.name", "must be a string", e)

        # NEW fields
        if "description" in e:
            _as_str(errors, filename, f"{where}.description", e.get("description"), e)
        if "minimum" in e:
            _as_float(errors, filename, f"{where}.minimum", e.get("minimum"), e)
        if "maximum" in e:
            _as_float(errors, filename, f"{where}.maximum", e.get("maximum"), e)

        # Backwards-compat: accept old min/max but warn if both present
        if "min" in e and "minimum" in e:
            _err(errors, filename, where, "both 'min' and 'minimum' present; use only 'minimum'", e)
        if "max" in e and "maximum" in e:
            _err(errors, filename, where, "both 'max' and 'maximum' present; use only 'maximum'", e)

        if "unit" in e and e["unit"] is not None and not isinstance(e["unit"], str):
            _err(errors, filename, f"{where}.unit", "must be a string or null", e)
        if "icon" in e and e["icon"] is not None and not isinstance(e["icon"], str):
            _err(errors, filename, f"{where}.icon", "must be a string or null", e)

        if "device_class" in e and e["device_class"] is not None and not isinstance(e["device_class"], str):
            _err(errors, filename, f"{where}.device_class", "must be a string or null", e)
        if "state_class" in e and e["state_class"] is not None and not isinstance(e["state_class"], str):
            _err(errors, filename, f"{where}.state_class", "must be a string or null", e)

        # number specifics
        if "step" in e and e["step"] is not None:
            _as_float(errors, filename, f"{where}.step", e["step"], e)

        # read/write schema
        _validate_read_write(errors, filename, e, "read", allowed_types={"holding", "input", "coil", "discrete"})
        _validate_read_write(errors, filename, e, "write", allowed_types={"holding"})  # only holding writes supported here

        # platform sanity
        if isinstance(platform, str):
            if platform == "number":
                # require write for number to be settable
                if e.get("write") is None:
                    _err(errors, filename, where, "platform 'number' usually requires 'write' section", e)


def _parse_mapping_data(filename: str, data: Any) -> tuple[dict, list[MappedEntity]]:
    errors: list[str] = []
    _validate_mapping(errors, filename, data)
    if errors:
        raise ValueError("Invalid mapping file:\n- " + "\n- ".join(errors))

    # At this point structure is valid enough to parse
    device = data["device"]
    entities_raw = data["entities"]

    entities: list[MappedEntity] = []
    for e in entities_raw:
        # new fields (prefer minimum/maximum; fall back to min/max)
        minimum = e.get("minimum")
        maximum = e.get("maximum")
        if minimum is None and "min" in e:
            minimum = e.get("min")
        if maximum is None and "max" in e:
            maximum = e.get("max")

        ent = MappedEntity(
            platform=e["platform"],
            key=e["key"],
            name=e.get("name", e["key"]),
            read=e.get("read"),
            write=e.get("write"),
            unit=e.get("unit"),
            icon=e.get("icon"),
            device_class=e.get("device_class"),
            state_class=e.get("state_class"),
            description=e.get("description"),
            minimum=float(minimum) if minimum is not None else None,
            maximum=float(maximum) if maximum is not None else None,
            options=e.get("options"),
            step=float(e["step"]) if e.get("step") is not None else None,
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

    try:
        data = ha_yaml.load_yaml(path)  # uses open() -> blocking
    except Exception as ex:
        # annotatedyaml usually provides line/col in the exception message,
        # but we still wrap it with filename.
        raise ValueError(f"{filename}: YAML parse error: {ex}") from ex

    return _parse_mapping_data(filename, data)


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

        try:
            device, entities = await self.hass.async_add_executor_job(load_mapping_sync, self._mapping_file)
        except Exception as ex:
            # Make the error useful in HA logs/UI
            raise UpdateFailed(str(ex)) from ex

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

    async def write_holding(self, ent: MappedEntity, value) -> None:
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

        self.hass.async_create_task(self.async_request_refresh())
