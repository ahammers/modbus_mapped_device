from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pymodbus.client import ModbusTcpClient, ModbusSerialClient


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
    def __init__(self, transport: str, tcp: TcpParams | None, rtu: RtuParams | None) -> None:
        self._transport = transport
        self._client: Any = None

        if transport == "tcp":
            assert tcp is not None
            self._client = ModbusTcpClient(host=tcp.host, port=tcp.port)
        elif transport == "rtu":
            assert rtu is not None
            self._client = ModbusSerialClient(
                port=rtu.port,
                baudrate=rtu.baudrate,
                bytesize=rtu.bytesize,
                parity=rtu.parity,
                stopbits=rtu.stopbits,
                timeout=2.0,
            )
        else:
            raise ValueError(f"Unsupported transport: {transport}")

    def connect(self) -> bool:
        return bool(self._client.connect())

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass

    @staticmethod
    def _ok(rr) -> bool:
        # pymodbus response objects have isError()
        try:
            return rr is not None and not rr.isError()
        except Exception:
            return rr is not None

    # ---- READ ----
    def read_holding_registers(self, address: int, count: int, slave: int):
        rr = self._client.read_holding_registers(address=address, count=count, slave=slave)
        if not self._ok(rr):
            raise RuntimeError("read_holding_registers failed")
        return rr

    def read_input_registers(self, address: int, count: int, slave: int):
        rr = self._client.read_input_registers(address=address, count=count, slave=slave)
        if not self._ok(rr):
            raise RuntimeError("read_input_registers failed")
        return rr

    def read_coils(self, address: int, count: int, slave: int):
        rr = self._client.read_coils(address=address, count=count, slave=slave)
        if not self._ok(rr):
            raise RuntimeError("read_coils failed")
        return rr

    def read_discrete_inputs(self, address: int, count: int, slave: int):
        rr = self._client.read_discrete_inputs(address=address, count=count, slave=slave)
        if not self._ok(rr):
            raise RuntimeError("read_discrete_inputs failed")
        return rr

    # ---- WRITE ----
    def write_register(self, address: int, value: int, slave: int):
        rr = self._client.write_register(address=address, value=value, slave=slave)
        if not self._ok(rr):
            raise RuntimeError("write_register failed")
        return rr

    def write_registers(self, address: int, values: list[int], slave: int):
        rr = self._client.write_registers(address=address, values=values, slave=slave)
        if not self._ok(rr):
            raise RuntimeError("write_registers failed")
        return rr

    def write_coil(self, address: int, value: bool, slave: int):
        rr = self._client.write_coil(address=address, value=value, slave=slave)
        if not self._ok(rr):
            raise RuntimeError("write_coil failed")
        return rr
