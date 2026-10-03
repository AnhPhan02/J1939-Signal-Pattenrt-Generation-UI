"""Single place for repository paths so modules work from any working directory."""

from pathlib import Path

HOST_DIR = Path(__file__).resolve().parent.parent      # host/
REPO_DIR = HOST_DIR.parent                             # repository root

WEBUI_DIR = REPO_DIR / "webui"                         # served as /static
FIRMWARE_MID_DIR = REPO_DIR / "MID"                    # j1939_signal_definitions.[ch]
DBC_DIR = REPO_DIR / "DBC"

DATA_DIR = HOST_DIR / "data"
SPN_DB_PATH = DATA_DIR / "j1939_spn_database.json"     # generated from MID/j1939_signal_definitions.c
RUNS_DIR = HOST_DIR / "runs"                           # per-run validation evidence
