from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from typing import Any

from pymodbus.client import ModbusSerialClient, ModbusTcpClient

_LOGGER = logging.getLogger(__name__)

@dataclass(frozen=True)
class TcpParams:
    host: str
    port: int

@dataclass(frozen=True)
class RtuParams:
    port: str
    baudrate: int
    bytesize: int
    parity: str
    stopbits: int

class ModbusClientWrapper:
    """
    Thin wrapper around pymodbus sync client.

    IMPORTANT:
    PyModbus changed the parameter name for addressing a slave/device:
      - old: slave=...
      - new: device_id=... (keyword-only)
    We adapt at runtime by inspecting the called method signature.
    """

    def __init__(self, transport: str, tcp: TcpParams | None, rtu: RtuParams | None) -> None:
        self._transport = transport
        self._tcp = tcp
        self._rtu = rtu
        self._client: ModbusTcpClient | ModbusSerialClient | None = None

    def connect(self) -> bool:
        if self._client is None:
            if self._transport == "tcp":
                assert self._tcp is not None
                _LOGGER.debug("Attempting TCP connection with parameters: host=%s, port=%d", self._tcp.host, self._tcp.port)
                self._client = ModbusTcpClient(host=self._tcp.host, port=self._tcp.port)
            else:
                assert self._rtu is not None
                _LOGGER.debug(
                    "Attempting RTU connection with parameters: port=%s, baudrate=%d, bytesize=%d, parity=%s, stopbits=%d",
                    self._rtu.port, self._rtu.baudrate, self._rtu.bytesize, self._rtu.parity, self._rtu.stopbits
                )
                self._client = ModbusSerialClient(
                    port=self._rtu.port,
                    baudrate=self._rtu.baudrate,
                    bytesize=self._rtu.bytesize,
                    parity=self._rtu.parity,
                    stopbits=self._rtu.stopbits,
                    timeout=2,
                )

        try:
            _LOGGER.debug("Connecting to Modbus client...")
            success = bool(self._client.connect())
            if success:
                _LOGGER.debug("Modbus connection established successfully.")
            else:
                _LOGGER.warning("Failed to establish Modbus connection.")
            return success
        except Exception as ex:
            _LOGGER.error("Modbus connect failed: %s", ex, exc_info=True)
            return False

    def close(self) -> None:
        if self._client is None:
            return
        try:
            _LOGGER.debug("Closing Modbus connection...")
            self._client.close()
            _LOGGER.debug("Modbus connection closed successfully.")
        except Exception as ex:
            _LOGGER.error("Failed to close Modbus connection: %s", ex, exc_info=True)

    # ---------- compatibility helper ----------

    def _call_with_slave_compat(self, fn_name: str, *args: Any, slave: int, **kwargs: Any) -> Any:
        """
        Call a pymodbus ModbusClientMixin method in a way that works with both:
          - fn(..., slave=1)
          - fn(..., device_id=1)   (keyword-only)
        and as a fallback also supports older unit= naming.
        """
        if self._client is None:
            raise RuntimeError("Client not connected")

        fn = getattr(self._client, fn_name)

        try:
            sig = inspect.signature(fn)
            params = sig.parameters
        except Exception:
            # If signature is not available, try common order:
            # historically: (address, count=..., slave=...)
            try:
                return fn(*args, slave=slave, **kwargs)
            except TypeError:
                return fn(*args, unit=slave, **kwargs)

        if "device_id" in params:
            # new pymodbus: keyword-only device_id
            return fn(*args, device_id=slave, **kwargs)
        if "slave" in params:
            # old pymodbus: slave kw
            return fn(*args, slave=slave, **kwargs)
        if "unit" in params:
            # some older variants used unit
            return fn(*args, unit=slave, **kwargs)

        # last resort: maybe accepts 3rd positional
        try:
            return fn(*args, slave, **kwargs)
        except TypeError as ex:
            raise TypeError(f"{fn_name}() does not accept slave/device_id/unit parameter") from ex

    # ---------- read helpers ----------

    def read_holding_registers(self, address: int, count: int, slave: int):
        _LOGGER.debug("Reading holding registers: address=%d, count=%d, slave=%d", address, count, slave)
        return self._call_with_slave_compat("read_holding_registers", int(address), count=int(count), slave=int(slave))

    def read_input_registers(self, address: int, count: int, slave: int):
        _LOGGER.debug("Reading input registers: address=%d, count=%d, slave=%d", address, count, slave)
        return self._call_with_slave_compat("read_input_registers", int(address), count=int(count), slave=int(slave))

    def read_coils(self, address: int, count: int, slave: int):
        _LOGGER.debug("Reading coils: address=%d, count=%d, slave=%d", address, count, slave)
        return self._call_with_slave_compat("read_coils", int(address), count=int(count), slave=int(slave))

    def read_discrete_inputs(self, address: int, count: int, slave: int):
        _LOGGER.debug("Reading discrete inputs: address=%d, count=%d, slave=%d", address, count, slave)
        return self._call_with_slave_compat("read_discrete_inputs", int(address), count=int(count), slave=int(slave))

    # ---------- write helpers ----------

    def write_register(self, address: int, value: int, slave: int):
        _LOGGER.debug("Writing to register: address=%d, value=%d, slave=%d", address, value, slave)
        # pymodbus API name is "write_register"
        return self._call_with_slave_compat("write_register", int(address), int(value), slave=int(slave))

    def write_coil(self, address: int, value: bool, slave: int):
        _LOGGER.debug("Writing to coil: address=%d, value=%s, slave=%d", address, value, slave)
        return self._call_with_slave_compat("write_coil", int(address), bool(value), slave=int(slave))
