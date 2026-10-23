"""Measure real native AEC against a synthetic delayed/reverberated speaker reference.

No microphone is opened. This is an algorithm smoke check, not acoustic acceptance.
"""

from __future__ import annotations

import json
import math
import sys
import wave
from pathlib import Path

import numpy as np
from decanat.audio_device import EchoCanceller
from decanat.config import SettingsStore
from decanat.speech import SileroVAD
from scipy.signal import resample_poly

ROOT = Path(__file__).resolve().parents[1]


def main():
    settings = SettingsStore(ROOT / ".runtime")
    with wave.open(str(ROOT / ".runtime/speech-preview.wav"), "rb") as source:
        if (
            source.getframerate() != 48000
            or source.getnchannels() != 1
            or source.getsampwidth() != 2
        ):
            raise ValueError("Run tools/smoke_speech.py to create a 48 kHz mono PCM16 preview")
        voice = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype(
            np.float32
        )
    rendered = np.tile(voice, 3)
    captured = np.zeros_like(rendered)
    for delay_ms, gain in [(80, 0.6), (85, 0.2), (92, 0.1)]:
        delay = delay_ms * 48
        captured[delay:] += rendered[:-delay] * gain
    captured += np.random.default_rng(3).normal(0, 4, len(captured))
    rendered = np.clip(rendered, -32768, 32767).astype("<i2")
    captured = np.clip(captured, -32768, 32767).astype("<i2")
    name = "libdecanat_audio.dylib" if sys.platform == "darwin" else "libdecanat_audio.so"
    library = (
        Path(settings.value.apm_library_path)
        if settings.value.apm_library_path
        else ROOT / ".tools/native/lib" / name
    )
    apm = EchoCanceller(library)
    cleaned = []
    try:
        for start in range(0, len(rendered) - 480, 480):
            cleaned.append(
                apm.process(
                    captured[start : start + 480].tobytes(),
                    rendered[start : start + 480].tobytes(),
                    80,
                )
            )
    finally:
        apm.close()
    clean = np.frombuffer(b"".join(cleaned), dtype="<i2").astype(np.float32)
    reference = resample_poly(captured.astype(np.float32), 1, 3)[: len(clean)]
    # Exclude the first 4 s so the adaptive echo canceller can converge.
    start = 4 * 16000
    reduction = 10 * math.log10(
        (np.mean(reference[start:] ** 2) + 1e-6) / (np.mean(clean[start:] ** 2) + 1e-6)
    )
    vad = SileroVAD(Path(settings.value.vad_model_path))
    probabilities = [
        vad.probability(clean[i : i + 512] / 32768) for i in range(start, len(clean) - 512, 512)
    ]
    report = {
        "scope": "synthetic echo only, no near-end speech or physical microphone; not acoustic acceptance",
        "delay_ms": 80,
        "echo_path": [[80, 0.6], [85, 0.2], [92, 0.1]],
        "seconds": round(len(clean) / 16000, 3),
        "echo_reduction_after_4s_db": round(reduction, 2),
        "vad_max_after_4s": max(probabilities),
        "voiced_frames_after_4s": sum(p >= 0.6 for p in probabilities),
    }
    (ROOT / ".runtime/echo-smoke.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if reduction < 6:
        raise SystemExit("Native AEC did not meaningfully reduce this synthetic echo")


if __name__ == "__main__":
    main()
