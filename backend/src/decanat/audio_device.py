from __future__ import annotations

import asyncio
import ctypes
import threading
from pathlib import Path

import numpy as np

from decanat.speech import EndpointDetector, SileroVAD


def device_inventory() -> dict:
    """Enumerate device metadata without starting capture or speaker playback."""
    try:
        import sounddevice as sd

        hosts = sd.query_hostapis()
        devices = []
        for index, item in enumerate(sd.query_devices()):
            host = hosts[item["hostapi"]]["name"]
            devices.append(
                {
                    "index": index,
                    "name": item["name"],
                    "host_api": host,
                    "selection": f"{item['name']}, {host}",
                    "input_channels": item["max_input_channels"],
                    "output_channels": item["max_output_channels"],
                    "default_sample_rate": item["default_samplerate"],
                }
            )
        return {
            "available": bool(devices),
            "devices": devices,
            "message": "" if devices else "Аудиоустройства не обнаружены в среде запуска",
        }
    except Exception:
        return {
            "available": False,
            "devices": [],
            "message": "Не удалось получить список аудиоустройств",
        }


class EchoCanceller:
    def __init__(self, library: Path):
        self.dll = ctypes.CDLL(str(library))
        pointer = ctypes.POINTER(ctypes.c_int16)
        self.dll.decanat_apm_create.restype = ctypes.c_void_p
        self.dll.decanat_apm_process.argtypes = [
            ctypes.c_void_p,
            pointer,
            pointer,
            ctypes.c_int,
            pointer,
        ]
        self.dll.decanat_apm_process.restype = ctypes.c_int
        self.dll.decanat_apm_destroy.argtypes = [ctypes.c_void_p]
        self.handle = self.dll.decanat_apm_create()
        if not self.handle:
            raise RuntimeError("Не удалось создать WebRTC AEC")

    def process(self, captured: bytes, rendered: bytes, delay_ms: int) -> bytes:
        if not self.handle or len(captured) != 960 or len(rendered) != 960:
            raise ValueError("AEC ожидает открытый процессор и блоки по 480 PCM16 отсчётов")
        if not 0 <= delay_ms <= 500:
            raise ValueError("Задержка AEC должна быть в пределах 0–500 мс")
        capture = (ctypes.c_int16 * 480).from_buffer_copy(captured)
        render = (ctypes.c_int16 * 480).from_buffer_copy(rendered)
        clean = (ctypes.c_int16 * 160)()
        result = self.dll.decanat_apm_process(self.handle, capture, render, delay_ms, clean)
        if result:
            raise RuntimeError(f"WebRTC Audio Processing: {result}")
        return bytes(clean)

    def close(self):
        if self.handle:
            self.dll.decanat_apm_destroy(self.handle)
            self.handle = None


class AudioDeviceAgent:
    """The backend owns audible playback; Unreal receives the same PCM for facial animation only.

    AEC is fed the actual output blocks from this duplex stream. There is no second audio player.
    """

    def __init__(self, controller, apm_library: Path):
        self.controller = controller
        self.apm = EchoCanceller(apm_library)
        self.vad = SileroVAD(Path(controller.settings.value.vad_model_path))
        self.detector = EndpointDetector()
        self._lock = threading.Lock()
        self._pcm = b""
        self._cursor = 0
        self._turn_id: str | None = None
        self._marks: list[tuple[int, int]] = []
        self._mark_cursor = 0
        self._frames: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._stream = None
        self._tasks = []
        self._queue = None
        self.ready = False
        self._closing = False
        self._failed = False

    def _enqueue(self, session_id: str | None, frame: bytes):
        if self._closing or self._failed:
            return
        if self._frames.full():
            # Never splice separated audio into one apparently valid question.
            while not self._frames.empty():
                self._frames.get_nowait()
            self._frames.put_nowait(None)
        self._frames.put_nowait((session_id, frame))

    def _fail(self):
        if self._closing or self._failed:
            return
        self._failed = True
        self.ready = False
        with self._lock:
            self._pcm = b""
            self._turn_id = None
        self.controller.emit(
            "error", code="audio_failed", message="Аудиосистема недоступна. Обратитесь к сотруднику"
        )
        asyncio.create_task(self.controller.finish("audio_failed"))

    def _callback(self, indata, outdata, frames, timing, status):
        if frames != 480 or self._closing or self._failed:
            outdata[:] = bytes(len(outdata))
            if frames != 480:
                self.loop.call_soon_threadsafe(self._fail)
            return
        captured_session = self.controller.session_id
        finished = None
        progress = []
        with self._lock:
            first_sample = self._cursor // 2
            chunk = self._pcm[self._cursor : self._cursor + 960]
            self._cursor += len(chunk)
            rendered = chunk.ljust(960, b"\x00")
            outdata[:] = rendered
            if self._turn_id and self._pcm:
                while self._mark_cursor < len(self._marks):
                    sample, characters = self._marks[self._mark_cursor]
                    if sample > self._cursor // 2:
                        break
                    delay = max(
                        0.0,
                        timing.outputBufferDacTime
                        - timing.currentTime
                        + (sample - first_sample) / 48000,
                    )
                    progress.append((self._turn_id, characters, delay))
                    self._mark_cursor += 1
                if self._cursor >= len(self._pcm):
                    finished, self._turn_id = self._turn_id, None
        for item in progress:
            self.loop.call_soon_threadsafe(self._schedule_progress, *item)
        if finished:
            output_delay = max(0.01, timing.outputBufferDacTime - timing.currentTime + 0.01)
            self.loop.call_soon_threadsafe(self._schedule_finished, finished, output_delay)
        delay = max(
            0, min(500, round((timing.outputBufferDacTime - timing.inputBufferAdcTime) * 1000))
        )
        try:
            clean = self.apm.process(bytes(indata), rendered, delay)
        except (RuntimeError, ValueError):
            self.loop.call_soon_threadsafe(self._fail)
            return
        self.loop.call_soon_threadsafe(self._enqueue, captured_session, clean)

    def _schedule_finished(self, turn_id, delay):
        self.loop.call_later(delay, self.controller.playback_finished, turn_id)

    def _schedule_progress(self, turn_id, characters, delay):
        self.loop.call_later(delay, self.controller.playback_progress, turn_id, characters)

    async def start(self):
        import sounddevice as sd

        self.loop = asyncio.get_running_loop()
        settings = self.controller.settings.value
        devices = None
        if settings.input_device or settings.output_device:
            devices = (settings.input_device or None, settings.output_device or None)
        self._queue = self.controller.subscribe()
        self._stream = sd.RawStream(
            samplerate=48000,
            blocksize=480,
            channels=1,
            dtype="int16",
            device=devices,
            callback=self._callback,
            finished_callback=lambda: self.loop.call_soon_threadsafe(self._fail),
        )
        self._stream.start()
        self._tasks = [
            asyncio.create_task(self._events()),
            asyncio.create_task(self._recognition()),
        ]
        for task in self._tasks:
            task.add_done_callback(self._worker_done)
        self.ready = True

    def _worker_done(self, task):
        if self._closing or task.cancelled():
            return
        task.exception()
        self._fail()

    async def _events(self):
        import base64

        while True:
            event = await self._queue.get()
            if event.kind == "resync_required":
                raise RuntimeError("Audio event queue overflow")
            if event.kind in {"playback_stop", "cleared"}:
                with self._lock:
                    self._pcm = b""
                    self._cursor = 0
                    self._turn_id = None
                    self._marks = []
                    self._mark_cursor = 0
            elif event.kind == "speech":
                if event.payload["sample_rate"] != 48000:
                    self.controller.emit("audio_warning", message="Неверная частота синтеза речи")
                    continue
                with self._lock:
                    self._pcm = base64.b64decode(event.payload["audio_b64"])
                    self._cursor = 0
                    self._turn_id = event.turn_id
                    self._marks = [
                        (m["sample"], m["characters"]) for m in event.payload["speech_marks"]
                    ]
                    self._mark_cursor = 0
            if event.kind == "cleared":
                self.detector.reset()
                self.vad.reset()

    async def _recognition(self):
        pending = bytearray()
        pending_session = None
        while True:
            item = await self._frames.get()
            if item is None:
                pending.clear()
                self.detector.reset()
                self.vad.reset()
                if self.controller.active:
                    self.controller.emit(
                        "clarify", text="Аудио прервалось. Повторите вопрос, пожалуйста."
                    )
                continue
            session_id, cleaned = item
            if not self.controller.active or session_id != self.controller.session_id:
                pending.clear()
                self.detector.reset()
                continue
            if pending_session != session_id:
                pending.clear()
                self.detector.reset()
                self.vad.reset()
                pending_session = session_id
            pending.extend(cleaned)
            while len(pending) >= 1024:
                frame = bytes(pending[:1024])
                del pending[:1024]
                if not self.controller.active:
                    self.detector.reset()
                    continue
                probability = self.vad.probability(
                    np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32768
                )
                started, utterance = self.detector.feed(frame, probability)
                if started:
                    await self.controller.interrupt()
                if self.detector.active:
                    self.controller.activity()
                if utterance and self.controller.active:
                    await self.controller.audio(utterance, 16000)

    async def close(self):
        self._closing = True
        self.ready = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._queue:
            self.controller.unsubscribe(self._queue)
        self.apm.close()
