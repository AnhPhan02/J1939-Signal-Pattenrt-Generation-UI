"""
J1939 Intelligent Waveform & Signal Verification Web Server
Creators: Vishal Meyyappan R (3rd Year ECE - ACT) & Srikar (4th year ECE)
Institution: Chennai Institute of Technology (CIT Chennai) & STUST
"""

import os
import sys
import json
import time
import queue
import socket
import asyncio
import threading
from pathlib import Path
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import serial
import serial.tools.list_ports

from .dbc_generator import generate_j1939_dbc
from .correlator import Correlator, RxRecord, SignalDb
from .paths import WEBUI_DIR as STATIC_DIR, SPN_DB_PATH as DB_PATH, RUNS_DIR

# TSMaster Mini Program bridge sends received frames here as JSON lines
RX_BRIDGE_HOST = "127.0.0.1"
RX_BRIDGE_PORT = 29501

app = FastAPI(title="J1939 Intelligent Verification Hub", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =============================================================================
# HARDWARE SERIAL MANAGER
# =============================================================================
class SerialManager:
    def __init__(self):
        self.ser: Optional[serial.Serial] = None
        self.port_name: Optional[str] = None
        self.baudrate: int = 115200
        self.is_connected: bool = False
        self.reader_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.subscribers: List[asyncio.Queue] = []
        self.history: List[str] = []
        self.max_history = 500
        self.lock = threading.RLock()

    def list_ports(self) -> List[Dict[str, str]]:
        ports = []
        for p in serial.tools.list_ports.comports():
            ports.append({
                "device": p.device,
                "description": p.description or p.device,
                "hwid": p.hwid or ""
            })
        return ports

    def connect(self, port: str, baudrate: int = 115200) -> bool:
        self.disconnect()
        time.sleep(0.12)  # Allow Windows USB driver to release COM handle cleanly
        
        last_err = None
        for attempt in range(3):
            try:
                ser = serial.Serial(port, baudrate=baudrate, timeout=0.1, write_timeout=0.5)
                try:
                    ser.dtr = True
                    ser.rts = True
                except Exception:
                    pass
                self.ser = ser
                self.port_name = port
                self.baudrate = baudrate
                self.is_connected = True
                self.stop_event.clear()
                self.reader_thread = threading.Thread(target=self._read_loop, daemon=True)
                self.reader_thread.start()
                self._broadcast(f"[SYSTEM] Connected to {port} @ {baudrate} baud.")
                
                # Asynchronously ping status so connect() returns immediately
                def _init_ping():
                    time.sleep(0.2)
                    self.write_line("STATUS")
                threading.Thread(target=_init_ping, daemon=True).start()
                return True
            except Exception as e:
                last_err = e
                time.sleep(0.2)

        self.is_connected = False
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

        err_msg = str(last_err)
        if "Access is denied" in err_msg or "PermissionError" in err_msg:
            err_msg = f"Access denied to {port}. Please ensure Arduino IDE Serial Monitor or other serial tools are closed."
        self._broadcast(f"[SYSTEM ERR] Failed to connect to {port}: {err_msg}")
        raise RuntimeError(err_msg)

    def disconnect(self):
        self.is_connected = False
        self.stop_event.set()
        if self.reader_thread and self.reader_thread.is_alive():
            self.reader_thread.join(timeout=0.5)
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None
        self.port_name = None
        self._broadcast("[SYSTEM] Disconnected from serial port.")

    def write_line(self, text: str) -> bool:
        if not self.is_connected or not self.ser:
            return False
        with self.lock:
            try:
                clean_text = text.strip()
                payload = (clean_text + "\r\n").encode("utf-8")
                self.ser.write(payload)
                self.ser.flush()
                self._broadcast(f"[CMD OUT] >> {clean_text}")
                return True
            except Exception as e:
                self._broadcast(f"[SYSTEM ERR] Write failed: {e}")
                return False

    def _read_loop(self):
        while not self.stop_event.is_set() and self.ser and self.ser.is_open:
            try:
                line = self.ser.readline()
                if line:
                    decoded = line.decode("utf-8", errors="replace").strip()
                    if decoded:
                        run_recorder.write_tx(decoded)
                        # $TX lines feed the correlator only: at hundreds of
                        # frames/s they would flood the browser console
                        if correlator.feed_line(decoded) and decoded.startswith("$TX"):
                            continue
                        self._broadcast(decoded)
            except Exception:
                break
        self.is_connected = False

    def _broadcast(self, msg: str):
        timestamp = time.strftime("%H:%M:%S")
        entry = f"[{timestamp}] {msg}"
        with self.lock:
            self.history.append(entry)
            if len(self.history) > self.max_history:
                self.history.pop(0)

        dead_subs = []
        for q in self.subscribers:
            try:
                # Called from the serial thread: hand over to the asyncio loop
                if MAIN_LOOP is not None:
                    MAIN_LOOP.call_soon_threadsafe(q.put_nowait, entry)
                else:
                    q.put_nowait(entry)
            except Exception:
                dead_subs.append(q)
        for dead in dead_subs:
            if dead in self.subscribers:
                self.subscribers.remove(dead)

# =============================================================================
# TX/RX CORRELATION (validation)
# =============================================================================
MAIN_LOOP: Optional[asyncio.AbstractEventLoop] = None
correlator = Correlator(SignalDb(DB_PATH) if DB_PATH.exists() else None)


class RunRecorder:
    """Keeps the raw evidence of one run (UART log, RX frames, config) on disk
    so the same verdicts can be reproduced offline with tools/correlate_logs.py."""

    def __init__(self):
        self.lock = threading.Lock()
        self.dir: Optional[Path] = None
        self.tx_f = None
        self.rx_f = None

    def start(self, configs: List[dict], start_req: dict):
        self.stop()
        with self.lock:
            self.dir = RUNS_DIR / time.strftime("run_%Y%m%d_%H%M%S")
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / "config.json").write_text(
                json.dumps({"signals": configs, "start": start_req}, indent=2), encoding="utf-8")
            self.tx_f = open(self.dir / "stm32_uart.log", "w", encoding="utf-8")
            self.rx_f = open(self.dir / "tsmaster_rx.jsonl", "w", encoding="utf-8")

    def stop(self):
        with self.lock:
            for f in (self.tx_f, self.rx_f):
                if f:
                    f.close()
            self.tx_f = self.rx_f = None

    def write_tx(self, line: str):
        with self.lock:
            if self.tx_f:
                self.tx_f.write(line + "\n")

    def write_rx(self, raw_json: str):
        with self.lock:
            if self.rx_f:
                self.rx_f.write(raw_json + "\n")


run_recorder = RunRecorder()


def _rx_bridge_server():
    """Accept JSON lines {"id","data","timestamp"|"timestamp_us"} from TSMaster."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind((RX_BRIDGE_HOST, RX_BRIDGE_PORT))
    except OSError as e:
        print(f"[RX BRIDGE] cannot bind {RX_BRIDGE_HOST}:{RX_BRIDGE_PORT}: {e}")
        return
    srv.listen(4)

    def _client(conn: socket.socket):
        buf = b""
        with conn:
            while True:
                try:
                    chunk = conn.recv(65536)
                except OSError:
                    return
                if not chunk:
                    return
                buf += chunk
                *lines, buf = buf.split(b"\n")
                for raw in lines:
                    try:
                        text = raw.decode("utf-8").strip()
                        if not text:
                            continue
                        p = json.loads(text)
                        ts_ms = (float(p["timestamp_us"]) / 1000.0 if "timestamp_us" in p
                                 else float(p["timestamp"]) * 1000.0)
                        data = bytes.fromhex(p["data"]).ljust(8, b"\xff")
                        correlator.add_rx(RxRecord(ts_ms, int(str(p["id"]), 16), data))
                        run_recorder.write_rx(text)
                    except Exception:
                        continue

    while True:
        conn, _ = srv.accept()
        threading.Thread(target=_client, args=(conn,), daemon=True).start()


def _correlator_pump():
    while True:
        time.sleep(0.2)
        try:
            correlator.pump()
        except Exception as e:
            print(f"[CORRELATOR] {e}")


serial_mgr = SerialManager()

# Load SPN database
if DB_PATH.exists():
    with open(DB_PATH, "r", encoding="utf-8") as f:
        SPN_DATABASE = json.load(f)
else:
    SPN_DATABASE = []

# =============================================================================
# PYDANTIC SCHEMAS
# =============================================================================
class ConnectRequest(BaseModel):
    port: str
    baudrate: int = 115200

class ConfigItem(BaseModel):
    spn: int
    pattern_type: int
    min_value: float
    max_value: float
    param1: float
    timeframe_ms: Optional[int] = 20
    t_start: Optional[float] = 0.0
    t_dur: Optional[float] = 0.0

class ConfigureRequest(BaseModel):
    signals: List[ConfigItem]

class StartRequest(BaseModel):
    duration_sec: int = 0
    mode: int = 0  # 0 = Smooth, 1 = SAE J1939-71 periods, 2 = Stress (firmware J1939_Sched_Mode_t)
    tx_log: bool = True  # firmware $TX per-frame log for TX/RX correlation

class BaudRequest(BaseModel):
    baud_kbps: int = 500

class RawCommandRequest(BaseModel):
    command: str

class DbcExportRequest(BaseModel):
    signals: Optional[List[ConfigItem]] = None
    mode: int = 0
    baud_kbps: int = 500
    scope: str = "active"  # "active" or "full"
    title: Optional[str] = "J1939 Intelligent Waveform & Signal Verification Platform"

# =============================================================================
# API ROUTES
# =============================================================================

@app.get("/api/ports")
def get_ports():
    return {"ports": serial_mgr.list_ports()}

@app.post("/api/connect")
def connect_port(req: ConnectRequest):
    try:
        ok = serial_mgr.connect(req.port, req.baudrate)
        return {"success": ok, "port": req.port, "baudrate": req.baudrate}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/disconnect")
def disconnect_port():
    serial_mgr.disconnect()
    return {"success": True}

@app.get("/api/status")
def get_status():
    return {
        "connected": serial_mgr.is_connected,
        "port": serial_mgr.port_name,
        "baudrate": serial_mgr.baudrate,
        "total_spns": len(SPN_DATABASE),
    }

@app.get("/api/spns")
def get_spns():
    return {"spns": SPN_DATABASE, "total": len(SPN_DATABASE)}

@app.post("/api/configure")
def configure_signals(req: ConfigureRequest):
    if not serial_mgr.is_connected:
        raise HTTPException(status_code=400, detail="Serial port not connected! Please connect to STM32 first.")
    
    correlator.set_configs([item.dict() for item in req.signals])

    # 1. Clear old patterns
    serial_mgr.write_line("CLEAR")
    time.sleep(0.05)

    # 2. Register each pattern with timeframe parameters
    for item in req.signals:
        tf_ms = item.timeframe_ms if item.timeframe_ms is not None else 20
        t_start = item.t_start if item.t_start is not None else 0.0
        t_dur = item.t_dur if item.t_dur is not None else 0.0
        cmd = f"CONFIG {item.spn} {item.pattern_type} {item.min_value:.2f} {item.max_value:.2f} {item.param1:.2f} {tf_ms} {t_start:.2f} {t_dur:.2f}"
        serial_mgr.write_line(cmd)
        time.sleep(0.02)

    return {"success": True, "count": len(req.signals)}

@app.post("/api/start")
def start_generator(req: StartRequest):
    if not serial_mgr.is_connected:
        raise HTTPException(status_code=400, detail="Serial port not connected! Please connect to STM32 first.")
    correlator.reset()
    run_recorder.start(list(correlator.configs.values()), req.dict())
    serial_mgr.write_line(f"TXLOG {1 if req.tx_log else 0}")
    time.sleep(0.02)
    cmd = f"START {req.duration_sec} {req.mode}"
    ok = serial_mgr.write_line(cmd)
    return {"success": ok, "duration_sec": req.duration_sec, "mode": req.mode,
            "run_dir": str(run_recorder.dir) if run_recorder.dir else None}

@app.post("/api/stop")
def stop_generator():
    if not serial_mgr.is_connected:
        raise HTTPException(status_code=400, detail="Serial port not connected!")
    ok = serial_mgr.write_line("STOP")
    # Keep recording briefly so the last frames and $END reach the log
    threading.Timer(1.5, run_recorder.stop).start()
    return {"success": ok}

@app.post("/api/baud")
def change_can_baud(req: BaudRequest):
    if not serial_mgr.is_connected:
        raise HTTPException(status_code=400, detail="Serial port not connected!")
    cmd = f"BAUD {req.baud_kbps}"
    ok = serial_mgr.write_line(cmd)
    return {"success": ok, "baud_kbps": req.baud_kbps}

@app.post("/api/send_raw")
def send_raw_command(req: RawCommandRequest):
    if not serial_mgr.is_connected:
        raise HTTPException(status_code=400, detail="Serial port not connected!")
    ok = serial_mgr.write_line(req.command)
    return {"success": ok, "command": req.command}

@app.post("/api/dbc/generate")
def api_generate_dbc(req: DbcExportRequest):
    active_configs = [item.dict() for item in req.signals] if (req.scope == "active" and req.signals) else None
    dbc_content = generate_j1939_dbc(
        database_signals=SPN_DATABASE,
        active_configs=active_configs,
        mode=req.mode,
        baud_kbps=req.baud_kbps,
        generator_title=req.title or "J1939 Intelligent Waveform & Signal Verification Hub"
    )
    return {
        "success": True,
        "dbc_text": dbc_content,
        "filename": "j1939_pattern_active.dbc" if req.scope == "active" else "j1939_full_system.dbc",
        "scope": req.scope,
        "total_signals": len(active_configs) if active_configs else len(SPN_DATABASE)
    }

@app.post("/api/dbc/download")
def api_download_dbc(req: DbcExportRequest):
    active_configs = [item.dict() for item in req.signals] if (req.scope == "active" and req.signals) else None
    dbc_content = generate_j1939_dbc(
        database_signals=SPN_DATABASE,
        active_configs=active_configs,
        mode=req.mode,
        baud_kbps=req.baud_kbps,
        generator_title=req.title or "J1939 Intelligent Waveform & Signal Verification Hub"
    )
    filename = f"j1939_{req.scope}_{'stress' if req.mode == 1 else 'sae'}_{req.baud_kbps}kbps.dbc"
    return Response(
        content=dbc_content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

@app.get("/api/dbc/download_full")
def api_download_full_dbc(mode: int = 0, baud: int = 500):
    dbc_content = generate_j1939_dbc(
        database_signals=SPN_DATABASE,
        active_configs=None,
        mode=mode,
        baud_kbps=baud,
        generator_title="J1939 Intelligent Waveform & Signal Verification Hub"
    )
    filename = f"j1939_full_52spn_database_{baud}kbps.dbc"
    return Response(
        content=dbc_content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

# =============================================================================
# VALIDATION (TX vs RX correlation)
# =============================================================================
@app.get("/api/validation/summary")
def validation_summary():
    s = correlator.summary()
    s["run_dir"] = str(run_recorder.dir) if run_recorder.dir else None
    s["rx_bridge"] = f"{RX_BRIDGE_HOST}:{RX_BRIDGE_PORT}"
    return s

@app.get("/api/validation/events")
def validation_events(limit: int = 100, errors_only: bool = False):
    return {"events": correlator.recent_events(min(limit, 2000), errors_only)}

@app.post("/api/validation/reset")
def validation_reset():
    correlator.reset()
    return {"success": True}

@app.get("/api/validation/export")
def validation_export():
    out = (run_recorder.dir or RUNS_DIR) / "correlation_report.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    correlator.write_csv(out)
    return Response(content=out.read_bytes(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="correlation_report.csv"'})

@app.get("/api/serial_stream")
async def serial_stream(request: Request):
    q: asyncio.Queue = asyncio.Queue()
    # Pre-populate with recent history
    with serial_mgr.lock:
        for line in serial_mgr.history[-50:]:
            q.put_nowait(line)

    serial_mgr.subscribers.append(q)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    line = await asyncio.wait_for(q.get(), timeout=1.0)
                    yield f"data: {line}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            if q in serial_mgr.subscribers:
                serial_mgr.subscribers.remove(q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.on_event("startup")
async def _on_startup():
    global MAIN_LOOP
    MAIN_LOOP = asyncio.get_running_loop()
    threading.Thread(target=_rx_bridge_server, daemon=True).start()
    threading.Thread(target=_correlator_pump, daemon=True).start()

# Static files mounting
if not STATIC_DIR.exists():
    STATIC_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/")
def serve_index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>J1939 Verification Web Hub Initializing...</h1>")

if __name__ == "__main__":
    import uvicorn
    print("Starting J1939 Verification Web Hub on http://127.0.0.1:8000 ...")
    uvicorn.run(app, host="127.0.0.1", port=8000, reload=False)
