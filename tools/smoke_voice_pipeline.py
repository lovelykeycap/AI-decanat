"""Exercise actual local TTS/Whisper/search/Codex/TTS on synthetic Russian questions.

No microphone, speaker, or Unreal avatar is used. Animation/playback acknowledgements
are diagnostic commands, not a claim of verified acoustic or visual integration.
"""

from __future__ import annotations

import asyncio
import json
import time
import wave
from pathlib import Path

from decanat.cli import make_kb
from decanat.config import RuntimeLease, SettingsStore, write_private
from decanat.journal import Journal
from decanat.providers import Providers
from decanat.session import Conversation
from decanat.speech import LocalSpeech

ROOT = Path(__file__).resolve().parents[1]


async def main():
    root = ROOT / ".runtime"
    settings = SettingsStore(root)
    settings.value.speech_enabled = True  # Diagnostic only; never persisted.
    settings.value.api_fallback = False
    kb = make_kb(root, settings)
    if not kb.metadata() or not kb.metadata()["kb_id"].startswith("fictional-"):
        raise ValueError("This smoke check requires the explicitly fictional sample corpus")
    speech = LocalSpeech(settings)
    report = {
        "scope": "synthetic questions; real inference; no microphone/speakers/Unreal; playback/animation acknowledgements simulated",
        "provider": settings.value.provider,
        "kb": kb.metadata(),
        "turns": [],
    }
    started = time.monotonic()
    await speech.warm_up()
    await asyncio.to_thread(kb.warm_up)
    report["warm_up_seconds"] = round(time.monotonic() - started, 3)
    controller = Conversation(
        settings, kb, Providers(settings), Journal(root / "smoke-voice"), speech=speech
    )
    queue = controller.subscribe()
    controller.start()
    controller.avatar_ready()
    try:
        for index, question in enumerate(
            [
                "Как получить справку об обучении и сколько её делают?",
                "А выходные дни в этот срок входят?",
            ],
            1,
        ):
            # Only prepared synthetic test material enters ASR; no live student is recorded.
            utterance = await speech.synthesize(question)
            started = time.monotonic()
            await controller.audio(utterance.pcm, utterance.sample_rate)
            row = {"synthetic_question": question, "question_audio_seconds": utterance.duration}
            async with asyncio.timeout(120):
                while True:
                    event = await queue.get()
                    if event.kind == "transcript":
                        row["transcript"] = event.payload["text"]
                    elif event.kind == "error":
                        raise RuntimeError(event.payload["message"])
                    elif event.kind == "answer":
                        row["answer"] = event.payload["answer"]
                        row["provider"] = event.payload["provider"]
                    elif event.kind == "speech":
                        import base64

                        row["asr_retrieval_provider_tts_seconds"] = round(
                            time.monotonic() - started, 3
                        )
                        row["answer_audio_seconds"] = event.payload["duration"]
                        row["completion_marks"] = event.payload["speech_marks"]
                        preview = root / f"voice-check-answer-{index}.wav"
                        with wave.open(str(preview), "wb") as output:
                            output.setnchannels(1)
                            output.setsampwidth(2)
                            output.setframerate(event.payload["sample_rate"])
                            output.writeframes(base64.b64decode(event.payload["audio_b64"]))
                        assert row["completion_marks"][-1]["characters"] == len(
                            row["answer"]["answer_text"]
                        )
                        row["synthetic_answer_preview"] = str(preview)
                        controller.playback_finished(controller.turn_id)
                        break
            report["turns"].append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            if row["answer"]["status"] != "answer" or not row["answer"]["source_ids"]:
                raise RuntimeError("Expected a sourced factual answer on the synthetic corpus")
        assert len(controller.history) == 4
    finally:
        await controller.finish("diagnostic_finished")
        controller.avatar_ready()
        write_private(
            root / "voice-pipeline-smoke.json",
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        )


if __name__ == "__main__":
    with RuntimeLease(ROOT / ".runtime"):
        asyncio.run(main())
