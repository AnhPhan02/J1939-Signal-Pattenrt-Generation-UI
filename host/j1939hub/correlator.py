"""
J1939 TX <-> RX correlation engine.

Proves that every frame the STM32 handed to its CAN controller ($TX lines on the
UART) was received unchanged by TSMaster (ASC export, or live JSON lines from
integrations/tsmaster/mini_program_bridge.py), and that the received SPN values follow the
waveform configured from the web UI.

Three layers, each answering one question:
  1. Transport  - same CAN ID + same 8 bytes arrived?  (MATCH / LOST / MISMATCH)
  2. Timing     - latency jitter, clock drift, real PGN period vs. configured
  3. Semantics  - decoded SPN value == firmware pattern formula at t_tx?

Firmware line formats (MID/j1939_tx_scheduler.c, APP/app_generator.c):
  $TX,<seq>,<t_ms>,<can_id hex8>,<data hex16>,<Q|B|E>
  $RUN,<t0_ms>,<mode>,<duration_s>
  $END,<t_ms>,<seq>

Offline use:
  python host/tools/correlate_logs.py --tx stm32_uart.log --rx tsmaster.asc \
         --config run_config.json --csv report.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Deque, Dict, Iterable, List, Optional, Tuple

try:
    from .paths import SPN_DB_PATH as DEFAULT_DB
except ImportError:     # loaded as a plain file, e.g. copied next to a log
    DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "j1939_spn_database.json"

TX_RE = re.compile(r"\$TX,(\d+),(\d+),([0-9A-Fa-f]{8}),([0-9A-Fa-f]{16}),([QBE])")
RUN_RE = re.compile(r"\$RUN,(\d+),(\d+),(\d+)")
END_RE = re.compile(r"\$END,(\d+),(\d+)")

# Firmware pattern start-up lead-in (j1939_pattern_generator.c: STARTUP_SEC)
STARTUP_SEC = 5.0


# =============================================================================
# RECORDS
# =============================================================================
@dataclass
class TxRecord:
    seq: int
    t_ms: float
    can_id: int
    data: bytes
    status: str  # Q = queued, B = mailboxes full, E = driver error


@dataclass
class RxRecord:
    t_ms: float
    can_id: int
    data: bytes
    arrived_wall: float = 0.0


@dataclass
class Event:
    verdict: str            # MATCH | LOST | MISMATCH | UNEXPECTED | UNLOGGED
    can_id: int
    tx: Optional[TxRecord]
    rx: Optional[RxRecord]
    latency_ms: Optional[float] = None
    diff_bytes: List[int] = field(default_factory=list)
    spn_checks: List[dict] = field(default_factory=list)


def parse_tx_line(line: str) -> Optional[TxRecord]:
    m = TX_RE.search(line)
    if not m:
        return None
    seq, t_ms, can_id, data, status = m.groups()
    return TxRecord(int(seq), float(t_ms), int(can_id, 16), bytes.fromhex(data), status)


def pgn_from_id(can_id: int) -> int:
    pgn = (can_id >> 8) & 0x3FFFF
    if ((pgn >> 8) & 0xFF) < 240:   # PDU1: PS byte is a destination address
        pgn &= 0x3FF00
    return pgn


# =============================================================================
# SIGNAL DATABASE + FIRMWARE PATTERN MODEL
# =============================================================================
class SignalDb:
    def __init__(self, path: Path = DEFAULT_DB):
        with open(path, "r", encoding="utf-8") as f:
            rows = json.load(f)
        self.by_spn: Dict[int, dict] = {r["spn"]: r for r in rows}

    @staticmethod
    def raw_value(sig: dict, data: bytes) -> int:
        frame = int.from_bytes(data, "little")
        start = (sig["start_byte"] - 1) * 8 + (sig["start_bit"] - 1)
        return (frame >> start) & ((1 << sig["num_bits"]) - 1)

    @staticmethod
    def encode_raw(sig: dict, physical: float) -> int:
        """Same steps as J1939_Encode_Signal(): clamp, scale, round half up, saturate."""
        v = min(max(physical, sig["min_physical"]), sig["max_physical"])
        raw = (v - sig["offset"]) / sig["resolution"]
        raw = int(max(raw, 0.0) + 0.5)
        return min(raw, (1 << sig["num_bits"]) - 1)


def pattern_value(cfg: dict, t_sec: float) -> Optional[float]:
    """
    Mirror of prv_compute_next_value() + App_Gen_Config_Signal() parameter mapping.
    cfg keys: pattern_type, min_value, max_value, param1.
    Returns None for patterns that are not deterministic (random walk).
    """
    if t_sec < STARTUP_SEC:
        return 0.0
    t = t_sec - STARTUP_SEC
    typ = int(cfg["pattern_type"])
    lo, hi, p1 = float(cfg["min_value"]), float(cfg["max_value"]), float(cfg["param1"])
    rng = hi - lo
    period = p1 if p1 > 0.1 else 10.0
    phase = math.fmod(t, period) / period

    if typ == 0:   # Constant
        return p1 if lo <= p1 <= hi else (lo + hi) * 0.5
    if typ == 1:   # Ramp
        return lo + phase * rng
    if typ == 2:   # Sine
        return min(max((lo + hi) * 0.5 + rng * 0.5 * math.sin(2.0 * math.pi * phase), lo), hi)
    if typ == 3:   # Triangle
        u = phase * 2.0 if phase < 0.5 else 2.0 * (1.0 - phase)
        return lo + u * rng
    if typ == 4:   # Step
        n = int(p1) if 2.0 <= p1 <= 50.0 else 8
        idx = min(int(phase * n), n - 1)
        return lo + (idx / (n - 1)) * rng
    if typ in (5, 7):   # Square (State sequence without a table behaves the same)
        return hi if phase < 0.5 else lo
    return None    # 6 = random walk


# =============================================================================
# RUNNING STATISTICS
# =============================================================================
class Welford:
    def __init__(self):
        self.n = 0
        self.mean = 0.0
        self.m2 = 0.0
        self.min = math.inf
        self.max = -math.inf

    def add(self, x: float):
        self.n += 1
        d = x - self.mean
        self.mean += d / self.n
        self.m2 += d * (x - self.mean)
        self.min = min(self.min, x)
        self.max = max(self.max, x)

    def as_dict(self, digits: int = 3) -> dict:
        if self.n == 0:
            return {"n": 0}
        std = math.sqrt(self.m2 / (self.n - 1)) if self.n > 1 else 0.0
        return {"n": self.n, "mean": round(self.mean, digits), "std": round(std, digits),
                "min": round(self.min, digits), "max": round(self.max, digits)}


# =============================================================================
# CORRELATOR
# =============================================================================
class Correlator:
    """
    Streaming matcher. Feed TX and RX in any order; call pump() periodically
    (live) or finish() once (offline).

    Clock alignment: the STM32 (ms since boot) and TSMaster run on different
    clocks. For every RX we take the time difference to every TX with the same
    ID + payload; the true offset forms one sharp peak in that histogram while
    coincidental pairs (repeated payloads) spread out. After lock the offset
    follows slow crystal drift with a small EWMA.
    """

    MIN_RX_FOR_LOCK = 40
    MIN_TX_FOR_LOCK = 40

    def __init__(self, db: Optional[SignalDb] = None, window_ms: float = 30.0,
                 rx_hold_s: float = 2.0, keep_events: int = 200_000):
        self.db = db
        self.window_ms = window_ms
        self.rx_hold_s = rx_hold_s
        self.keep_events = keep_events
        self.lock = threading.RLock()
        self.configs: Dict[int, dict] = {}     # spn -> config sent with CONFIG
        self.reset()

    # ------------------------------------------------------------------ setup
    def reset(self):
        with getattr(self, "lock", threading.RLock()):
            self.offset_ms: Optional[float] = None
            self.t0_ms: Optional[float] = None
            self.pending: Dict[int, Deque[TxRecord]] = {}
            self.rx_queue: Deque[RxRecord] = deque()
            self.lock_tx: List[TxRecord] = []
            self.tx_ids: set = set()
            self.last_seq: Optional[int] = None
            self.last_tx_ms: Optional[float] = None
            self.gaps: List[Tuple[float, float, int]] = []
            self.counts = Counter()
            self.latency = Welford()
            self.rx_period: Dict[int, Welford] = {}
            self.tx_period: Dict[int, Welford] = {}
            self.last_rx_t: Dict[int, float] = {}
            self.last_tx_t: Dict[int, float] = {}
            self.spn_stats: Dict[int, Counter] = {}
            self.spn_last: Dict[int, dict] = {}
            self.events: Deque[Event] = deque(maxlen=self.keep_events)
            self.drift_sums = [0.0, 0.0, 0.0, 0.0, 0]   # sx, sy, sxx, sxy, n

    def set_configs(self, signals: Iterable[dict]):
        with self.lock:
            self.configs = {int(s["spn"]): dict(s) for s in signals}

    # ------------------------------------------------------------------ input
    def feed_line(self, line: str) -> bool:
        """Feed one UART line. Returns True if it was a machine-readable marker."""
        tx = parse_tx_line(line)
        if tx:
            self.add_tx(tx)
            return True
        m = RUN_RE.search(line)
        if m:
            with self.lock:
                self.t0_ms = float(m.group(1))
                self.counts["runs"] += 1
            return True
        m = END_RE.search(line)
        if m:
            with self.lock:
                self.counts["ends"] += 1
            return True
        return False

    def add_tx(self, tx: TxRecord):
        with self.lock:
            if self.last_seq is not None and tx.seq > self.last_seq + 1:
                missing = tx.seq - self.last_seq - 1
                self.gaps.append((self.last_tx_ms or tx.t_ms, tx.t_ms, missing))
                self.counts["tx_log_gap_lines"] += missing
            self.last_seq = tx.seq
            self.last_tx_ms = tx.t_ms
            self.counts["tx_logged"] += 1

            if tx.status != "Q":
                self.counts["tx_busy" if tx.status == "B" else "tx_error"] += 1
                return
            self.counts["tx_queued"] += 1
            self.tx_ids.add(tx.can_id)

            prev = self.last_tx_t.get(tx.can_id)
            if prev is not None:
                self.tx_period.setdefault(tx.can_id, Welford()).add(tx.t_ms - prev)
            self.last_tx_t[tx.can_id] = tx.t_ms

            self.pending.setdefault(tx.can_id, deque()).append(tx)
            if self.offset_ms is None:
                self.lock_tx.append(tx)

    def add_rx(self, rx: RxRecord):
        with self.lock:
            if not rx.arrived_wall:
                rx.arrived_wall = time.monotonic()
            self.counts["rx_total"] += 1
            self.rx_queue.append(rx)

    # ------------------------------------------------------------------ engine
    def _try_lock_offset(self) -> bool:
        if len(self.rx_queue) < self.MIN_RX_FOR_LOCK or len(self.lock_tx) < self.MIN_TX_FOR_LOCK:
            return False
        index: Dict[Tuple[int, bytes], List[float]] = {}
        for tx in self.lock_tx:
            index.setdefault((tx.can_id, tx.data), []).append(tx.t_ms)

        hist: Counter = Counter()
        deltas: List[Tuple[float, float]] = []
        for rx in list(self.rx_queue)[:400]:
            cands = index.get((rx.can_id, rx.data))
            if not cands:
                continue
            w = 1.0 / len(cands)          # unique payloads vote strongest
            for t in cands:
                d = rx.t_ms - t
                hist[round(d)] += w
                deltas.append((d, w))
        if not hist:
            return False
        peak = max(hist, key=lambda k: sum(hist.get(k + j, 0.0) for j in (-1, 0, 1)))
        near = sorted(d for d, _ in deltas if abs(d - peak) <= 2.0)
        self.offset_ms = near[len(near) // 2] if near else float(peak)
        self.lock_tx.clear()
        return True

    def _in_gap(self, t_tx_ms: float) -> bool:
        return any(a - self.window_ms <= t_tx_ms <= b + self.window_ms for a, b, _ in self.gaps)

    def _record(self, ev: Event):
        self.counts[ev.verdict.lower()] += 1
        self.events.append(ev)

    def _check_spns(self, tx: TxRecord, rx: RxRecord) -> List[dict]:
        if self.db is None or not self.configs:
            return []
        pgn = pgn_from_id(rx.can_id)
        out = []
        for spn, cfg in self.configs.items():
            sig = self.db.by_spn.get(spn)
            if sig is None or sig["pgn"] != pgn:
                continue
            raw = SignalDb.raw_value(sig, rx.data)
            phys = raw * sig["resolution"] + sig["offset"]
            res = {"spn": spn, "name": sig["name"], "unit": sig["unit"],
                   "value": round(phys, 4), "raw": raw}
            in_range = sig["min_physical"] - sig["resolution"] <= phys <= sig["max_physical"] + sig["resolution"]
            verdict = "RANGE_OK" if in_range else "OUT_OF_RANGE"

            if self.t0_ms is not None:
                t_sec = (tx.t_ms - self.t0_ms) / 1000.0
                expected = pattern_value(cfg, t_sec)
                if expected is not None:
                    # +-1 ms covers float32 vs float64 rounding at square/step edges
                    exp_raws = set()
                    for dt in (0.0, -0.001, 0.001):
                        v = pattern_value(cfg, t_sec + dt)
                        exp_raws.add(SignalDb.encode_raw(sig, v))
                    ok = any(abs(raw - e) <= 1 for e in exp_raws)
                    verdict = "VALUE_OK" if ok else "VALUE_FAIL"
                    res["expected"] = round(expected, 4)
            res["verdict"] = verdict
            self.spn_stats.setdefault(spn, Counter())[verdict] += 1
            self.spn_last[spn] = res
            out.append(res)
        return out

    def _match_rx(self, rx: RxRecord):
        if rx.can_id not in self.tx_ids:
            self.counts["rx_foreign"] += 1
            return
        prev = self.last_rx_t.get(rx.can_id)
        if prev is not None:
            self.rx_period.setdefault(rx.can_id, Welford()).add(rx.t_ms - prev)
        self.last_rx_t[rx.can_id] = rx.t_ms

        expect_tx = rx.t_ms - self.offset_ms
        # Never pair across a neighbouring frame of the same ID: one wrong pair
        # would shift every following pair by one period.
        win = self.window_ms
        txp = self.tx_period.get(rx.can_id)
        if txp is not None and txp.n:
            win = min(win, 0.5 * txp.mean)
        q = self.pending.get(rx.can_id, deque())
        best_i, best_err, near_i, near_err = None, None, None, None
        for i, tx in enumerate(q):
            err = expect_tx - tx.t_ms
            if err < -win:
                break
            if abs(err) > win:
                continue
            if tx.data == rx.data and (best_err is None or abs(err) < abs(best_err)):
                best_i, best_err = i, err
            if near_err is None or abs(err) < abs(near_err):
                near_i, near_err = i, err

        if best_i is not None:
            tx = q[best_i]
            del q[best_i]
            self.latency.add(best_err)
            self.offset_ms += 0.02 * best_err            # track crystal drift
            s = self.drift_sums
            d = rx.t_ms - tx.t_ms
            s[0] += tx.t_ms; s[1] += d; s[2] += tx.t_ms * tx.t_ms; s[3] += tx.t_ms * d; s[4] += 1
            self._record(Event("MATCH", rx.can_id, tx, rx, round(best_err, 3),
                               spn_checks=self._check_spns(tx, rx)))
        elif self._in_gap(expect_tx):
            self._record(Event("UNLOGGED", rx.can_id, None, rx))
        elif near_i is not None:
            tx = q[near_i]
            del q[near_i]
            diff = [i for i in range(8) if tx.data[i] != rx.data[i]]
            self._record(Event("MISMATCH", rx.can_id, tx, rx, round(near_err, 3), diff))
        else:
            self._record(Event("UNEXPECTED", rx.can_id, None, rx))

    def _sweep_lost(self, horizon_tx_ms: float):
        for can_id, q in self.pending.items():
            while q and q[0].t_ms < horizon_tx_ms - self.window_ms:
                self._record(Event("LOST", can_id, q.popleft(), None))

    def pump(self, force: bool = False):
        with self.lock:
            if self.offset_ms is None and not self._try_lock_offset():
                if force:
                    self.counts["unlocked_rx"] += len(self.rx_queue)
                    self.rx_queue.clear()
                return
            now = time.monotonic()
            last_rx_tx_ms = None
            while self.rx_queue:
                rx = self.rx_queue[0]
                ready = (self.last_tx_ms is not None and
                         rx.t_ms - self.offset_ms + self.window_ms <= self.last_tx_ms)
                if not (ready or force or now - rx.arrived_wall > self.rx_hold_s):
                    break
                self.rx_queue.popleft()
                self._match_rx(rx)
                last_rx_tx_ms = rx.t_ms - self.offset_ms
            if last_rx_tx_ms is not None:
                self._sweep_lost(last_rx_tx_ms)

    def finish(self):
        """Offline: process everything; TX after the last RX are not counted lost."""
        self.pump(force=True)

    # ------------------------------------------------------------------ output
    def summary(self) -> dict:
        with self.lock:
            c = self.counts
            judged = c["match"] + c["lost"] + c["mismatch"]
            s = self.drift_sums
            drift_ppm = None
            if s[4] > 10:
                den = s[4] * s[2] - s[0] * s[0]
                if den:
                    drift_ppm = round((s[4] * s[3] - s[0] * s[1]) / den * 1e6, 1)

            per_id = []
            for can_id in sorted(self.tx_ids):
                txp = self.tx_period.get(can_id, Welford()).as_dict()
                rxp = self.rx_period.get(can_id, Welford()).as_dict()
                per_id.append({"can_id": f"0x{can_id:08X}", "pgn": pgn_from_id(can_id),
                               "tx_period_ms": txp, "rx_period_ms": rxp})

            spns = []
            for spn, cnt in sorted(self.spn_stats.items()):
                last = self.spn_last.get(spn, {})
                total = sum(cnt.values())
                fails = cnt["VALUE_FAIL"] + cnt["OUT_OF_RANGE"]
                spns.append({"spn": spn, "name": last.get("name"), "checked": total,
                             "value_ok": cnt["VALUE_OK"], "range_only": cnt["RANGE_OK"],
                             "fail": fails, "last": last})

            return {
                "clock_locked": self.offset_ms is not None,
                "offset_ms": round(self.offset_ms, 3) if self.offset_ms is not None else None,
                "run_t0_ms": self.t0_ms,
                "tx": {"logged": c["tx_logged"], "queued": c["tx_queued"], "busy": c["tx_busy"],
                       "error": c["tx_error"], "log_gap_lines": c["tx_log_gap_lines"]},
                "rx": {"total": c["rx_total"], "foreign": c["rx_foreign"],
                       "waiting": len(self.rx_queue)},
                "verdicts": {"match": c["match"], "lost": c["lost"], "mismatch": c["mismatch"],
                             "unexpected": c["unexpected"], "unlogged": c["unlogged"]},
                "match_rate_pct": round(100.0 * c["match"] / judged, 3) if judged else None,
                "latency_jitter_ms": self.latency.as_dict(),
                "clock_drift_ppm": drift_ppm,
                "per_id": per_id,
                "spn_checks": spns,
            }

    def recent_events(self, limit: int = 100, only_errors: bool = False) -> List[dict]:
        with self.lock:
            evs = [e for e in self.events if not only_errors or e.verdict != "MATCH"]
            return [self._event_row(e) for e in evs[-limit:]]

    @staticmethod
    def _event_row(e: Event) -> dict:
        return {
            "verdict": e.verdict,
            "can_id": f"0x{e.can_id:08X}",
            "pgn": pgn_from_id(e.can_id),
            "seq": e.tx.seq if e.tx else None,
            "t_tx_ms": e.tx.t_ms if e.tx else None,
            "t_rx_ms": round(e.rx.t_ms, 3) if e.rx else None,
            "tx_data": e.tx.data.hex(" ").upper() if e.tx else "",
            "rx_data": e.rx.data.hex(" ").upper() if e.rx else "",
            "latency_ms": e.latency_ms,
            "diff_bytes": e.diff_bytes,
            "spn": "; ".join(f"{s['spn']}={s['value']}{'' if 'expected' not in s else '/exp ' + str(s['expected'])}"
                             f" {s['verdict']}" for s in e.spn_checks),
        }

    def write_csv(self, path: Path):
        with self.lock:
            rows = [self._event_row(e) for e in self.events]
        with open(path, "w", newline="", encoding="utf-8") as f:
            fields = ["verdict", "seq", "can_id", "pgn", "t_tx_ms", "t_rx_ms", "latency_ms",
                      "tx_data", "rx_data", "diff_bytes", "spn"]
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)


# =============================================================================
# RX LOG READERS (TSMaster exports / bridge JSON lines)
# =============================================================================
ASC_RE = re.compile(
    r"^\s*([\d.]+)\s+\d+\s+([0-9A-Fa-f]+)x\s+Rx\s+d\s+(\d+)((?:\s+[0-9A-Fa-f]{2})+)", re.IGNORECASE)


def read_rx_file(path: Path) -> List[RxRecord]:
    """Vector/TSMaster .asc (absolute or relative timestamps, seconds) or .jsonl from the bridge."""
    out: List[RxRecord] = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("{"):
                p = json.loads(line)
                ts = float(p.get("timestamp_us", float(p.get("timestamp", 0.0)) * 1e6)) / 1000.0
                out.append(RxRecord(ts, int(str(p["id"]), 16), bytes.fromhex(p["data"]), 1.0))
                continue
            m = ASC_RE.match(line)
            if m:
                t_s, can_id, dlc, data = m.groups()
                b = bytes(int(x, 16) for x in data.split()[: int(dlc)])
                out.append(RxRecord(float(t_s) * 1000.0, int(can_id, 16), b.ljust(8, b"\xff"), 1.0))
    return out


def main():
    ap = argparse.ArgumentParser(description="Correlate STM32 $TX log with TSMaster RX log")
    ap.add_argument("--tx", required=True, type=Path, help="UART log containing $TX/$RUN lines")
    ap.add_argument("--rx", required=True, type=Path, help="TSMaster .asc export or bridge .jsonl")
    ap.add_argument("--config", type=Path, help="JSON list of CONFIG signals (enables value check)")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--window-ms", type=float, default=30.0)
    ap.add_argument("--csv", type=Path, help="Write per-frame verdicts")
    args = ap.parse_args()

    corr = Correlator(SignalDb(args.db), window_ms=args.window_ms)
    if args.config:
        cfg = json.loads(args.config.read_text(encoding="utf-8"))
        corr.set_configs(cfg["signals"] if isinstance(cfg, dict) else cfg)

    with open(args.tx, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            corr.feed_line(line)
    for rx in read_rx_file(args.rx):
        corr.add_rx(rx)
    corr.finish()

    print(json.dumps(corr.summary(), indent=2, ensure_ascii=False))
    if args.csv:
        corr.write_csv(args.csv)
        print(f"Per-frame report: {args.csv}")


if __name__ == "__main__":
    main()
