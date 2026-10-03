import json
import socket
import time


# 29500 = host/tools/live_verifier.py (tk dashboard), 29501 = host/run_server.py correlator
BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORTS = (29500, 29501)
RETRY_SECONDS = 2.0          # a closed target is retried at most this often

_connections = {}
_next_retry = {}
_sent_frames = 0


def _close_connection(port):
    connection = _connections.pop(port, None)
    if connection is not None:
        try:
            connection.close()
        except OSError:
            pass


def _send(port, line):
    connection = _connections.get(port)
    if connection is None:
        if time.monotonic() < _next_retry.get(port, 0.0):
            return
        try:
            connection = socket.create_connection((BRIDGE_HOST, port), timeout=0.05)
            _connections[port] = connection
            print(f"J1939 bridge connected to {BRIDGE_HOST}:{port}")
        except OSError:
            _next_retry[port] = time.monotonic() + RETRY_SECONDS
            return
    try:
        connection.sendall(line)
    except OSError:
        _close_connection(port)
        _next_retry[port] = time.monotonic() + RETRY_SECONDS


def forward_to_verifier(can_identifier, data_bytes, timestamp_seconds, timestamp_us=None):
    global _sent_frames
    packet = {
        "id": f"0x{int(can_identifier):08X}",
        "data": bytes(data_bytes).hex(),
        "timestamp": float(timestamp_seconds),
        "extended": True,
    }
    if timestamp_us is not None:
        packet["timestamp_us"] = int(timestamp_us)
    line = (json.dumps(packet) + "\n").encode("utf-8")
    for port in BRIDGE_PORTS:
        _send(port, line)
    _sent_frames += 1


def on_init():
    print("J1939 verifier bridge loaded")


def on_can_rx(a_can):
    dlc = int(a_can.FDLC)
    data_bytes = bytes(a_can.FData[index] for index in range(dlc))
    # TSMaster hardware timestamp (us) is far less jittery than PC time
    timestamp_us = getattr(a_can, "FTimeUs", None)
    forward_to_verifier(a_can.FIdentifier, data_bytes, time.time(), timestamp_us)


def On_CAN_Rx(a_can):
    on_can_rx(a_can)


def on_stop():
    for port in list(_connections):
        _close_connection(port)
