"""Start the web UI + serial bridge + TX/RX correlator.

    python host/run_server.py            ->  http://127.0.0.1:8000
"""

import uvicorn

from j1939hub.server import app

if __name__ == "__main__":
    print("Starting J1939 Verification Web Hub on http://127.0.0.1:8000 ...")
    uvicorn.run(app, host="127.0.0.1", port=8000, reload=False)
