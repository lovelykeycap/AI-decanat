import asyncio
import threading
from contextlib import suppress
from types import SimpleNamespace

import pytest
from decanat.audio_device import AudioDeviceAgent
from decanat.speech import EndpointDetector


class VAD:
    def __init__(self):
        self.frames = []

    def reset(self):
        pass

    def probability(self, frame):
        self.frames.append(frame.copy())
        return 0.0


def agent_for(controller):
    # No microphone or inference model is opened by logic tests.
    agent = object.__new__(AudioDeviceAgent)
    agent.controller = controller
    agent._frames = asyncio.Queue(maxsize=200)
    agent._closing = agent._failed = False
    agent._lock = threading.Lock()
    agent._pcm = b"queued audio"
    agent._turn_id = "old-turn"
    agent.ready = True
    agent.detector = EndpointDetector()
    agent.vad = VAD()
    return agent


async def test_audio_captured_for_previous_student_is_discarded(controller):
    controller.start()
    controller.avatar_ready()
    agent = agent_for(controller)
    agent._enqueue("previous-student", bytes(1024))
    agent._enqueue(controller.session_id, bytes(1024))
    task = asyncio.create_task(agent._recognition())
    await asyncio.sleep(0)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    assert len(agent.vad.frames) == 1
    await controller.finish()


async def test_overflow_discards_broken_utterance_instead_of_splicing_audio(controller):
    agent = agent_for(controller)
    for _ in range(200):
        agent._enqueue("session", b"old")
    agent._enqueue("session", b"new")
    assert agent._frames.qsize() == 2
    assert await agent._frames.get() is None
    assert await agent._frames.get() == ("session", b"new")


async def test_audio_failure_mutes_pending_playback_and_ends_session(controller):
    controller.start()
    controller.avatar_ready()
    agent = agent_for(controller)
    agent._fail()
    agent._fail()
    await asyncio.sleep(0)
    assert not agent.ready
    assert agent._pcm == b""
    assert controller.state.value == "sitting_down"


def test_completed_text_is_reported_after_dac_time_not_when_pcm_is_queued(controller):
    class Loop:
        def __init__(self):
            self.soon, self.later = [], []

        def call_soon_threadsafe(self, callback, *args):
            self.soon.append((callback, args))

        def call_later(self, delay, callback, *args):
            self.later.append((delay, callback, args))

    agent = agent_for(controller)
    agent._pcm = bytes(1920)
    agent._cursor = agent._mark_cursor = 0
    agent._marks = [(480, 9), (960, 20)]
    agent._turn_id = "turn"
    agent.loop = Loop()
    agent.apm = SimpleNamespace(process=lambda *args: bytes(320))
    timing = SimpleNamespace(outputBufferDacTime=10.05, currentTime=10.0, inputBufferAdcTime=9.95)
    agent._callback(bytes(960), bytearray(960), 480, timing, None)
    schedule = [
        (callback, args)
        for callback, args in agent.loop.soon
        if callback == agent._schedule_progress
    ]
    assert len(schedule) == 1
    callback, args = schedule[0]
    callback(*args)
    delay, callback, args = agent.loop.later[0]
    assert delay == pytest.approx(0.06)
    assert callback == controller.playback_progress
    assert args == ("turn", 9)
