"""
J1939 DBC Generator
Generates valid, SAE J1939-compliant Vector DBC CAN database files
reflecting active waveform pattern configurations and full SPN specifications.
Compatible with Vector CANoe/CANalyzer, TSMaster, SavvyCAN, PCAN, Kvaser, BusMaster, and cantools.
"""

import json
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

PGN_METADATA = {
    61444: {"name": "EEC1", "prio": 3, "sa": 0x00, "sae_ms": 20, "stress_ms": 5, "desc": "Electronic Engine Controller 1"},
    61442: {"name": "EEC2", "prio": 3, "sa": 0x00, "sae_ms": 50, "stress_ms": 10, "desc": "Electronic Engine Controller 2"},
    61443: {"name": "EEC3", "prio": 6, "sa": 0x00, "sae_ms": 250, "stress_ms": 25, "desc": "Electronic Engine Controller 3"},
    61445: {"name": "ETC2", "prio": 3, "sa": 0x03, "sae_ms": 50, "stress_ms": 10, "desc": "Electronic Transmission Controller 2"},
    61440: {"name": "ERC1", "prio": 3, "sa": 0x00, "sae_ms": 50, "stress_ms": 10, "desc": "Electronic Retarder Controller 1"},
    61441: {"name": "EBC1", "prio": 6, "sa": 0x0B, "sae_ms": 100, "stress_ms": 20, "desc": "Electronic Brake Controller 1"},
    61449: {"name": "VDS",  "prio": 3, "sa": 0x00, "sae_ms": 50, "stress_ms": 10, "desc": "Vehicle Dynamic Stability"},
    61475: {"name": "AT1_SCR_DOSING", "prio": 6, "sa": 0x00, "sae_ms": 100, "stress_ms": 20, "desc": "Aftertreatment 1 SCR Dosing Control"},
    65265: {"name": "CCVS1", "prio": 6, "sa": 0x00, "sae_ms": 100, "stress_ms": 20, "desc": "Cruise Control/Vehicle Speed 1"},
    65266: {"name": "LFE",   "prio": 6, "sa": 0x00, "sae_ms": 100, "stress_ms": 20, "desc": "Fuel Economy Liquid"},
    65190: {"name": "TURBO1", "prio": 6, "sa": 0x00, "sae_ms": 500, "stress_ms": 50, "desc": "Turbocharger 1 Status"},
    65263: {"name": "ENGINE_FLUIDS1", "prio": 6, "sa": 0x00, "sae_ms": 500, "stress_ms": 50, "desc": "Engine Fluids 1 (Oil / Fuel / Coolant Level)"},
    65270: {"name": "ENGINE_FLUIDS2", "prio": 6, "sa": 0x00, "sae_ms": 500, "stress_ms": 50, "desc": "Engine Fluids 2 (Intake / Air / Boost)"},
    65262: {"name": "ENGINE_TEMP1", "prio": 6, "sa": 0x00, "sae_ms": 1000, "stress_ms": 100, "desc": "Engine Temperature 1 (Coolant / Oil Temp)"},
    65269: {"name": "AMBIENT_COND", "prio": 6, "sa": 0x00, "sae_ms": 1000, "stress_ms": 100, "desc": "Ambient Conditions (Barometric / Ambient Temp)"},
    65303: {"name": "AFTERTREATMENT_GAS1", "prio": 6, "sa": 0x00, "sae_ms": 1000, "stress_ms": 100, "desc": "Aftertreatment Gas 1"},
    65253: {"name": "VEHICLE_HOURS", "prio": 6, "sa": 0x00, "sae_ms": 1000, "stress_ms": 100, "desc": "Vehicle Hours / Total Distance"},
    65271: {"name": "BATTERY_STATUS", "prio": 6, "sa": 0x00, "sae_ms": 1000, "stress_ms": 100, "desc": "Electrical Potential (Voltage / Current)"},
    65276: {"name": "DASH_DISPLAY", "prio": 6, "sa": 0x00, "sae_ms": 1000, "stress_ms": 100, "desc": "Dash Display 1 (Fuel / Washer Level)"},
    65254: {"name": "TIME_DATE", "prio": 6, "sa": 0x00, "sae_ms": 1000, "stress_ms": 100, "desc": "Time / Date"},
    64947: {"name": "DPF_TEMP1", "prio": 6, "sa": 0x00, "sae_ms": 1000, "stress_ms": 100, "desc": "Aftertreatment 1 DPF Temperatures"},
    65110: {"name": "AT1_SCR_TANK", "prio": 6, "sa": 0x00, "sae_ms": 1000, "stress_ms": 100, "desc": "Aftertreatment 1 SCR Tank Info"},
    65213: {"name": "FAN_DRIVE", "prio": 6, "sa": 0x00, "sae_ms": 1000, "stress_ms": 100, "desc": "Fan Drive Status & Speed"},
    65272: {"name": "TRANS_FLUIDS1", "prio": 6, "sa": 0x03, "sae_ms": 1000, "stress_ms": 100, "desc": "Transmission Fluids 1 (Oil Pressure / Temp)"},
    65274: {"name": "BRAKE_AIR_PRESS", "prio": 6, "sa": 0x0B, "sae_ms": 1000, "stress_ms": 100, "desc": "Service Brake Air Pressure"},
    65279: {"name": "WATER_IN_FUEL", "prio": 6, "sa": 0x00, "sae_ms": 1000, "stress_ms": 100, "desc": "Water In Fuel Indicator"}
}

PATTERN_NAMES = {
    0: "Constant",
    1: "Ramp (Sawtooth)",
    2: "Sine Wave",
    3: "Triangle Wave",
    4: "Step Function",
    5: "Square Wave",
    6: "Pure Random Signal",
    7: "State Sequence"
}

def pgn_to_dbc_id(pgn: int, priority: int = 6, source_address: int = 0) -> int:
    """Calculate 29-bit CAN ID with bit 31 set for standard Vector DBC format."""
    dp = (pgn >> 16) & 0x01
    pf = (pgn >> 8) & 0xFF
    ps = pgn & 0xFF
    
    can_id = (priority & 0x07) << 26
    can_id |= (dp & 0x01) << 24
    can_id |= (pf & 0xFF) << 16
    if pf < 240:
        can_id |= (0xFF << 8)  # Global broadcast destination
    else:
        can_id |= (ps << 8)
    can_id |= (source_address & 0xFF)
    
    return can_id | 0x80000000

def sanitize_identifier(text: str) -> str:
    """Sanitize text to be a valid DBC C-identifier."""
    clean = re.sub(r'[^a-zA-Z0-9_]', '_', text.strip())
    clean = re.sub(r'_+', '_', clean).strip('_')
    if clean and clean[0].isdigit():
        clean = "SIG_" + clean
    return clean or "Signal"

def generate_j1939_dbc(
    database_signals: List[Dict[str, Any]],
    active_configs: Optional[List[Dict[str, Any]]] = None,
    mode: int = 0,
    baud_kbps: int = 500,
    generator_title: str = "J1939 Intelligent Waveform & Signal Verification Hub"
) -> str:
    """
    Generate complete, syntactically valid Vector DBC file content.
    
    :param database_signals: Full SPN database list
    :param active_configs: Optional list of active pattern configurations (from UI)
    :param mode: 0 for SAE Standards, 1 for Stress Testing
    :param baud_kbps: 250 or 500
    :param generator_title: Header attribution title
    :return: Formatted DBC string
    """
    # Build SPN lookup
    spn_lookup = {s["spn"]: s for s in database_signals}
    
    # Map active configs if provided
    active_map = {}
    if active_configs:
        for cfg in active_configs:
            active_map[cfg["spn"]] = cfg
        # Filter to only active signals if specified
        target_signals = [spn_lookup[spn] for spn in active_map.keys() if spn in spn_lookup]
    else:
        target_signals = database_signals

    # Group signals by PGN
    pgn_groups: Dict[int, List[Dict[str, Any]]] = {}
    for sig in target_signals:
        pgn = sig["pgn"]
        if pgn not in pgn_groups:
            pgn_groups[pgn] = []
        pgn_groups[pgn].append(sig)

    lines = []
    lines.append('VERSION ""')
    lines.append('')
    lines.append('NS_ :')
    ns_symbols = [
        "NS_DESC_", "CM_", "BA_DEF_", "BA_", "VAL_", "CAT_DEF_", "CAT_", "FILTER",
        "BA_DEF_DEF_", "EV_DATA_", "ENVVAR_DATA_", "SGTYPE_", "SGTYPE_VAL_", "BA_DEF_SGTYPE_",
        "BA_SGTYPE_", "SIG_TYPE_REF_", "VAL_TABLE_", "SIG_GROUP_", "SIG_VALTYPE_",
        "SIGTYPE_VALTYPE_", "BO_TX_BU_", "BA_DEF_REL_", "BA_REL_", "BA_DEF_DEF_REL_",
        "BU_SG_REL_", "BU_EV_REL_", "BU_BO_REL_", "SG_MUL_VAL_"
    ]
    for sym in ns_symbols:
        lines.append(f"\t{sym}")
    lines.append('')
    lines.append('BS_:')
    lines.append('')
    lines.append('BU_: Controller Vector__XXX')
    lines.append('')

    # Track message metadata for attributes later
    msg_metadata_list = []
    sig_metadata_list = []

    # Generate BO_ and SG_ sections
    for pgn, sig_list in sorted(pgn_groups.items()):
        meta = PGN_METADATA.get(pgn, {
            "name": sig_list[0].get("pgn_name", f"PGN_{pgn}").replace("PGN_", ""),
            "prio": 6,
            "sa": 0x00,
            "sae_ms": 1000,
            "stress_ms": 100,
            "desc": f"J1939 PGN {pgn}"
        })

        prio = meta.get("prio", 6)
        sa = meta.get("sa", 0x00)
        
        # Check if any active signal in this PGN has a custom timeframe specified
        custom_timeframes = [
            active_map[s["spn"]].get("timeframe_ms")
            for s in sig_list
            if s["spn"] in active_map and active_map[s["spn"]].get("timeframe_ms")
        ]
        if custom_timeframes and min(custom_timeframes) > 0:
            cycle_time = min(custom_timeframes)
        else:
            cycle_time = meta["stress_ms"] if mode == 1 else meta["sae_ms"]

        msg_name = meta["name"]
        dbc_id = pgn_to_dbc_id(pgn, prio, sa)

        msg_metadata_list.append({
            "dbc_id": dbc_id,
            "pgn": pgn,
            "name": msg_name,
            "cycle_time": cycle_time,
            "desc": meta.get("desc", f"PGN {pgn}")
        })

        lines.append(f"BO_ {dbc_id} {msg_name}: 8 Controller")

        # Sort signals by start_bit ascending for clean DBC layout
        sorted_sigs = sorted(sig_list, key=lambda s: (s["start_byte"] - 1) * 8 + (s["start_bit"] - 1))
        
        seen_names = set()
        for sig in sorted_sigs:
            spn = sig["spn"]
            raw_sig_name = sig.get("spn_name", "").replace("SPN_", "")
            if not raw_sig_name:
                raw_sig_name = sanitize_identifier(sig["name"])
            else:
                raw_sig_name = sanitize_identifier(raw_sig_name)

            # Ensure uniqueness within message
            sig_ident = raw_sig_name
            suffix = 2
            while sig_ident in seen_names:
                sig_ident = f"{raw_sig_name}_{suffix}"
                suffix += 1
            seen_names.add(sig_ident)

            # Compute Intel start bit (0-indexed LSB)
            start_bit = (sig["start_byte"] - 1) * 8 + (sig["start_bit"] - 1)
            num_bits = sig["num_bits"]
            resolution = sig["resolution"]
            offset = sig["offset"]
            unit = sig.get("unit", "").strip()

            # Physical ranges (check active override)
            if spn in active_map:
                cfg = active_map[spn]
                min_phys = cfg.get("min_value", sig["min_physical"])
                max_phys = cfg.get("max_value", sig["max_physical"])
                pat_type = cfg.get("pattern_type", 0)
                pat_param = cfg.get("param1", 10.0)
            else:
                min_phys = sig["min_physical"]
                max_phys = sig["max_physical"]
                pat_type = 0
                pat_param = 0.0

            # Sign representation: standard unsigned @1+
            sign_char = "+"

            # Formatting floats cleanly
            res_str = f"{resolution:g}"
            off_str = f"{offset:g}"
            min_str = f"{min_phys:g}"
            max_str = f"{max_phys:g}"

            lines.append(f' SG_ {sig_ident} : {start_bit}|{num_bits}@1{sign_char} ({res_str},{off_str}) [{min_str}|{max_str}] "{unit}" Vector__XXX')

            sig_metadata_list.append({
                "dbc_id": dbc_id,
                "sig_ident": sig_ident,
                "spn": spn,
                "name": sig["name"],
                "unit": unit,
                "pattern_type": pat_type,
                "pattern_param": pat_param,
                "min_val": min_phys,
                "max_val": max_phys,
                "timeframe_ms": active_map[spn].get("timeframe_ms", 20) if spn in active_map else None,
                "t_start": active_map[spn].get("t_start", 0.0) if spn in active_map else 0.0,
                "t_dur": active_map[spn].get("t_dur", 0.0) if spn in active_map else 0.0,
            })

        lines.append('')

    # Comments CM_ section
    mode_str = "STRESS TESTING MODE" if mode == 1 else "SAE STANDARDS COMPLIANT MODE"
    lines.append(f'CM_ "J1939 CAN Database generated by {generator_title}";')
    lines.append(f'CM_ "Creators: Vishal Meyyappan R (3rd Year ECE - ACT) & Srikar (4th year ECE), CIT Chennai & STUST";')
    lines.append(f'CM_ "Configured CAN Bitrate: {baud_kbps} kbps | Timing Profile: {mode_str}";')

    for msg in msg_metadata_list:
        lines.append(f'CM_ BO_ {msg["dbc_id"]} "{msg["desc"]} (PGN {msg["pgn"]} / 0x{msg["pgn"]:04X}) - Transmit Period: {msg["cycle_time"]} ms";')

    for sig in sig_metadata_list:
        pat_name = PATTERN_NAMES.get(sig["pattern_type"], "Waveform")
        tf_str = f" | Timeframe: {sig['timeframe_ms']}ms" if sig["timeframe_ms"] else ""
        if sig.get("t_dur", 0) > 0:
            tf_str += f" [Window: {sig['t_start']}s - {sig['t_start'] + sig['t_dur']}s]"
        lines.append(f'CM_ SG_ {sig["dbc_id"]} {sig["sig_ident"]} "{sig["name"]} (SPN {sig["spn"]}) | Configured: {pat_name} [{sig["min_val"]}..{sig["max_val"]} {sig["unit"]}]{tf_str}";')

    lines.append('')

    # Attribute Definitions BA_DEF_
    lines.append('BA_DEF_ "BusType" STRING ;')
    lines.append('BA_DEF_ "ProtocolType" STRING ;')
    lines.append('BA_DEF_ BO_ "VFrameFormat" ENUM "StandardCAN","ExtendedCAN","reserved","J1939PG";')
    lines.append('BA_DEF_ BO_ "GenMsgCycleTime" INT 0 3600000;')
    lines.append('BA_DEF_ BO_ "GenMsgSendType" ENUM "cyclic","spontaneous","cyclicAndSpontaneous";')
    lines.append('BA_DEF_ BO_ "PGN" INT 0 262143;')
    lines.append('BA_DEF_ SG_ "SPN" INT 0 524287;')
    lines.append('BA_DEF_ SG_ "WaveformPattern" STRING ;')
    lines.append('BA_DEF_ SG_ "WaveformParam" FLOAT 0 1000000;')
    lines.append('')

    # Attribute Defaults BA_DEF_DEF_
    lines.append('BA_DEF_DEF_ "BusType" "CAN";')
    lines.append('BA_DEF_DEF_ "ProtocolType" "J1939";')
    lines.append('BA_DEF_DEF_ "VFrameFormat" "J1939PG";')
    lines.append('BA_DEF_DEF_ "GenMsgCycleTime" 100;')
    lines.append('BA_DEF_DEF_ "GenMsgSendType" "cyclic";')
    lines.append('BA_DEF_DEF_ "PGN" 0;')
    lines.append('BA_DEF_DEF_ "SPN" 0;')
    lines.append('BA_DEF_DEF_ "WaveformPattern" "Constant";')
    lines.append('BA_DEF_DEF_ "WaveformParam" 10.0;')
    lines.append('')

    # Attribute Values BA_
    lines.append('BA_ "BusType" "CAN";')
    lines.append('BA_ "ProtocolType" "J1939";')
    lines.append('')

    for msg in msg_metadata_list:
        lines.append(f'BA_ "VFrameFormat" BO_ {msg["dbc_id"]} 3;')
        lines.append(f'BA_ "GenMsgCycleTime" BO_ {msg["dbc_id"]} {msg["cycle_time"]};')
        lines.append(f'BA_ "GenMsgSendType" BO_ {msg["dbc_id"]} 0;')
        lines.append(f'BA_ "PGN" BO_ {msg["dbc_id"]} {msg["pgn"]};')

    lines.append('')
    for sig in sig_metadata_list:
        lines.append(f'BA_ "SPN" SG_ {sig["dbc_id"]} {sig["sig_ident"]} {sig["spn"]};')
        pat_name = PATTERN_NAMES.get(sig["pattern_type"], "Constant")
        lines.append(f'BA_ "WaveformPattern" SG_ {sig["dbc_id"]} {sig["sig_ident"]} "{pat_name}";')
        lines.append(f'BA_ "WaveformParam" SG_ {sig["dbc_id"]} {sig["sig_ident"]} {sig["pattern_param"]:.2f};')

    lines.append('')
    return "\n".join(lines)
