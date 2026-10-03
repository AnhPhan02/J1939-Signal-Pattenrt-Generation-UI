import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # host/ -> import j1939hub

import cantools
from fastapi.testclient import TestClient
from j1939hub.server import app

print("=== STARTING END-TO-END VERIFICATION ===")

import j1939hub.server
import j1939hub.dbc_generator
print("1. Web server & DBC generator modules imported cleanly.")

client = TestClient(app)

res = client.get("/api/spns")
assert res.status_code == 200
data = res.json()
assert data["total"] == 52
print(f"2. SPNs API returned {data['total']} signals.")

sample_signals = [
    {"spn": 190, "pattern_type": 2, "min_value": 800, "max_value": 3500, "param1": 8.0},
    {"spn": 91, "pattern_type": 1, "min_value": 0, "max_value": 100, "param1": 10.0},
    {"spn": 84, "pattern_type": 1, "min_value": 0, "max_value": 120, "param1": 15.0},
    {"spn": 110, "pattern_type": 4, "min_value": 40, "max_value": 110, "param1": 2.0},
    {"spn": 523, "pattern_type": 3, "min_value": 0, "max_value": 8, "param1": 1.0}
]

res_gen = client.post("/api/dbc/generate", json={
    "signals": sample_signals,
    "mode": 0,
    "baud_kbps": 500,
    "scope": "active",
    "title": "J1939 Active Waveform Generator"
})
assert res_gen.status_code == 200
dbc_dict = res_gen.json()
assert dbc_dict["success"] is True

db = cantools.database.load_string(dbc_dict["dbc_text"], "dbc")
print(f"3. Active DBC generated and validated: {len(db.messages)} messages, {sum(len(m.signals) for m in db.messages)} signals.")

res_dl_active = client.post("/api/dbc/download", json={
    "signals": sample_signals,
    "mode": 1,
    "baud_kbps": 500,
    "scope": "active"
})
assert res_dl_active.status_code == 200
assert len(res_dl_active.content) > 500
print(f"4. Active DBC download endpoint returned {len(res_dl_active.content)} bytes.")

res_dl_full = client.get("/api/dbc/download_full?mode=0&baud=500")
assert res_dl_full.status_code == 200
db_full = cantools.database.load_string(res_dl_full.content.decode("utf-8"), "dbc")
assert len(db_full.messages) == 23
assert sum(len(m.signals) for m in db_full.messages) == 52
print(f"5. Full DBC download endpoint returned {len(res_dl_full.content)} bytes, 23 messages, 52 signals.")

print("=== ALL TESTS PASSED WITH 100% SUCCESS ===")
