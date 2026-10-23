from __future__ import annotations

import asyncio
import base64
import hashlib
import math
import re
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from decanat.config import SettingsStore


@dataclass
class Audio:
    pcm: bytes
    sample_rate: int
    # (end sample, end character in the original text), confirmed only after playback.
    marks: tuple[tuple[int, int], ...] = ()

    @property
    def duration(self) -> float:
        return len(self.pcm) / (self.sample_rate * 2)

    def base64(self) -> str:
        return base64.b64encode(self.pcm).decode("ascii")

    def marks_payload(self, text_length: int) -> list[dict]:
        marks = self.marks or ((len(self.pcm) // 2, text_length),)
        return [{"sample": sample, "characters": characters} for sample, characters in marks]


def speech_spans(text: str, maximum: int = 360) -> list[tuple[str, int]]:
    """Keep sentence/phrase boundaries mapped to the unchanged canonical answer.

    We confirm whole completed fragments, never guess words from a duration ratio.
    Short names, dates, initials and numbered list markers do not create sentences.
    """
    if not text.strip():
        raise ValueError("Текст озвучивания пуст")
    abbreviations = {
        "г",
        "гг",
        "ул",
        "д",
        "стр",
        "п",
        "пп",
        "ст",
        "ч",
        "им",
        "руб",
        "коп",
        "т",
        "е",
        "пр",
        "проф",
        "доц",
        "др",
        "каб",
        "ауд",
        "тел",
        "мин",
    }
    boundaries = []
    for match in re.finditer(r"[.!?;][»”\"')\]]*\s+|\n+", text):
        if text[match.start()] == ".":
            prefix = text[: match.start()]
            word = re.search(r"(\w+)$", prefix)
            if word and (
                word[0].lower() in abbreviations or (len(word[0]) == 1 and word[0].isupper())
            ):
                continue
            if re.fullmatch(r"\s*\d{1,3}", prefix.rsplit("\n", 1)[-1]):
                continue
        boundaries.append(match.end())
    boundaries.append(len(text))
    spans, start = [], 0
    for end in sorted(set(boundaries)):
        while end - start > maximum:
            cut = text.rfind(" ", start + 1, start + maximum + 1)
            if cut < start + 1:
                cut = start + maximum
            if fragment := text[start:cut].strip():
                spans.append((fragment, cut))
            start = cut
            while start < end and text[start].isspace():
                start += 1
        if fragment := text[start:end].strip():
            spans.append((fragment, end))
        start = end
    return spans


class LocalSpeech:
    """Local-only inference. Model files must be installed explicitly, not fetched during a turn."""

    def __init__(self, settings: SettingsStore):
        self.settings = settings
        self._asr = None
        self._tts = None
        self._asr_path = ""
        self._asr_gpu = None
        self._tts_path = ""
        self._asr_lock = threading.Lock()
        self._tts_lock = threading.Lock()

    @staticmethod
    def _file(value: str, description: str) -> Path:
        path = Path(value)
        if not value or not path.is_file():
            raise ValueError(f"Не установлен локальный файл {description}")
        return path.resolve()

    async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        cancelled = threading.Event()
        try:
            return await asyncio.to_thread(self._transcribe, pcm, sample_rate, cancelled)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def warm_up(self) -> None:
        # Pay model/backend initialisation during startup, not the student's first turn.
        await self.synthesize("Здравствуйте.")
        await self.transcribe(bytes(16000 * 2), 16000)

    def _transcribe(self, pcm: bytes, sample_rate: int, cancelled: threading.Event) -> str:
        from pywhispercpp.model import Model

        path = self._file(self.settings.value.whisper_model_path, "Whisper")
        audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        if sample_rate != 16000:
            from scipy.signal import resample_poly

            factor = math.gcd(sample_rate, 16000)
            audio = resample_poly(audio, 16000 // factor, sample_rate // factor).astype(np.float32)
        with self._asr_lock:
            if cancelled.is_set():
                return ""
            gpu = self.settings.value.whisper_use_gpu
            if self._asr is None or self._asr_path != str(path) or self._asr_gpu != gpu:
                self._asr = Model(
                    str(path),
                    context_params={"use_gpu": gpu},
                    n_threads=4,
                    language="ru",
                    print_realtime=False,
                    print_progress=False,
                )
                self._asr_path = str(path)
                self._asr_gpu = gpu
            segments = self._asr.transcribe(
                audio, language="ru", no_context=True, abort_callback=cancelled.is_set
            )
            return " ".join(segment.text.strip() for segment in segments).strip()

    async def synthesize(self, text: str) -> Audio:
        cancelled = threading.Event()
        try:
            return await asyncio.to_thread(self._synthesize, text, cancelled)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    def _synthesize(self, text: str, cancelled: threading.Event) -> Audio:
        import torch

        path = self._file(self.settings.value.silero_model_path, "Silero TTS")
        with self._tts_lock:
            if cancelled.is_set():
                return Audio(b"", 48000)
            if self._tts is None or self._tts_path != str(path):
                torch.set_num_threads(4)
                # A torch package can contain code: only operator-installed, checksummed models belong here.
                self._tts = torch.package.PackageImporter(str(path)).load_pickle(
                    "tts_models", "model"
                )
                self._tts.to(torch.device("cpu"))
                self._tts_path = str(path)
            chunks, marks, samples_total = [], [], 0
            with torch.inference_mode():
                for fragment, characters in speech_spans(text):
                    if cancelled.is_set():
                        break
                    audio = self._tts.apply_tts(
                        text=fragment.replace("№", " номер "),
                        speaker=self.settings.value.silero_speaker,
                        sample_rate=48000,
                    )
                    samples = np.asarray(audio.detach().cpu(), dtype=np.float32)
                    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
                    chunks.append(pcm)
                    samples_total += len(pcm) // 2
                    marks.append((samples_total, characters))
        return Audio(b"".join(chunks), 48000, tuple(marks))

    def diagnostics(self) -> dict:
        import importlib.util

        return {
            "whisper_library": importlib.util.find_spec("pywhispercpp") is not None,
            "torch_library": importlib.util.find_spec("torch") is not None,
            "vad_library": importlib.util.find_spec("onnxruntime") is not None,
            "audio_library": importlib.util.find_spec("sounddevice") is not None,
            "whisper_model": Path(self.settings.value.whisper_model_path).is_file(),
            "whisper_gpu_requested": self.settings.value.whisper_use_gpu,
            "tts_model": Path(self.settings.value.silero_model_path).is_file(),
            "vad_model": Path(self.settings.value.vad_model_path).is_file(),
            "apm_library": Path(self.settings.value.apm_library_path).is_file(),
        }


class SileroVAD:
    """Stateful ONNX Silero VAD v5/v6 for 16 kHz 512-sample frames."""

    def __init__(self, path: Path):
        import onnxruntime

        onnxruntime.disable_telemetry_events()
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self.model = onnxruntime.InferenceSession(
            str(path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self.reset()

    def reset(self) -> None:
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, 64), dtype=np.float32)

    def probability(self, samples: np.ndarray) -> float:
        if samples.shape != (512,):
            raise ValueError("VAD ожидает 512 отсчётов на 16 kHz")
        data = np.concatenate((self.context, samples.reshape(1, -1)), axis=1).astype(np.float32)
        out, self.state = self.model.run(
            None, {"input": data, "state": self.state, "sr": np.array(16000, dtype=np.int64)}
        )
        self.context = data[:, -64:]
        return float(out.reshape(-1)[0])


class EndpointDetector:
    """Utterance segmentation with pre-roll. AEC must run before this detector."""

    def __init__(
        self,
        *,
        threshold: float = 0.6,
        silence_ms: int = 650,
        minimum_ms: int = 160,
        maximum_seconds: int = 45,
    ):
        from collections import deque

        self.threshold = threshold
        self.silence_frames = max(1, math.ceil(silence_ms / 32))
        self.minimum_frames = max(1, math.ceil(minimum_ms / 32))
        # A ceil would produce 45.024 seconds, rejected by Conversation.audio's 45 s cap.
        self.maximum_frames = max(1, math.floor(maximum_seconds * 1000 / 32))
        self.pre_roll = deque(maxlen=10)
        self.reset()

    def reset(self) -> None:
        self.frames: list[bytes] = []
        self.silent = 0
        self.voiced = 0
        self.active = False
        self.pre_roll.clear()

    def feed(self, pcm: bytes, probability: float) -> tuple[bool, bytes | None]:
        started = False
        if probability >= self.threshold:
            self.voiced += 1
            self.silent = 0
            if not self.active and self.voiced >= self.minimum_frames:
                self.active = True
                self.frames = list(self.pre_roll)
                started = True
        else:
            if not self.active:
                self.voiced = 0
            self.silent += 1
        if self.active:
            self.frames.append(pcm)
            if self.silent >= self.silence_frames or len(self.frames) >= self.maximum_frames:
                utterance = b"".join(self.frames)
                self.reset()
                return started, utterance
        self.pre_roll.append(pcm)
        return started, None


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
