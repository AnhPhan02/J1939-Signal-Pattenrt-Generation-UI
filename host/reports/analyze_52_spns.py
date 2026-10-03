import json
import re
import pandas as pd
from pathlib import Path

# Load J1939 database
with open(Path(__file__).resolve().parents[1] / "data" / "j1939_spn_database.json", "r", encoding="utf-8") as f:
    db_spns = json.load(f)

print(f"Total SPNs in Database: {len(db_spns)}")

# Group SPNs by PGN
pgn_to_spns = {}
for item in db_spns:
    p = item["pgn"]
    if p not in pgn_to_spns:
        pgn_to_spns[p] = []
    pgn_to_spns[p].append(item)

print(f"Total Unique PGNs: {len(pgn_to_spns)}")

def extract_signal_raw(data_bytes, start_byte, start_bit, num_bits):
    if len(data_bytes) < 8:
        data_bytes = data_bytes + b'\x00' * (8 - len(data_bytes))
    bit_offset = (start_byte - 1) * 8 + (start_bit - 1)
    val64 = int.from_bytes(data_bytes[:8], byteorder='little')
    mask = (1 << num_bits) - 1
    raw_val = (val64 >> bit_offset) & mask
    return raw_val

def decode_spn_value(data_bytes, spn_def):
    raw = extract_signal_raw(data_bytes, spn_def["start_byte"], spn_def["start_bit"], spn_def["num_bits"])
    phys = raw * spn_def["resolution"] + spn_def["offset"]
    return raw, phys

# Parse PCAN TRC File
trc_path = Path("new verification system - logs comp/today2.trc")
trc_frames = []
trc_pgn_frames = {p: [] for p in pgn_to_spns}

with open(trc_path, "r") as f:
    for line in f:
        if line.startswith(";") or not line.strip():
            continue
        parts = line.strip().split()
        if len(parts) >= 5:
            try:
                t_ms = float(parts[1])
                can_id = int(parts[3], 16)
                dlc = int(parts[4])
                hex_str = "".join(parts[5:5+dlc])
                data_bytes = bytes.fromhex(hex_str)
                
                pf = (can_id >> 16) & 0xFF
                if pf < 240:
                    pgn = (can_id >> 8) & 0x3FF00
                else:
                    pgn = (can_id >> 8) & 0x3FFFF
                    
                frame = {
                    "time_ms": t_ms,
                    "can_id": can_id,
                    "pgn": pgn,
                    "dlc": dlc,
                    "data_bytes": data_bytes,
                    "hex_str": hex_str
                }
                trc_frames.append(frame)
                if pgn in trc_pgn_frames:
                    trc_pgn_frames[pgn].append(frame)
            except Exception as e:
                pass

print(f"Parsed TRC Frames: {len(trc_frames)}")

# Parse Website TXT File
txt_path = Path("new verification system - logs comp/j1939_can_bus_trace_log_1789371055974.txt")
txt_frames = []
txt_pgn_frames = {p: [] for p in pgn_to_spns}

with open(txt_path, "r") as f:
    for line in f:
        line_str = line.strip()
        if not line_str or line_str.startswith("=") or line_str.startswith("-") or line_str.startswith("#") or "INTELLIGENT" in line_str or "Creators" in line_str or "Institution" in line_str or "Log Timestamp" in line_str or "CAN Bus" in line_str or "Timing" in line_str or "Total Logged" in line_str:
            continue
        parts = re.split(r'\s+', line_str)
        if len(parts) >= 7 and parts[3].startswith("0x"):
            try:
                frame_num = int(parts[0])
                t_ms = float(parts[1])
                can_id = int(parts[3], 16)
                pgn = int(parts[4])
                dlc = int(parts[5])
                hex_bytes = parts[6:6+dlc]
                data_bytes = bytes.fromhex("".join(hex_bytes))
                
                frame = {
                    "frame_num": frame_num,
                    "time_ms": t_ms,
                    "can_id": can_id,
                    "pgn": pgn,
                    "dlc": dlc,
                    "data_bytes": data_bytes,
                    "hex_str": "".join(hex_bytes),
                    "raw_line": line_str
                }
                txt_frames.append(frame)
                if pgn in txt_pgn_frames:
                    txt_pgn_frames[pgn].append(frame)
            except Exception as e:
                pass

print(f"Parsed Website TXT Frames: {len(txt_frames)}")

spn_summary = []
for item in db_spns:
    spn_num = item["spn"]
    spn_name = item["name"]
    pgn = item["pgn"]
    pgn_name = item["pgn_name"]
    
    trc_count = len(trc_pgn_frames.get(pgn, []))
    txt_count = len(txt_pgn_frames.get(pgn, []))
    
    trc_vals = []
    for f in trc_pgn_frames.get(pgn, []):
        raw, phys = decode_spn_value(f["data_bytes"], item)
        trc_vals.append(phys)
        
    txt_vals = []
    for f in txt_pgn_frames.get(pgn, []):
        raw, phys = decode_spn_value(f["data_bytes"], item)
        txt_vals.append(phys)
        
    trc_range = f"{min(trc_vals):.2f} .. {max(trc_vals):.2f}" if trc_vals else "N/A"
    txt_range = f"{min(txt_vals):.2f} .. {max(txt_vals):.2f}" if txt_vals else "N/A"
    
    valid_range = item["min_physical"] <= min(trc_vals) and max(trc_vals) <= item["max_physical"] if trc_vals else True
    status = "PASS" if (trc_count > 0 and txt_count > 0 and valid_range) else "FAIL"
    
    spn_summary.append({
        "SPN": spn_num,
        "Name": spn_name,
        "PGN": pgn,
        "PGN Name": pgn_name,
        "TRC Frames": trc_count,
        "TXT Frames": txt_count,
        "TRC Range": trc_range,
        "TXT Range": txt_range,
        "Status": status
    })

df_spn = pd.DataFrame(spn_summary)
pd.set_option('display.max_rows', 100)
pd.set_option('display.max_columns', 10)
pd.set_option('display.width', 1000)
print("\n--- ALL 52 SPNS SUMMARY TABLE ---")
print(df_spn)
