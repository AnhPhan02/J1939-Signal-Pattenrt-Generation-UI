"""Live J1939 verification dashboard for the STM32F103 signal generator."""

from __future__ import annotations

import argparse
import json
import queue
import re
import socket
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import ttk
from typing import Callable


DBC_MESSAGE = re.compile(r"^BO_\s+(\d+)\s+(\w+):\s+(\d+)\s+(\w+)$")
DBC_SIGNAL = re.compile(
    r'^\s+SG_\s+(\w+)\s+:\s+(\d+)\|(\d+)@([01])([+-])\s+'
    r'\(([-+0-9.eE]+),([-+0-9.eE]+)\)\s+'
    r'\[([-+0-9.eE]+)\|([-+0-9.eE]+)\]\s+"([^"]*)"'
)
DBC_CYCLE = re.compile(r'^CM_\s+BO_\s+(\d+)\s+".*?cycle\s+(\d+)\s+ms\.";$')

def get_decimal_places(name: str) -> int:
    name_lower = name.lower()
    if "engine_speed" in name_lower:
        return 3
    elif "accel_pedal" in name_lower:
        return 1
    elif "brake_pedal" in name_lower:
        return 1
    elif "vehicle_speed" in name_lower:
        return 8
    elif "fuel_rate" in name_lower:
        return 2
    elif "inst_fuel" in name_lower:
        return 9
    elif "avg_fuel" in name_lower:
        return 9
    return 4

@dataclass(frozen=True)
class SignalDefinition:
    name: str
    start_bit: int
    bit_length: int
    scale: float
    offset: float
    minimum: float
    maximum: float
    unit: str

    def decode(self, payload: bytes) -> tuple[int, float]:
        raw_frame = int.from_bytes(payload, byteorder="little", signed=False)
        raw_value = (raw_frame >> self.start_bit) & ((1 << self.bit_length) - 1)
        return raw_value, raw_value * self.scale + self.offset

    def encode(self, value: float) -> int:
        raw_value = round((value - self.offset) / self.scale)
        return max(0, min(raw_value, (1 << self.bit_length) - 1))


@dataclass
class MessageDefinition:
    dbc_identifier: int
    name: str
    dlc: int
    transmitter: str
    signals: list[SignalDefinition] = field(default_factory=list)
    cycle_ms: int | None = None

    @property
    def can_identifier(self) -> int:
        return self.dbc_identifier & 0x1FFFFFFF


@dataclass(frozen=True)
class CanFrame:
    identifier: int
    data: bytes
    timestamp: float
    is_extended: bool = True


@dataclass
class VerificationResult:
    status: str
    message: MessageDefinition | None
    detail: str
    values: dict[str, float]
    timestamp: float


class J1939Database:
    def __init__(self, dbc_path: Path) -> None:
        self.messages = self._load(dbc_path)
        self.by_can_identifier = {message.can_identifier: message for message in self.messages}

    @staticmethod
    def _load(dbc_path: Path) -> list[MessageDefinition]:
        messages: dict[int, MessageDefinition] = {}
        current_message: MessageDefinition | None = None
        cycles: dict[int, int] = {}

        for line in dbc_path.read_text(encoding="utf-8").splitlines():
            message_match = DBC_MESSAGE.match(line)
            if message_match:
                dbc_identifier, name, dlc, transmitter = message_match.groups()
                current_message = MessageDefinition(int(dbc_identifier), name, int(dlc), transmitter)
                messages[current_message.dbc_identifier] = current_message
                continue

            signal_match = DBC_SIGNAL.match(line)
            if signal_match and current_message:
                (
                    name,
                    start_bit,
                    bit_length,
                    byte_order,
                    signed,
                    scale,
                    offset,
                    minimum,
                    maximum,
                    unit,
                ) = signal_match.groups()
                if byte_order != "1" or signed != "+":
                    raise ValueError(f"{name} is not an unsigned Intel/J1939 signal")
                current_message.signals.append(
                    SignalDefinition(
                        name,
                        int(start_bit),
                        int(bit_length),
                        float(scale),
                        float(offset),
                        float(minimum),
                        float(maximum),
                        unit,
                    )
                )
                continue

            cycle_match = DBC_CYCLE.match(line)
            if cycle_match:
                dbc_identifier, cycle_ms = cycle_match.groups()
                cycles[int(dbc_identifier)] = int(cycle_ms)

        if not messages:
            raise ValueError(f"No DBC messages found in {dbc_path}")

        for dbc_identifier, message in messages.items():
            message.cycle_ms = cycles.get(dbc_identifier)
        return list(messages.values())


class FrameValidator:
    def __init__(self, database: J1939Database, expected_values: dict[str, dict[str, float]]) -> None:
        self.database = database
        self.expected_values = expected_values
        self.last_seen: dict[int, float] = {}

    def validate(self, frame: CanFrame) -> VerificationResult:
        message = self.database.by_can_identifier.get(frame.identifier)
        if message is None:
            return VerificationResult("IGNORED", None, "Frame is not in the verifier DBC", {}, frame.timestamp)

        errors: list[str] = []
        values: dict[str, float] = {}
        if not frame.is_extended:
            errors.append("expected 29-bit extended CAN frame")
        if len(frame.data) != message.dlc:
            errors.append(f"expected DLC {message.dlc}, received {len(frame.data)}")
            return VerificationResult("FAIL", message, "; ".join(errors), values, frame.timestamp)

        previous_timestamp = self.last_seen.get(frame.identifier)
        self.last_seen[frame.identifier] = frame.timestamp
        if previous_timestamp is not None and message.cycle_ms:
            elapsed_ms = (frame.timestamp - previous_timestamp) * 1000.0
            tolerance_ms = max(10.0, message.cycle_ms * 0.35)
            if abs(elapsed_ms - message.cycle_ms) > tolerance_ms:
                errors.append(f"cycle {elapsed_ms:.1f} ms, expected {message.cycle_ms} +/- {tolerance_ms:.1f} ms")

        configured_expectations = self.expected_values.get(f"0x{frame.identifier:08X}", {})
        for signal in message.signals:
            raw_value, physical_value = signal.decode(frame.data)
            values[signal.name] = physical_value
            decimals = get_decimal_places(signal.name)
            if physical_value < signal.minimum - 1e-9 or physical_value > signal.maximum + 1e-9:
                errors.append(
                    f"{signal.name}={physical_value:.{decimals}f} {signal.unit} outside [{signal.minimum}, {signal.maximum}]"
                )
            if signal.name in configured_expectations:
                expected = float(configured_expectations[signal.name])
                tolerance = max(abs(signal.scale) / 2.0, 1e-9)
                if abs(physical_value - expected) > tolerance:
                    errors.append(
                        f"{signal.name}={physical_value:.{decimals}f} {signal.unit}, expected {expected:.{decimals}f} +/- {tolerance:.{decimals}f}"
                    )
            if raw_value == (1 << signal.bit_length) - 1:
                errors.append(f"{signal.name} is J1939 not-available raw value")

        status = "PASS" if not errors else "FAIL"
        detail = "All ID, DLC, scaling, range, expected-value and timing checks passed" if not errors else "; ".join(errors)
        return VerificationResult(status, message, detail, values, frame.timestamp)

    def timeouts(self, now: float) -> list[MessageDefinition]:
        expired: list[MessageDefinition] = []
        for message in self.database.messages:
            if not message.cycle_ms or message.can_identifier not in self.last_seen:
                continue
            timeout_s = max(0.5, message.cycle_ms * 3.0 / 1000.0)
            if now - self.last_seen[message.can_identifier] > timeout_s:
                expired.append(message)
        return expired


class TcpJsonBridge(threading.Thread):
    def __init__(self, host: str, port: int, publish: Callable[[CanFrame], None]) -> None:
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.publish = publish

    def run(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.host, self.port))
            server.listen()
            while True:
                client, _ = server.accept()
                with client, client.makefile("r", encoding="utf-8") as stream:
                    for line in stream:
                        try:
                            message = json.loads(line)
                            identifier = int(str(message["id"]), 0)
                            data = bytes.fromhex(message["data"].replace(" ", ""))
                            timestamp = float(message.get("timestamp", time.time()))
                            extended = bool(message.get("extended", True))
                            self.publish(CanFrame(identifier, data, timestamp, extended))
                        except (KeyError, ValueError, TypeError, json.JSONDecodeError):
                            continue


class PythonCanReceiver(threading.Thread):
    def __init__(self, interface: str, channel: str, bitrate: int, publish: Callable[[CanFrame], None]) -> None:
        super().__init__(daemon=True)
        self.interface = interface
        self.channel = channel
        self.bitrate = bitrate
        self.publish = publish

    def run(self) -> None:
        try:
            import can
        except ImportError as error:
            raise RuntimeError("Install python-can with: pip install -r requirements.txt") from error

        with can.Bus(interface=self.interface, channel=self.channel, bitrate=self.bitrate) as bus:
            while True:
                message = bus.recv(timeout=1.0)
                if message is not None:
                    self.publish(
                        CanFrame(
                            message.arbitration_id,
                            bytes(message.data),
                            message.timestamp or time.time(),
                            message.is_extended_id,
                        )
                    )


class Simulator(threading.Thread):
    def __init__(self, database: J1939Database, publish: Callable[[CanFrame], None]) -> None:
        super().__init__(daemon=True)
        self.database = database
        self.publish = publish

    def run(self) -> None:
        next_transmission = {message.can_identifier: time.monotonic() for message in self.database.messages}
        while True:
            now = time.monotonic()
            for message in self.database.messages:
                period_s = (message.cycle_ms or 1000) / 1000.0
                if now < next_transmission[message.can_identifier]:
                    continue
                payload_value = 0
                for signal in message.signals:
                    midpoint = (signal.minimum + signal.maximum) / 2.0
                    raw_value = signal.encode(midpoint)
                    payload_value |= raw_value << signal.start_bit
                self.publish(CanFrame(message.can_identifier, payload_value.to_bytes(8, "little"), time.time(), True))
                next_transmission[message.can_identifier] += period_s
            time.sleep(0.002)


class VerificationDashboard(tk.Tk):
    def __init__(self, database: J1939Database, validator: FrameValidator, incoming: queue.Queue[CanFrame]) -> None:
        super().__init__()
        self.database = database
        self.validator = validator
        self.incoming = incoming
        self.rows: dict[int, str] = {}
        self.total_frames = 0
        self.pass_frames = 0
        self.fail_frames = 0
        self.title("STM32F103 J1939 Real-Time Verifier")
        self.geometry("1360x780")
        self.minsize(1050, 620)
        self._build_interface()
        self.after(50, self._process_messages)
        self.after(250, self._refresh_timeouts)

    def _build_interface(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Header.TLabel", font=("Segoe UI", 19, "bold"))
        style.configure("PASS.TLabel", foreground="#087f23", font=("Segoe UI", 16, "bold"))
        style.configure("FAIL.TLabel", foreground="#b00020", font=("Segoe UI", 16, "bold"))
        style.configure("Treeview", rowheight=28, font=("Consolas", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))

        header = ttk.Frame(self, padding=14)
        header.pack(fill="x")
        ttk.Label(header, text="STM32F103 J1939 Real-Time Verification", style="Header.TLabel").pack(side="left")
        self.overall_status = ttk.Label(header, text="WAITING FOR CAN FRAMES", style="FAIL.TLabel")
        self.overall_status.pack(side="right")

        summary = ttk.Frame(self, padding=(14, 0, 14, 10))
        summary.pack(fill="x")
        self.summary_text = tk.StringVar(value="Frames: 0   PASS: 0   FAIL: 0   Timeouts: 0")
        self.last_error_text = tk.StringVar(value="Last check: waiting for data")
        ttk.Label(summary, textvariable=self.summary_text, font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(summary, textvariable=self.last_error_text, wraplength=1280).pack(anchor="w", pady=(4, 0))

        columns = ("message", "can_id", "period", "status", "last_seen", "decoded_values", "detail")
        self.table = ttk.Treeview(self, columns=columns, show="headings")
        labels = {
            "message": "Message",
            "can_id": "CAN ID",
            "period": "Cycle",
            "status": "Status",
            "last_seen": "Last Received",
            "decoded_values": "Decoded Values",
            "detail": "Verification Detail",
        }
        widths = {"message": 120, "can_id": 105, "period": 75, "status": 85, "last_seen": 145, "decoded_values": 420, "detail": 560}
        for column in columns:
            self.table.heading(column, text=labels[column])
            self.table.column(column, width=widths[column], anchor="w")
        self.table.tag_configure("PASS", background="#e7f6ea")
        self.table.tag_configure("FAIL", background="#ffe8ec")
        self.table.tag_configure("WAIT", background="#fff7d6")
        self.table.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        for message in sorted(self.database.messages, key=lambda item: item.can_identifier):
            row = self.table.insert(
                "",
                "end",
                values=(
                    message.name,
                    f"0x{message.can_identifier:08X}",
                    f"{message.cycle_ms or '-'} ms",
                    "WAITING",
                    "-",
                    "-",
                    "No frame received yet",
                ),
                tags=("WAIT",),
            )
            self.rows[message.can_identifier] = row

    def _process_messages(self) -> None:
        while True:
            try:
                frame = self.incoming.get_nowait()
            except queue.Empty:
                break
            result = self.validator.validate(frame)
            if result.message is None:
                continue
            self.total_frames += 1
            if result.status == "PASS":
                self.pass_frames += 1
            else:
                self.fail_frames += 1
            values = ", ".join(f"{name}={value:.{get_decimal_places(name)}f}" for name, value in result.values.items())
            row = self.rows[result.message.can_identifier]
            self.table.item(
                row,
                values=(
                    result.message.name,
                    f"0x{result.message.can_identifier:08X}",
                    f"{result.message.cycle_ms or '-'} ms",
                    result.status,
                    time.strftime("%H:%M:%S", time.localtime(result.timestamp)),
                    values,
                    result.detail,
                ),
                tags=(result.status,),
            )
            self.last_error_text.set(f"{result.message.name}: {result.detail}")
        self._refresh_summary()
        self.after(50, self._process_messages)

    def _refresh_timeouts(self) -> None:
        expired = self.validator.timeouts(time.time())
        for message in expired:
            row = self.rows[message.can_identifier]
            current = list(self.table.item(row, "values"))
            current[3] = "TIMEOUT"
            current[6] = f"No frame for more than three expected cycles ({message.cycle_ms} ms)"
            self.table.item(row, values=current, tags=("FAIL",))
        self._refresh_summary(len(expired))
        self.after(250, self._refresh_timeouts)

    def _refresh_summary(self, timeout_count: int = 0) -> None:
        self.summary_text.set(
            f"Frames: {self.total_frames}   PASS: {self.pass_frames}   FAIL: {self.fail_frames}   Timeouts: {timeout_count}"
        )
        if self.total_frames and self.fail_frames == 0 and timeout_count == 0:
            self.overall_status.configure(text="LIVE VERIFICATION: PASS", style="PASS.TLabel")
        elif self.fail_frames or timeout_count:
            self.overall_status.configure(text="LIVE VERIFICATION: ERROR", style="FAIL.TLabel")


def load_expectations(path: Path | None) -> dict[str, dict[str, float]]:
    if path is None:
        return {}
    contents = json.loads(path.read_text(encoding="utf-8"))
    return {str(identifier): {str(name): float(value) for name, value in values.items()} for identifier, values in contents.items()}


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dbc", type=Path, required=True,
                        help="DBC to check against, e.g. one exported from the web UI (Download Active DBC)")
    parser.add_argument("--expected-profile", type=Path)
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--interface", help="python-can interface, for example pcan or vector")
    parser.add_argument("--channel", help="python-can channel, for example PCAN_USBBUS1")
    parser.add_argument("--bitrate", type=int, default=500000)
    parser.add_argument("--tcp-port", type=int, default=29500)
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    database = J1939Database(arguments.dbc)
    validator = FrameValidator(database, load_expectations(arguments.expected_profile))
    incoming: queue.Queue[CanFrame] = queue.Queue()
    publish = incoming.put

    TcpJsonBridge("127.0.0.1", arguments.tcp_port, publish).start()
    if arguments.simulate:
        Simulator(database, publish).start()
    if arguments.interface or arguments.channel:
        if not arguments.interface or not arguments.channel:
            raise SystemExit("Both --interface and --channel are required for direct python-can capture")
        PythonCanReceiver(arguments.interface, arguments.channel, arguments.bitrate, publish).start()

    VerificationDashboard(database, validator, incoming).mainloop()


if __name__ == "__main__":
    main()
