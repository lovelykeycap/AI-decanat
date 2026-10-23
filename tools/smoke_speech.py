"""Local TTS → ASR → VAD smoke check on synthetic speech, without a microphone."""

from __future__ import annotations

import asyncio
import json
import time
import wave
from pathlib import Path

import numpy as np
from decanat.config import SettingsStore
from decanat.speech import LocalSpeech, SileroVAD
from scipy.signal import resample_poly

ROOT = Path(__file__).resolve().parents[1]


async def main():
    settings = SettingsStore(ROOT / ".runtime")
    speech = LocalSpeech(settings)
    text = "Здравствуйте! Я информационный помощник деканата. Справка об обучении готовится в течение трёх рабочих дней."
    started = time.monotonic()
    audio = await speech.synthesize(text)
    cold = time.monotonic() - started
    started = time.monotonic()
    audio = await speech.synthesize(text)
    warm = time.monotonic() - started
    preview = ROOT / ".runtime/speech-preview.wav"
    with wave.open(str(preview), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(audio.sample_rate)
        output.writeframes(audio.pcm)
    started = time.monotonic()
    transcript = await speech.transcribe(audio.pcm, audio.sample_rate)
    recognition = time.monotonic() - started
    samples = resample_poly(np.frombuffer(audio.pcm, dtype="<i2").astype(np.float32) / 32768, 1, 3)
    vad = SileroVAD(Path(settings.value.vad_model_path))
    probabilities = [
        vad.probability(samples[i : i + 512]) for i in range(0, len(samples) - 512, 512)
    ]
    report = {
        "input": "synthetic TTS, no microphone",
        "synthesis_cold_seconds": round(cold, 2),
        "synthesis_warm_seconds": round(warm, 2),
        "audio_seconds": round(audio.duration, 2),
        "recognition_seconds": round(recognition, 2),
        "transcript": transcript,
        "vad_max": max(probabilities),
        "preview": str(preview),
        "completion_marks": audio.marks_payload(len(text)),
    }
    (ROOT / ".runtime/speech-smoke.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if "рабочих дней" not in transcript.lower() or max(probabilities) < 0.6:
        raise SystemExit("Synthetic speech smoke check failed")
    assert audio.marks[-1] == (len(audio.pcm) // 2, len(text))


if __name__ == "__main__":
    asyncio.run(main())
