"""Compatibility launcher: use the project's virtual environment."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if __name__ == "__main__":
    import uvicorn
    from c_app.api import create_app

    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=18065)
    p.add_argument("--host", default="127.0.0.1")
    args = p.parse_args()
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning")
