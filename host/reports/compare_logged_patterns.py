import os
import csv
import glob
import re
import sys
import math
from pathlib import Path
import pandas as pd
import numpy as np

# host/ on the path so we can import J1939Database
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from j1939hub.live_verifier import J1939Database

# =============================================================================
# STRESS TESTING & TIMING CONFIGURATIONS (Must match the Arduino firmware)
# =============================================================================
PATTERN_DURATION_MS = 500000.0  # High-resolution pattern duration: 500 seconds (8.3 minutes)
MARKER_DURATION_MS = 3000.0     # Duration of start/end markers in ms

# SPN configurations matching standard J1939 definitions
SPN_CONFIGS = [
    {
        "key": "SPN190",
        "spn_num": 190,
        "name": "Engine Speed",
        "col_pattern": re.compile(r"190|engine.*speed|speed", re.I),
        "sheet_name": "SPN190_Engine_Speed",
        "period": 20.0,
        "max_val": 8031.875,
        "res": 0.125,
        "offset": 0.0,
        "decimals": 3
    },
    {
        "key": "SPN91",
        "spn_num": 91,
        "name": "Accelerator Pedal Position 1",
        "col_pattern": re.compile(r"91|accel|pedal.*pos", re.I),
        "sheet_name": "SPN91_Accelerator",
        "period": 50.0,
        "max_val": 100.0,
        "res": 0.4,
        "offset": 0.0,
        "decimals": 1
    },
    {
        "key": "SPN521",
        "spn_num": 521,
        "name": "Brake Pedal Position",
        "col_pattern": re.compile(r"521|brake", re.I),
        "sheet_name": "SPN521_Brake_Pedal",
        "period": 100.0,
        "max_val": 100.0,
        "res": 0.4,
        "offset": 0.0,
        "decimals": 1
    },
    {
        "key": "SPN84",
        "spn_num": 84,
        "name": "Wheel-Based Vehicle Speed",
        "col_pattern": re.compile(r"84|vehicle|wheel", re.I),
        "sheet_name": "SPN84_Vehicle_Speed",
        "period": 100.0,
        "max_val": 250.996,
        "res": 0.00390625,
        "offset": 0.0,
        "decimals": 8
    },
    {
        "key": "SPN183",
        "spn_num": 183,
        "name": "Engine Fuel Rate",
        "col_pattern": re.compile(r"183|fuel_rate|fuel rate", re.I),
        "sheet_name": "SPN183_Fuel_Rate",
        "period": 100.0,
        "max_val": 3212.75,
        "res": 0.05,
        "offset": 0.0,
        "decimals": 2
    },
    {
        "key": "SPN184",
        "spn_num": 184,
        "name": "Instantaneous Fuel Economy",
        "col_pattern": re.compile(r"184|inst_fuel|instantaneous", re.I),
        "sheet_name": "SPN184_Inst_Fuel_Economy",
        "period": 100.0,
        "max_val": 125.5,
        "res": 0.001953125,
        "offset": 0.0,
        "decimals": 9
    },
    {
        "key": "SPN185",
        "spn_num": 185,
        "name": "Average Fuel Economy",
        "col_pattern": re.compile(r"185|avg_fuel|average", re.I),
        "sheet_name": "SPN185_Avg_Fuel_Economy",
        "period": 100.0,
        "max_val": 125.5,
        "res": 0.001953125,
        "offset": 0.0,
        "decimals": 9
    }
]

# Decimal places mapping for standard conformance
DECIMAL_PLACES = {cfg["key"]: cfg["decimals"] for cfg in SPN_CONFIGS}

# =============================================================================
# DYNAMIC MATHEMATICAL WAVEFORM FUNCTIONS (Based on elapsed time in ms)
# =============================================================================
def get_ramp_value(elapsed_ms, max_val, duration_ms=60000.0):
    """Computes exact mathematical triangle ramp value matching firmware."""
    if elapsed_ms <= 0.0:
        return 0.0
    half = duration_ms / 2.0
    if elapsed_ms < half:
        return max_val * elapsed_ms / half
    elif elapsed_ms < duration_ms:
        return max_val - max_val * (elapsed_ms - half) / half
    else:
        return 0.0

def find_target_csv():
    """Finds target log CSV file (prefers commandline arg or newest CSV)."""
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        return sys.argv[1]
    if os.path.exists("normal report log 2.CSV"):
        return "normal report log 2.CSV"
    if os.path.exists("normal report log 2.csv"):
        return "normal report log 2.csv"
    csv_files = glob.glob("*.csv") + glob.glob("*.CSV")
    csv_files = [f for f in csv_files if "comparison_results" not in f.lower()]
    if not csv_files:
        raise FileNotFoundError("No log CSV files found in the current directory.")
    csv_files.sort(key=os.path.getmtime, reverse=True)
    return csv_files[0]

def parse_raw_payload(raw_data_str):
    try:
        parts = raw_data_str.strip().split()
        if 'D' in parts:
            d_idx = parts.index('D')
            dlc = int(parts[d_idx + 1])
            hex_bytes = parts[d_idx + 2 : d_idx + 2 + dlc]
            return bytes.fromhex("".join(hex_bytes))
    except Exception:
        pass
    return None

def parse_logged_data(filepath, db):
    print(f"Reading log file: {filepath}")
    
    # Map DBC signals
    sig_map = {}
    for msg in db.messages:
        for sig in msg.signals:
            name_low = sig.name.lower()
            if "engine_speed" in name_low:
                sig_map["SPN190"] = sig
            elif "accel_pedal" in name_low:
                sig_map["SPN91"] = sig
            elif "brake_pedal" in name_low:
                sig_map["SPN521"] = sig
            elif "vehicle_speed" in name_low:
                sig_map["SPN84"] = sig
            elif "fuel_rate" in name_low:
                sig_map["SPN183"] = sig
            elif "inst_fuel" in name_low:
                sig_map["SPN184"] = sig
            elif "avg_fuel" in name_low:
                sig_map["SPN185"] = sig

    col_indices = {}
    time_col_idx = 0
    raw_col_idx = -1

    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f)
        header = next(reader)
        clean_header = [h.strip() for h in header]

        for i, col in enumerate(clean_header):
            col_low = col.lower()
            if "time" in col_low and "global" not in col_low:
                time_col_idx = i
            elif "raw data" in col_low or "raw_data" in col_low:
                raw_col_idx = i

        for cfg in SPN_CONFIGS:
            k = cfg["key"]
            for i, col in enumerate(clean_header):
                if "expected" in col.lower() or "ref" in col.lower() or "raw" in col.lower() or "time" in col.lower():
                    continue
                if cfg["col_pattern"].search(col):
                    col_indices[k] = i
                    break

        logged_series = {cfg["key"]: [] for cfg in SPN_CONFIGS}

        for row in reader:
            if not row or len(row) <= max(col_indices.values()):
                continue

            try:
                t_val = float(row[time_col_idx])
            except ValueError:
                continue

            raw_payload = None
            if raw_col_idx != -1 and raw_col_idx < len(row):
                raw_payload = parse_raw_payload(row[raw_col_idx])

            for cfg in SPN_CONFIGS:
                k = cfg["key"]
                if k not in col_indices:
                    continue
                idx = col_indices[k]
                val_str = row[idx].strip()
                if val_str:
                    try:
                        phys_val = float(val_str)
                        sig_def = sig_map.get(k)
                        raw_val = 0
                        if raw_payload is not None and sig_def is not None:
                            try:
                                parts = row[raw_col_idx].strip().split()
                                id_part = [p for p in parts if p.startswith('$')]
                                if id_part:
                                    msg_id = int(id_part[0].replace('$', ''), 16) & 0x1FFFFFFF
                                    if msg_id == sig_def.can_identifier:
                                        raw_val, _ = sig_def.decode(raw_payload)
                                    else:
                                        raw_val = sig_def.encode(phys_val)
                                else:
                                    raw_val = sig_def.encode(phys_val)
                            except Exception:
                                raw_val = sig_def.encode(phys_val)
                        elif sig_def is not None:
                            raw_val = sig_def.encode(phys_val)

                        logged_series[k].append((t_val, phys_val, raw_val))
                    except ValueError:
                        pass

    return logged_series, sig_map

def main():
    dbc_path = Path(__file__).parent / "STM32F103_J1939_Signal_Generator.dbc"
    db = J1939Database(dbc_path)
    
    try:
        target_file = find_target_csv()
    except FileNotFoundError as e:
        print(e)
        return
        
    log_basename = Path(target_file).stem.replace(" ", "_")
    logged_series, sig_map = parse_logged_data(target_file, db)
    
    # =============================================================================
    # WRITE EXCEL WORKBOOK (Each SPN has its own separate tab, filtering lead/trail zeros)
    # =============================================================================
    excel_filename = f"comparison_results_{log_basename}.xlsx"
    try:
        test_f = open(excel_filename, "a")
        test_f.close()
    except PermissionError:
        excel_filename = f"comparison_results_{log_basename}_new.xlsx"
        
    print(f"\nWriting multi-page Excel comparison workbook: {excel_filename}")
    
    with pd.ExcelWriter(excel_filename, engine="openpyxl") as writer:
        for cfg in SPN_CONFIGS:
            k = cfg["key"]
            frames = logged_series[k]
            
            # Isolate the active ramp between the start marker (0.0) and end marker (0.0)
            first_ramp_idx = 0
            for i in range(len(frames)):
                if frames[i][1] == 0 and i + 1 < len(frames) and frames[i+1][1] > 0:
                    first_ramp_idx = i + 1
            last_ramp_idx = len(frames) - 1
            for i in range(len(frames) - 1, -1, -1):
                if frames[i][1] == 0 and i - 1 >= 0 and frames[i-1][1] > 0:
                    last_ramp_idx = i - 1
                    break

            active_frames = [(t, p, r) for t, p, r in frames[first_ramp_idx : last_ramp_idx + 1] if p > 0]
            
            if not active_frames:
                print(f"  Warning: No active non-zero data for {k}")
                continue
                
            t_min = active_frames[0][0]
            t_max = active_frames[-1][0]
            max_v = cfg["max_val"]
            decimals = cfg["decimals"]
            sig_def = sig_map.get(k)
            
            # Calibrate hardware timer start and end bounds with CAN wire delay compensation (0.512 ms)
            WIRE_DELAY_SEC = 0.000512  # 128 bit times at 250 kbps
            
            # Auto-detect test duration: 60s stress test vs 500s standard test
            active_span_sec = t_max - t_min
            if active_span_sec < 120.0:
                pattern_dur_ms = 60000.0
            else:
                pattern_dur_ms = 500000.0
                
            best_st, best_et = t_min, t_max
            min_e = 1e9
            sample = active_frames[::max(1, len(active_frames)//100)]
            for st in np.linspace(t_min - 0.2, t_min + 0.05, 50):
                for et in np.linspace(t_max - 0.05, t_max + 0.2, 50):
                    errs = [abs(p - get_ramp_value((t - WIRE_DELAY_SEC - st)/(et - st)*pattern_dur_ms, max_v, pattern_dur_ms)) for t, p, r in sample]
                    mean_e = sum(errs)/len(errs)
                    if mean_e < min_e:
                        min_e = mean_e
                        best_st = st
                        best_et = et
            
            # Determine expected period in seconds (single-SPN stress vs multi-SPN stress vs standard)
            active_signals_count = sum(1 for c in SPN_CONFIGS if any(f[1] > 0 for f in logged_series[c["key"]]))
            if pattern_dur_ms == 60000.0:
                if active_signals_count == 1:
                    # Dynamically detect single-SPN transmission period from median delta (e.g., 1ms, 2ms, 3ms, 5ms)
                    deltas = [active_frames[i][0] - active_frames[i-1][0] for i in range(1, min(500, len(active_frames)))]
                    med_delta = float(np.median(deltas)) if deltas else 0.003
                    detected_ms = max(1, int(round(med_delta * 1000.0)))
                    period_sec = detected_ms / 1000.0
                else:
                    if k == "SPN190": period_sec = 0.005
                    elif k == "SPN91": period_sec = 0.010
                    else: period_sec = 0.020
            else:
                period_sec = cfg["period"] / 1000.0

            sno_list = []
            time_list = []
            raw_in_list = []
            raw_log_list = []
            phys_in_list = []
            phys_log_list = []
            diff_raw_list = []
            diff_phys_list = []
            
            total_expected_steps = int(round((best_et - best_st) / period_sec)) + 1
            frame_idx = 0
            missed_count = 0
            logged_count = 0
            
            for step in range(total_expected_steps):
                t_exp_tx = best_st + step * period_sec
                t_exp_rx = t_exp_tx + WIRE_DELAY_SEC
                elapsed_ms = (t_exp_tx - best_st) / (best_et - best_st) * pattern_dur_ms
                p_in = get_ramp_value(elapsed_ms, max_v, pattern_dur_ms)
                r_in = sig_def.encode(p_in) if sig_def is not None else int(round(p_in / cfg["res"]))
                p_in_rounded = round(p_in, decimals)
                
                # Check if next logged frame matches this expected cycle slot
                if frame_idx < len(active_frames) and abs((active_frames[frame_idx][0] - WIRE_DELAY_SEC) - t_exp_tx) < (period_sec * 0.7):
                    t_actual, p_log, r_log = active_frames[frame_idx]
                    frame_idx += 1
                    logged_count += 1
                    
                    p_log_rounded = round(p_log, decimals)
                    d_raw = r_log - r_in
                    d_phys = round(p_log_rounded - p_in_rounded, decimals)
                    
                    sno_list.append(step + 1)
                    time_list.append(round(t_actual, 6))
                    raw_in_list.append(r_in)
                    raw_log_list.append(r_log)
                    phys_in_list.append(p_in_rounded)
                    phys_log_list.append(p_log_rounded)
                    diff_raw_list.append(d_raw)
                    diff_phys_list.append(d_phys)
                else:
                    # Frame was missed / dropped on bus
                    missed_count += 1
                    sno_list.append(step + 1)
                    time_list.append(round(t_exp_rx, 6))
                    raw_in_list.append(r_in)
                    raw_log_list.append("Missed")
                    phys_in_list.append(p_in_rounded)
                    phys_log_list.append("Missed")
                    diff_raw_list.append("Missed")
                    diff_phys_list.append("Missed")
                
            df = pd.DataFrame({
                "S.No": sno_list,
                "Time": time_list,
                "Raw Value Input": raw_in_list,
                "Raw Value Logged": raw_log_list,
                "Physical Value Input": phys_in_list,
                "Physical Value Logged": phys_log_list,
                "Difference in Raw": diff_raw_list,
                "Difference in Physical": diff_phys_list
            })
            
            df.to_excel(writer, sheet_name=cfg["sheet_name"], index=False)
            print(f"  [{cfg['sheet_name']}] -> Total Expected: {total_expected_steps} | Logged: {logged_count} | Missed: {missed_count}")
            
    # Also save as comparison_results.xlsx for standard link
    try:
        import shutil
        shutil.copyfile(excel_filename, "comparison_results.xlsx")
    except Exception:
        pass
        
    print(f"\nReal Comparison Excel workbook successfully generated: '{excel_filename}'")

if __name__ == "__main__":
    main()
