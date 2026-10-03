"""Regenerate host/data/j1939_spn_database.json from the firmware signal table
(MID/j1939_signal_definitions.[ch]) so web, correlator and STM32 share one source."""
import re
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # host/ -> import j1939hub

from j1939hub.paths import FIRMWARE_MID_DIR, SPN_DB_PATH

with open(FIRMWARE_MID_DIR / "j1939_signal_definitions.h", "r", encoding="utf-8") as f:
    h_content = f.read()

pgn_map = {}
for m in re.finditer(r"#define\s+(PGN_\w+)\s+(\d+)u?", h_content):
    pgn_map[m.group(1)] = int(m.group(2))

spn_map = {}
for m in re.finditer(r"#define\s+(SPN_\w+)\s+(\d+)u?", h_content):
    spn_map[m.group(1)] = int(m.group(2))

with open(FIRMWARE_MID_DIR / "j1939_signal_definitions.c", "r", encoding="utf-8") as f:
    c_content = f.read()

signals = []
block_regex = re.compile(
    r"\.pgn\s*=\s*([A-Za-z0-9_]+).*?"
    r"\.spn\s*=\s*([A-Za-z0-9_]+).*?"
    r'\.name\s*=\s*"([^"]+)".*?'
    r'\.unit\s*=\s*"([^"]*)".*?'
    r"\.start_byte\s*=\s*(\d+).*?"
    r"\.start_bit\s*=\s*(\d+).*?"
    r"\.num_bits\s*=\s*(\d+).*?"
    r"\.resolution\s*=\s*([0-9.efF\-]+).*?"
    r"\.offset\s*=\s*([0-9.efF\-]+).*?"
    r"\.min_physical\s*=\s*([0-9.efF\-]+).*?"
    r"\.max_physical\s*=\s*([0-9.efF\-]+)",
    re.DOTALL
)

def categorize(pgn_name, spn_name, name):
    name_l = name.lower()
    if any(k in name_l for k in ["engine speed", "torque", "crank", "rpm", "accel", "pedal pos", "percent load"]):
        return "Engine Control"
    elif any(k in name_l for k in ["trans", "gear"]):
        return "Transmission"
    elif any(k in name_l for k in ["brake", "retarder"]):
        return "Brakes"
    elif any(k in name_l for k in ["vehicle speed", "steering", "yaw", "lateral accel", "long accel"]):
        return "Vehicle Motion"
    elif any(k in name_l for k in ["scr", "dpf", "dosing", "soot"]):
        return "Aftertreatment (SCR/DPF)"
    elif any(k in name_l for k in ["temp", "oil", "coolant", "fuel rate", "economy", "intake", "fluid", "pressure", "baro", "water in fuel"]):
        return "Fluids & Temperatures"
    elif any(k in name_l for k in ["battery", "volt", "amp", "fan", "dash", "hour", "day", "month", "year", "second", "minute", "washer"]):
        return "Electrical & Dashboard"
    return "General"

for block in re.finditer(r"\{([^{}]+)\}", c_content):
    m = block_regex.search(block.group(1))
    if m:
        pgn_name = m.group(1)
        spn_name = m.group(2)
        name = m.group(3)
        signals.append({
            "pgn_name": pgn_name,
            "pgn": pgn_map.get(pgn_name, 0),
            "spn_name": spn_name,
            "spn": spn_map.get(spn_name, 0),
            "name": name,
            "unit": m.group(4),
            "start_byte": int(m.group(5)),
            "start_bit": int(m.group(6)),
            "num_bits": int(m.group(7)),
            "resolution": float(m.group(8).rstrip("fF")),
            "offset": float(m.group(9).rstrip("fF")),
            "min_physical": float(m.group(10).rstrip("fF")),
            "max_physical": float(m.group(11).rstrip("fF")),
            "category": categorize(pgn_name, spn_name, name)
        })

with open(SPN_DB_PATH, "w", encoding="utf-8") as out:
    json.dump(signals, out, indent=2)

print(f"Generated {SPN_DB_PATH} with {len(signals)} signals.")
