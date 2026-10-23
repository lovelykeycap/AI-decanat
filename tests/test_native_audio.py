import sys
from pathlib import Path

import numpy as np
import pytest
from decanat.audio_device import EchoCanceller
from decanat.config import RuntimeLease


def test_offline_changes_cannot_race_running_application(tmp_path):
    with RuntimeLease(tmp_path), pytest.raises(ValueError, match="уже запущено"):
        with RuntimeLease(tmp_path):
            pass
    with RuntimeLease(tmp_path):
        pass


def test_compiled_web_rtc_processes_real_native_blocks():
    root = Path(__file__).resolve().parents[1]
    name = "libdecanat_audio.dylib" if sys.platform == "darwin" else "libdecanat_audio.so"
    library = root / ".tools/native/lib" / name
    if not library.exists():
        pytest.skip("Build the native WebRTC library before its integration check")
    apm = EchoCanceller(library)
    rng = np.random.default_rng(7)
    try:
        for _ in range(100):
            render = (rng.normal(0, 700, 480)).astype("<i2").tobytes()
            capture = bytes(960)
            result = apm.process(capture, render, 80)
            assert len(result) == 320
            assert np.isfinite(np.frombuffer(result, dtype="<i2")).all()
    finally:
        apm.close()
