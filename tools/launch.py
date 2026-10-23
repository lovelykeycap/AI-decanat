"""Start the local backend and packaged desktop application as one supervised process group."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("desktop", type=Path, help="Path to the packaged Decanat executable")
    parser.add_argument("--data-dir", type=Path, default=Path(".runtime"))
    args = parser.parse_args()
    if not args.desktop.is_file():
        parser.error("Packaged desktop executable is not installed")
    root = args.data_dir.resolve()
    connection_file = root / "desktop-connection.json"
    connection_file.unlink(missing_ok=True)
    environment = dict(os.environ)
    environment["DECANAT_CONNECTION_FILE"] = str(connection_file)
    backend = subprocess.Popen(
        [sys.executable, "-m", "decanat.cli", "--data-dir", str(root), "serve"], env=environment
    )
    desktop = None
    try:
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if backend.poll() is not None:
                raise RuntimeError("Backend failed to start")
            if connection_file.exists():
                try:
                    data = json.loads(connection_file.read_text())
                    request = urllib.request.Request(
                        data["url"] + "/v1/health",
                        headers={"Authorization": "Bearer " + data["token"]},
                    )
                    with urllib.request.urlopen(request, timeout=1) as response:
                        if response.status == 200:
                            break
                except (OSError, ValueError, urllib.error.URLError):
                    pass
            time.sleep(0.2)
        else:
            raise RuntimeError("Backend startup timed out")
        desktop = subprocess.Popen(
            [str(args.desktop.resolve()), "-fullscreen", "-NoSplash"], env=environment
        )
        while desktop.poll() is None:
            if backend.poll() is not None:
                raise RuntimeError(
                    "Backend stopped; closing desktop to avoid a stale student session"
                )
            time.sleep(0.5)
    finally:
        for process in (desktop, backend):
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        connection_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
