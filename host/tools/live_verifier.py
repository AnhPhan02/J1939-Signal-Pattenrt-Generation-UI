"""Desktop (tk) live J1939 verifier - see LIVE_VERIFIER_README.md.

    python host/tools/live_verifier.py --dbc j1939_active.dbc --simulate
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # host/ -> import j1939hub

from j1939hub.live_verifier import main

if __name__ == "__main__":
    main()
