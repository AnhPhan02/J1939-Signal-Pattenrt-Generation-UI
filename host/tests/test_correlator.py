"""
Self-test for j1939hub.correlator: synthesises what the STM32 and TSMaster would
produce (with clock offset, jitter, a lost frame, a corrupted byte and dropped
UART log lines) and checks that every injected fault gets the right verdict.

    python host/tests/test_correlator.py
"""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # host/ -> import j1939hub

from j1939hub.correlator import (Correlator, RxRecord, SignalDb, pattern_value)

PGN_EEC1, PGN_CCVS1 = 61444, 65265
IDS = {PGN_EEC1: 0x0CF00400, PGN_CCVS1: 0x18FEF100}
PERIOD = {PGN_EEC1: 10, PGN_CCVS1: 20}          # smooth-mode periods (ms)

CONFIGS = [
    {"spn": 190, "pattern_type": 2, "min_value": 800, "max_value": 3500, "param1": 8.0},
    {"spn": 84, "pattern_type": 1, "min_value": 0, "max_value": 120, "param1": 15.0},
]


def build_payload(db, pgn, t_sec):
    frame = bytearray(b"\xff" * 8)
    value = int.from_bytes(frame, "little")
    for cfg in CONFIGS:
        sig = db.by_spn[cfg["spn"]]
        if sig["pgn"] != pgn:
            continue
        raw = SignalDb.encode_raw(sig, pattern_value(cfg, t_sec))
        start = (sig["start_byte"] - 1) * 8 + (sig["start_bit"] - 1)
        mask = ((1 << sig["num_bits"]) - 1) << start
        value = (value & ~mask) | (raw << start)
    return value.to_bytes(8, "little")


def main():
    random.seed(1)
    db = SignalDb()
    corr = Correlator(db)
    corr.set_configs(CONFIGS)

    t0 = 123_456                 # STM32 ms since boot at START
    offset = 987_654.321         # TSMaster clock - STM32 clock (ms)
    corr.feed_line(f"$RUN,{t0},0,0")

    seq = 0
    lost_seq, corrupt_seq, unlogged_seqs = 400, 700, set(range(900, 905))
    expected = {"lost": 1, "mismatch": 1, "unlogged": 5}

    tx_lines, rx = [], []
    for t in range(t0, t0 + 20_000):          # 20 s run, 1 ms tick
        for pgn in (PGN_EEC1, PGN_CCVS1):
            if (t - t0) % PERIOD[pgn] or t == t0:
                continue
            seq += 1
            data = build_payload(db, pgn, (t - t0) / 1000.0)
            if seq not in unlogged_seqs:
                tx_lines.append(f"[12:00:00] $TX,{seq},{t},{IDS[pgn]:08X},{data.hex().upper()},Q")
            if seq == lost_seq:
                continue
            if seq == corrupt_seq:
                data = bytes([data[0] ^ 0x01]) + data[1:]
            rx.append(RxRecord(t + offset + random.uniform(0.2, 1.5), IDS[pgn], data, 1.0))

    # Foreign ECU frame that the generator never sent
    rx.append(RxRecord(t0 + offset + 5000, 0x18FEEE00, bytes(8), 1.0))
    rx.sort(key=lambda r: r.t_ms)

    for line in tx_lines:
        corr.feed_line(line)
    for r in rx:
        corr.add_rx(r)
    corr.finish()

    s = corr.summary()
    v = s["verdicts"]
    print("verdicts:", v, "| offset:", s["offset_ms"], "| jitter:", s["latency_jitter_ms"])
    for spn in s["spn_checks"]:
        print(f"  SPN {spn['spn']:>4} {spn['name']:<20} checked={spn['checked']} ok={spn['value_ok']} fail={spn['fail']}")

    assert s["clock_locked"] and abs(s["offset_ms"] - offset) < 2.0, s["offset_ms"]
    for k, n in expected.items():
        assert v[k] == n, (k, v[k], n)
    assert v["unexpected"] == 0, v
    assert s["rx"]["foreign"] == 1
    assert v["match"] == seq - sum(expected.values())
    assert s["tx"]["log_gap_lines"] == 5
    assert all(sp["fail"] == 0 and sp["value_ok"] > 0 for sp in s["spn_checks"]), s["spn_checks"]

    # Negative check: the UI believes SPN 190 runs an 9 s sine, firmware ran 8 s
    wrong = Correlator(db)
    wrong.set_configs([dict(CONFIGS[0], param1=9.0), CONFIGS[1]])
    wrong.feed_line(f"$RUN,{t0},0,0")
    for line in tx_lines:
        wrong.feed_line(line)
    for r in rx:
        wrong.add_rx(RxRecord(r.t_ms, r.can_id, r.data, 1.0))
    wrong.finish()
    by_spn = {sp["spn"]: sp for sp in wrong.summary()["spn_checks"]}
    assert by_spn[190]["fail"] > 0 and by_spn[84]["fail"] == 0, by_spn
    print(f"Wrong-config run flagged {by_spn[190]['fail']} SPN 190 value failures (expected)")
    print("ALL CORRELATOR CHECKS PASSED")


if __name__ == "__main__":
    main()
