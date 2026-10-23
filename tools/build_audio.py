"""Build the checksummed WebRTC APM source and the C ABI bridge inside the project."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = "https://freedesktop.org/software/pulseaudio/webrtc-audio-processing/webrtc-audio-processing-1.3.tar.xz"
SHA256 = "2365e93e778d7b61b5d6e02d21c47d97222e9c7deff9e1d0838ad6ec2e86f1b9"


def main():
    cache = ROOT / ".cache/native"
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / "webrtc-audio-processing-1.3.tar.xz"
    if not archive.exists():
        with urllib.request.urlopen(URL, timeout=60) as response, archive.open("wb") as stream:
            while chunk := response.read(1024 * 1024):
                stream.write(chunk)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise SystemExit("Checksum mismatch: remove the untrusted download and retry")
    subprojects = ROOT / "native/audio/subprojects"
    subprojects.mkdir(parents=True, exist_ok=True)
    if not (subprojects / "webrtc-audio-processing-1.3").exists():
        with tarfile.open(archive) as source:
            source.extractall(subprojects, filter="data")
    # Meson promotes wraps to the root subprojects directory for a nested subproject.
    wrap = subprojects / "webrtc-audio-processing-1.3/subprojects/abseil-cpp.wrap"
    (subprojects / "abseil-cpp.wrap").write_bytes(wrap.read_bytes())
    environment = dict(os.environ)
    environment["PATH"] = (
        str(Path(sys.executable).parent) + os.pathsep + environment.get("PATH", "")
    )
    prefix = ROOT / ".tools/native"
    build = cache / "build"
    setup = [
        sys.executable,
        "-m",
        "mesonbuild.mesonmain",
        "setup",
        str(build),
        str(ROOT / "native/audio"),
        f"--prefix={prefix}",
        "--buildtype=release",
    ]
    if (build / "meson-private/coredata.dat").exists():
        setup.append("--reconfigure")
    subprocess.run(setup, env=environment, check=True)
    subprocess.run(
        [sys.executable, "-m", "mesonbuild.mesonmain", "compile", "-C", str(build), "-j", "4"],
        env=environment,
        check=True,
    )
    subprocess.run(
        [sys.executable, "-m", "mesonbuild.mesonmain", "install", "-C", str(build)],
        env=environment,
        check=True,
    )
    print(f"Native AEC installed under {prefix}")


if __name__ == "__main__":
    main()
