import asyncio
import threading

import pytest
from decanat.config import SettingsStore
from decanat.speech import Audio, EndpointDetector, LocalSpeech, speech_spans


def test_vad_preroll_and_silence_endpoint():
    detector = EndpointDetector(minimum_ms=64, silence_ms=64)
    frame = b"\x00\x00" * 512
    assert detector.feed(frame, 0.0) == (False, None)
    assert detector.feed(frame, 0.9) == (False, None)
    assert detector.feed(frame, 0.9) == (True, None)
    assert detector.feed(frame, 0.0) == (False, None)
    started, utterance = detector.feed(frame, 0.0)
    assert not started
    assert len(utterance) == len(frame) * 5
    assert not detector.active


def test_short_noise_is_not_an_utterance():
    detector = EndpointDetector()
    frame = b"\x00\x00" * 512
    for _ in range(30):
        assert detector.feed(frame, 0.99) == (False, None)
        assert detector.feed(frame, 0.0) == (False, None)


def test_maximum_utterance_including_preroll_fits_asr_limit():
    detector = EndpointDetector()
    frame = bytes(512 * 2)
    for _ in range(10):
        detector.feed(frame, 0.0)
    for _ in range(1500):
        _, utterance = detector.feed(frame, 0.95)
        if utterance is not None:
            assert 44 * 16000 * 2 < len(utterance) <= 45 * 16000 * 2
            assert not detector.active
            break
    else:
        raise AssertionError("Continuous speech must be segmented before the ASR cap")


def test_speech_marks_preserve_dates_abbreviations_and_original_character_offsets():
    text = "Пересдача 03.12.2026 в 14:30, ауд. Б-203. Возьмите паспорт."
    spans = speech_spans(text)
    assert len(spans) == 2
    assert spans[0][0].endswith("Б-203.")
    assert text[: spans[0][1]].strip() == spans[0][0]
    assert spans[-1][1] == len(text)
    assert len(speech_spans("1. Подайте заявление.\n2. Принесите паспорт.")) == 2


def test_speech_chunks_are_bounded_without_changing_canonical_text():
    text = " ".join(["сведение"] * 200)
    spans = speech_spans(text)
    assert " ".join(s for s, _ in spans) == text
    assert all(len(s) <= 360 for s, _ in spans)
    assert spans[-1][1] == len(text)
    assert Audio(bytes(200), 16000).marks_payload(80) == [{"sample": 100, "characters": 80}]


async def test_asr_cancellation_reaches_native_abort_callback(tmp_path, monkeypatch):
    model_module = pytest.importorskip("pywhispercpp.model")
    entered, stopped = threading.Event(), threading.Event()
    settings = SettingsStore(tmp_path)
    model_path = tmp_path / "model.bin"
    model_path.write_bytes(b"fixture, no neural inference in this test")
    settings.value.whisper_model_path = str(model_path)

    class Model:
        def __init__(self, *args, **kwargs):
            assert kwargs["context_params"] == {"use_gpu": False}

        def transcribe(self, audio, *, abort_callback, **kwargs):
            entered.set()
            while not abort_callback():
                if stopped.wait(0.01):
                    break
            stopped.set()
            return []

    monkeypatch.setattr(model_module, "Model", Model)
    task = asyncio.create_task(LocalSpeech(settings).transcribe(bytes(16000 * 2), 16000))
    assert await asyncio.to_thread(entered.wait, 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await asyncio.to_thread(stopped.wait, 1)
