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

# Safety limits for Modbus batch reads (keep conservative for RTU)
MAX_REGS_PER_READ = 60          # holding/input registers
MAX_BITS_PER_READ = 200         # coils/discrete bits


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

    description: str | None = None
    minimum: float | None = None
    maximum: float | None = None

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


def _parse_mapping_data(filename: str, data: Any) -> tuple[dict, list[MappedEntity]]:
    # Minimal parsing (assuming your validation layer exists already);
    # keep backwards-compat min/max.
    if not _require_dict(data):
        raise ValueError(f"{filename}: root must be a mapping/dict")

    device = data.get("device")
    entities_raw = data.get("entities")
    if not _require_dict(device):
        raise ValueError(f"{filename}: device must be a mapping/dict")
    if not _require_list(entities_raw):
        raise ValueError(f"{filename}: entities must be a list")

    entities: list[MappedEntity] = []
    for e in entities_raw:
        if not _require_dict(e):
            continue

        minimum = e.get("minimum")
        maximum = e.get("maximum")
        if minimum is None and "min" in e:
            minimum = e.get("min")
        if maximum is None and "max" in e:
            maximum = e.get("max")

        ent = MappedEntity(
            platform=str(e.get("platform")),
            key=str(e.get("key")),
            name=str(e.get("name", e.get("key"))),
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
    path = os.path.join(_mappings_dir(), filename)
    if not os.path.exists(path):
        raise ValueError(f"Mapping-Datei nicht gefunden: {path}")

    try:
        data = ha_yaml.load_yaml(path)
    except Exception as ex:
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


def _rr_is_error(rr: Any) -> bool:
    try:
        return bool(rr.isError())
    except Exception:
        return False


class ModbusMappedCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass, entry):
        self.hass = hass
        self.entry = entry

        self._lock = asyncio.Lock()
        self._connected = False

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
            try:
                await self._ensure()
                return await self._read_all()
            except Exception as e:
                # IMPORTANT: do NOT always drop the connection here.
                # A single register can fail (timeout/illegal address) without meaning the link is dead.
                _LOGGER.warning("Update cycle failed (keeping connection): %s", e, exc_info=True)

                # Only drop on obvious transport-level failures
                if isinstance(e, (OSError, ConnectionError, asyncio.TimeoutError)):
                    _LOGGER.warning("Transport-level error -> dropping connection to force reconnect")
                    await self._drop()

                # Return last known data (keeps entities alive), or empty dict if none yet
                return self.data or {}


    # ---------------------------------------------------------------------
    # Batched reading + per-register error isolation
    # ---------------------------------------------------------------------

    def _iter_reg_entities(self) -> list[tuple[MappedEntity, str, int, str, str, float | None, int | None, int]]:
        """
        Returns normalized list of register read specs:
          (ent, reg_type, address, dtype, word_order, scale, bit, width_regs)
        width_regs is 1 or 2 for 16/32-bit reads.
        """
        out: list[tuple[MappedEntity, str, int, str, str, float | None, int | None, int]] = []
        for ent in self.entities:
            r = ent.read
            if not r or not _require_dict(r):
                continue

            reg_type = str(r.get("type", "holding"))
            if reg_type not in ("holding", "input", "coil", "discrete"):
                continue

            try:
                addr = int(r["address"])
            except Exception:
                continue

            dtype = str(r.get("data_type", "uint16"))
            word_order = str(r.get("word_order", "AB"))
            scale = r.get("scale", None)
            try:
                scale_f = float(scale) if scale is not None else None
            except Exception:
                scale_f = None

            bit = r.get("bit", None)
            try:
                bit_i = int(bit) if bit is not None else None
            except Exception:
                bit_i = None

            width = 2 if dtype.endswith("32") else 1
            out.append((ent, reg_type, addr, dtype, word_order, scale_f, bit_i, width))
        return out

    @staticmethod
    def _group_ranges(items: list[tuple[int, int, Any]], max_span: int) -> list[tuple[int, int, list[Any]]]:
        """
        items: list of (start, end, payload), inclusive end.
        Groups overlapping/adjacent items into ranges, limited by max_span.
        Returns list of (range_start, range_end, payloads)
        """
        if not items:
            return []
        items = sorted(items, key=lambda x: x[0])
        groups: list[tuple[int, int, list[Any]]] = []

        cur_s, cur_e, cur_payloads = items[0][0], items[0][1], [items[0][2]]
        for s, e, payload in items[1:]:
            # try merge
            new_s = cur_s
            new_e = max(cur_e, e)
            span = new_e - new_s + 1

            if s <= cur_e + 1 and span <= max_span:
                cur_e = new_e
                cur_payloads.append(payload)
            else:
                groups.append((cur_s, cur_e, cur_payloads))
                cur_s, cur_e, cur_payloads = s, e, [payload]

        groups.append((cur_s, cur_e, cur_payloads))
        return groups

    async def _read_all(self) -> dict[str, Any]:
        data: dict[str, Any] = {}

        specs = self._iter_reg_entities()

        # Separate by type
        reg_specs = [s for s in specs if s[1] in ("holding", "input")]
        bit_specs = [s for s in specs if s[1] in ("coil", "discrete")]

        # ----------- batch holding/input registers -----------
        # We batch by (reg_type) only; per-entity dtype/scale/word_order are handled when decoding slices.
        for reg_type in ("holding", "input"):
            items: list[tuple[int, int, tuple]] = []
            for ent, rt, addr, dtype, word_order, scale, bit, width in reg_specs:
                if rt != reg_type:
                    continue
                start = addr
                end = addr + width - 1
                items.append((start, end, (ent, addr, dtype, word_order, scale, bit, width)))

            for start, end, payloads in self._group_ranges(items, MAX_REGS_PER_READ):
                count = end - start + 1

                # 1) Try batch read
                rr = None
                batch_ok = False
                try:
                    if reg_type == "holding":
                        rr = await self.hass.async_add_executor_job(
                            self.client.read_holding_registers, start, count, self._slave
                        )
                    else:
                        rr = await self.hass.async_add_executor_job(
                            self.client.read_input_registers, start, count, self._slave
                        )

                    if _rr_is_error(rr):
                        raise RuntimeError(f"Modbus error response: {rr}")
                    batch_ok = True

                except Exception as ex:
                    _LOGGER.warning(
                        "Batch read failed (%s %d..%d, count=%d, slave=%d): %s",
                        reg_type, start, end, count, self._slave, ex,
                    )

                if batch_ok and rr is not None:
                    regs = rr.registers
                    # decode each entity from slice
                    for (ent, addr, dtype, word_order, scale, bit, width) in payloads:
                        off = addr - start
                        try:
                            slice_regs = regs[off:off + width]
                            if bit is not None:
                                raw16 = int(slice_regs[0]) & 0xFFFF
                                val = bool((raw16 >> int(bit)) & 1)
                            else:
                                val = _decode_16_32(dtype, slice_regs, word_order)
                                if scale is not None:
                                    val = float(val) * float(scale)
                            data[ent.key] = val
                        except Exception as ex:
                            data[ent.key] = None
                            _LOGGER.warning(
                                "Decode failed for %s (key=%s, %s addr=%d dtype=%s width=%d): %s",
                                ent.platform, ent.key, reg_type, addr, dtype, width, ex
                            )
                    continue

                # 2) Fallback: isolate by reading each entity individually
                for (ent, addr, dtype, word_order, scale, bit, width) in payloads:
                    try:
                        if reg_type == "holding":
                            rr1 = await self.hass.async_add_executor_job(
                                self.client.read_holding_registers, addr, width, self._slave
                            )
                        else:
                            rr1 = await self.hass.async_add_executor_job(
                                self.client.read_input_registers, addr, width, self._slave
                            )

                        if _rr_is_error(rr1):
                            raise RuntimeError(f"Modbus error response: {rr1}")

                        regs1 = rr1.registers
                        if bit is not None:
                            raw16 = int(regs1[0]) & 0xFFFF
                            val = bool((raw16 >> int(bit)) & 1)
                        else:
                            val = _decode_16_32(dtype, regs1, word_order)
                            if scale is not None:
                                val = float(val) * float(scale)

                        data[ent.key] = val

                    except Exception as ex:
                        data[ent.key] = None
                        _LOGGER.error(
                            "Read failed for %s (key=%s, %s addr=%d dtype=%s width=%d slave=%d). "
                            "Entity will be set to None. Error: %s",
                            ent.platform, ent.key, reg_type, addr, dtype, width, self._slave, ex
                        )

        # ----------- batch coils/discrete bits -----------
        for reg_type in ("coil", "discrete"):
            items_bits: list[tuple[int, int, tuple]] = []
            for ent, rt, addr, dtype, word_order, scale, bit, width in bit_specs:
                if rt != reg_type:
                    continue
                # coils/discrete are bit-addressed; width is irrelevant here.
                items_bits.append((addr, addr, (ent, addr)))

            for start, end, payloads in self._group_ranges(items_bits, MAX_BITS_PER_READ):
                count = end - start + 1

                rr = None
                batch_ok = False
                try:
                    if reg_type == "coil":
                        rr = await self.hass.async_add_executor_job(
                            self.client.read_coils, start, count, self._slave
                        )
                    else:
                        rr = await self.hass.async_add_executor_job(
                            self.client.read_discrete_inputs, start, count, self._slave
                        )

                    if _rr_is_error(rr):
                        raise RuntimeError(f"Modbus error response: {rr}")
                    batch_ok = True

                except Exception as ex:
                    _LOGGER.warning(
                        "Batch read failed (%s %d..%d, count=%d, slave=%d): %s",
                        reg_type, start, end, count, self._slave, ex,
                    )

                if batch_ok and rr is not None:
                    bits = rr.bits
                    for (ent, addr) in payloads:
                        off = addr - start
                        try:
                            data[ent.key] = bool(bits[off])
                        except Exception as ex:
                            data[ent.key] = None
                            _LOGGER.warning(
                                "Decode failed for %s (key=%s, %s addr=%d): %s",
                                ent.platform, ent.key, reg_type, addr, ex
                            )
                    continue

                # fallback: per-bit read
                for (ent, addr) in payloads:
                    try:
                        if reg_type == "coil":
                            rr1 = await self.hass.async_add_executor_job(
                                self.client.read_coils, addr, 1, self._slave
                            )
                        else:
                            rr1 = await self.hass.async_add_executor_job(
                                self.client.read_discrete_inputs, addr, 1, self._slave
                            )

                        if _rr_is_error(rr1):
                            raise RuntimeError(f"Modbus error response: {rr1}")

                        data[ent.key] = bool(rr1.bits[0])
                    except Exception as ex:
                        data[ent.key] = None
                        _LOGGER.error(
                            "Read failed for %s (key=%s, %s addr=%d slave=%d). "
                            "Entity will be set to None. Error: %s",
                            ent.platform, ent.key, reg_type, addr, self._slave, ex
                        )

        return data

    # ---------------------------------------------------------------------
    # Writes (unchanged logic, no deadlock)
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
