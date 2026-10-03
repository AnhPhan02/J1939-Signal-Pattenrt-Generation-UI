"""Offline TX/RX correlation of one run.

    python host/tools/correlate_logs.py --tx host/runs/run_X/stm32_uart.log \
        --rx host/runs/run_X/tsmaster_rx.jsonl --config host/runs/run_X/config.json --csv report.csv

--rx also accepts a TSMaster/Vector .asc export.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # host/ -> import j1939hub

from j1939hub.correlator import main

if __name__ == "__main__":
    main()
