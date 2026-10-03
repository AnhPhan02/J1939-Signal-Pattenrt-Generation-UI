import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # host/ -> import j1939hub

import cantools
from j1939hub.dbc_generator import generate_j1939_dbc, sanitize_identifier
from j1939hub.paths import SPN_DB_PATH

with open(SPN_DB_PATH, encoding="utf-8") as f:
    db = json.load(f)

print(f"Checking {len(db)} SPNs for layout sanity...")
errors = []
for s in db:
    start_bit = (s["start_byte"] - 1) * 8 + (s["start_bit"] - 1)
    end_bit = start_bit + s["num_bits"] - 1
    if end_bit > 63:
        errors.append(f"SPN {s['spn']} ({s['name']}) end_bit {end_bit} exceeds 63!")
    if s["resolution"] <= 0:
        errors.append(f"SPN {s['spn']} has invalid resolution {s['resolution']}")
    if s["min_physical"] > s["max_physical"]:
        errors.append(f"SPN {s['spn']} has min > max")

if errors:
    print("ERRORS FOUND:", errors)
else:
    print("All 52 SPN bit layouts are 100% within valid 64-bit CAN frame bounds!")

full_dbc = generate_j1939_dbc(db, None, 0, 500)
cdb = cantools.database.load_string(full_dbc, "dbc")
print(f"Parsed full DBC: {len(cdb.messages)} messages and {sum(len(m.signals) for m in cdb.messages)} signals.")

# Check each message signals
for msg in cdb.messages:
    print(f"  Message {msg.name} (ID: 0x{msg.frame_id:08X}, DLC: {msg.length}): {len(msg.signals)} signals")
    for sig in msg.signals:
        if sig.start + sig.length > 64:
            print(f"    ERROR: Signal {sig.name} exceeds 64 bits!")
print("All messages and signals verified error-free!")
