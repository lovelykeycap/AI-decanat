from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from contextlib import suppress

from decanat.config import SettingsStore
from decanat.contracts import (
    AnswerResult,
    AnswerStatus,
    Event,
    Evidence,
    GeneratedAnswer,
    Message,
    State,
)
from decanat.journal import Journal
from decanat.knowledge import KnowledgeStore, PackageError
from decanat.providers import ProviderError, Providers


class SessionError(ValueError):
    pass


class Conversation:
    """Single-device session controller. Every asynchronous turn is fenced by its generation."""

    def __init__(
        self,
        settings: SettingsStore,
        kb: KnowledgeStore,
        providers: Providers,
        journal: Journal,
        *,
        speech=None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.settings, self.kb, self.providers, self.journal = settings, kb, providers, journal
        self.speech, self.clock = speech, clock
        self.state = State.IDLE
        self.session_id: str | None = None
        self.turn_id: str | None = None
        self.history: list[Message] = []
        self.sources: list[Evidence] = []
        self.answer: AnswerResult | None = None
        self.last_activity = clock()
        self.warning = False
        self.maintenance = False
        self._generation = 0
        self._sequence = 0
        self._task: asyncio.Task | None = None
        self._subscribers: set[asyncio.Queue] = set()
        self._spoken_characters = 0

    @property
    def active(self) -> bool:
        return self.session_id is not None and self.state != State.SITTING_DOWN

    def subscribe(self) -> asyncio.Queue:
        queue = asyncio.Queue(maxsize=256)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def emit(self, kind: str, **payload) -> None:
        self._sequence += 1
        event = Event(
            sequence=self._sequence,
            kind=kind,
            session_id=self.session_id,
            turn_id=self.turn_id,
            payload=payload,
        )
        for queue in list(self._subscribers):
            if queue.full():
                # A slow client must resynchronise; never block cancellation on a dead renderer.
                self._subscribers.discard(queue)
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(
                    Event(
                        sequence=self._sequence,
                        kind="resync_required",
                        session_id=self.session_id,
                        turn_id=self.turn_id,
                        payload={},
                    )
                )
            else:
                queue.put_nowait(event)

    def snapshot(self) -> dict:
        return {
            "state": self.state.value,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "maintenance": self.maintenance,
            "warning": self.warning,
            "history": [m.model_dump(mode="json") for m in self.history],
            "answer": self.answer.model_dump(mode="json") if self.answer else None,
            "sources": [s.model_dump() for s in self.sources],
            "sequence": self._sequence,
        }

    def _state(self, state: State) -> None:
        self.state = state
        self.emit("state", state=state.value)

    def activity(self) -> None:
        self.last_activity = self.clock()
        if self.warning:
            self.warning = False
            self.emit("idle_warning", visible=False)

    def start(self) -> str:
        if self.state != State.IDLE or self.maintenance:
            raise SessionError("Приложение занято или находится на обслуживании")
        if not self.kb.installed:
            raise SessionError("База знаний не установлена. Обратитесь к сотруднику")
        try:
            self.kb.ensure_compatible()
        except PackageError as exc:
            raise SessionError("База знаний требует настройки сотрудником") from exc
        error = self.providers.known_error()
        if error:
            self.emit("error", code=error.code, message=error.message)
            raise SessionError(error.message)
        self._generation += 1
        self.session_id = uuid.uuid4().hex
        self.turn_id = None
        self.history.clear()
        self.sources.clear()
        self.answer = None
        self.activity()
        self._state(State.STANDING_UP)
        self.emit("avatar", action="stand")
        return self.session_id

    def avatar_ready(self) -> None:
        if self.state == State.STANDING_UP:
            self._state(State.LISTENING)
            self.activity()
        elif self.state == State.SITTING_DOWN:
            self.session_id = None
            self._state(State.IDLE)

    async def _cancel(self) -> int:
        self._generation += 1
        generation = self._generation
        task, self._task = self._task, None
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                try:
                    await asyncio.wait_for(asyncio.shield(task), 2)
                except TimeoutError:
                    task.add_done_callback(
                        lambda completed: (
                            completed.exception() if not completed.cancelled() else None
                        )
                    )
        return generation

    def _mark_interrupted(self) -> None:
        if self.history and self.history[-1].role == "assistant" and self.state == State.SPEAKING:
            message = self.history[-1]
            message.interrupted = True
            message.spoken_text = message.text[: self._spoken_characters]
            if self.turn_id:
                self.journal.playback(self.turn_id, message.spoken_text, interrupted=True)

    async def interrupt(self) -> bool:
        if not self.active:
            return False
        session_id = self.session_id
        self._mark_interrupted()
        self.emit("playback_stop")
        generation = await self._cancel()
        if not self.active or self.session_id != session_id or self._generation != generation:
            return False
        self._state(State.LISTENING)
        self.activity()
        return True

    async def finish(self, reason: str = "user") -> None:
        if self.state in {State.IDLE, State.SITTING_DOWN}:
            return
        self._mark_interrupted()
        self.emit("playback_stop")
        self.history.clear()
        self.sources.clear()
        self.answer = None
        self.turn_id = None
        self.warning = False
        self.emit("cleared", reason=reason)
        self._state(State.SITTING_DOWN)
        self.emit("avatar", action="sit")
        # Establish the terminal state before waiting on a cancelled provider. A second
        # finish is now idempotent, and cleanup cannot later clear a new student's session.
        await self._cancel()

    async def submit(self, text: str) -> str:
        if not self.active:
            raise SessionError("Сначала начните разговор")
        text = text.strip()
        if not text or len(text) > 4000:
            raise SessionError("Реплика должна содержать от 1 до 4000 символов")
        if not await self.interrupt():
            raise SessionError("Реплика отменена или разговор уже завершён")
        self.turn_id = uuid.uuid4().hex
        self._spoken_characters = 0
        self.sources = []
        self.answer = None
        prior = list(self.history)
        self.history.append(Message(role="user", text=text))
        self.emit("transcript", text=text)
        self._state(State.THINKING)
        generation, session_id, turn_id = self._generation, self.session_id, self.turn_id
        self._task = asyncio.create_task(
            self._run_turn(generation, session_id, turn_id, text, prior)
        )
        return turn_id

    def _current(self, generation: int, session_id: str, turn_id: str) -> bool:
        return (
            self._generation == generation
            and self.session_id == session_id
            and self.turn_id == turn_id
        )

    async def _run_turn(
        self, generation: int, session_id: str, turn_id: str, text: str, history: list[Message]
    ) -> None:
        metadata = self.kb.metadata()
        try:
            evidence = await asyncio.to_thread(
                self.kb.search, text, [m.text for m in history if m.role == "user"]
            )
            if not self._current(generation, session_id, turn_id):
                return
            if not evidence:
                generated = GeneratedAnswer(
                    status=AnswerStatus.INSUFFICIENT,
                    answer_text="В базе знаний нет сведений, по которым я могу ответить на этот вопрос.",
                    source_ids=[],
                )
                provider = "local_no_evidence"
            else:
                generated, provider = await self.providers.answer(text, history, evidence)
            if not self._current(generation, session_id, turn_id):
                return
            self.sources = [s for s in evidence if s.id in generated.source_ids]
            self.answer = AnswerResult(
                **generated.model_dump(),
                session_id=session_id,
                turn_id=turn_id,
                kb_release_id=metadata["release_id"],
            )
            self.history.append(Message(role="assistant", text=generated.answer_text))
            self.journal.record(
                turn_id=turn_id,
                session_id=session_id,
                question=text,
                answer=generated.answer_text,
                status=generated.status.value,
                provider=provider,
                kb_id=metadata["kb_id"],
                release_id=metadata["release_id"],
                sources=self.sources,
            )
            self.emit(
                "answer",
                answer=self.answer.model_dump(mode="json"),
                sources=[s.model_dump() for s in self.sources],
                provider=provider,
            )
            if self.speech and self.settings.value.speech_enabled:
                audio = await self.speech.synthesize(generated.answer_text)
                if not self._current(generation, session_id, turn_id):
                    return
                self._state(State.SPEAKING)
                self.emit(
                    "speech",
                    audio_b64=audio.base64(),
                    sample_rate=audio.sample_rate,
                    text=generated.answer_text,
                    duration=audio.duration,
                    speech_marks=audio.marks_payload(len(generated.answer_text)),
                )
            else:
                self._state(State.LISTENING)
                self.activity()
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            await self._failure(
                ProviderError("timeout", "Не удалось дождаться ответа. Попробуйте позже"),
                generation,
                session_id,
                turn_id,
                text,
                metadata,
            )
        except ProviderError as exc:
            await self._failure(exc, generation, session_id, turn_id, text, metadata)
        except Exception:
            await self._failure(
                ProviderError(
                    "internal_error", "Возникла техническая ошибка. Обратитесь к сотруднику", False
                ),
                generation,
                session_id,
                turn_id,
                text,
                metadata,
            )

    async def _failure(self, error: ProviderError, generation, session_id, turn_id, text, metadata):
        if not self._current(generation, session_id, turn_id):
            return
        self.journal.record(
            turn_id=turn_id,
            session_id=session_id,
            question=text,
            answer="",
            status=error.code,
            provider=self.settings.value.provider,
            kb_id=metadata["kb_id"],
            release_id=metadata["release_id"],
            sources=[],
        )
        self.emit("error", code=error.code, message=error.message)
        await self.finish(reason=error.code)

    async def audio(self, samples: bytes, sample_rate: int) -> None:
        if not self.active:
            raise SessionError("Сначала начните разговор")
        if not self.speech or not self.settings.value.speech_enabled:
            raise SessionError("Локальное распознавание речи не настроено")
        if not samples or len(samples) % 2 or len(samples) > sample_rate * 2 * 45:
            raise SessionError("Некорректный PCM или реплика длиннее 45 секунд")
        if not await self.interrupt():
            raise SessionError("Реплика отменена или разговор уже завершён")
        self.turn_id = uuid.uuid4().hex
        self.sources = []
        self.answer = None
        self._spoken_characters = 0
        self.emit("turn_started")
        self._state(State.TRANSCRIBING)
        generation, session_id, turn_id = self._generation, self.session_id, self.turn_id

        async def transcribe():
            try:
                text = await self.speech.transcribe(samples, sample_rate)
                if not self._current(generation, session_id, turn_id):
                    return
                if text.strip():
                    # Detach this completed transcription so submit does not cancel itself.
                    self._task = None
                    await self.submit(text)
                else:
                    await self._asr_clarification(generation, session_id, turn_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                if self._current(generation, session_id, turn_id):
                    await self._asr_clarification(generation, session_id, turn_id)

        self._task = asyncio.create_task(transcribe())

    async def _asr_clarification(self, generation: int, session_id: str, turn_id: str) -> None:
        text = "Не удалось разобрать вопрос. Повторите, пожалуйста."
        self.emit("clarify", text=text)
        try:
            audio = await self.speech.synthesize(text)
        except asyncio.CancelledError:
            raise
        except Exception:
            if self._current(generation, session_id, turn_id):
                self.emit(
                    "error",
                    code="tts_error",
                    message="Озвучивание недоступно. Обратитесь к сотруднику",
                )
                await self.finish("tts_error")
            return
        if not self._current(generation, session_id, turn_id):
            return
        metadata = self.kb.metadata()
        self.answer = AnswerResult(
            status=AnswerStatus.CLARIFY,
            answer_text=text,
            source_ids=[],
            session_id=session_id,
            turn_id=turn_id,
            kb_release_id=metadata["release_id"],
        )
        self.history.append(Message(role="assistant", text=text))
        self.journal.record(
            turn_id=turn_id,
            session_id=session_id,
            question="",
            answer=text,
            status=AnswerStatus.CLARIFY.value,
            provider="local_clarification",
            kb_id=metadata["kb_id"],
            release_id=metadata["release_id"],
            sources=[],
        )
        self.emit(
            "answer",
            answer=self.answer.model_dump(mode="json"),
            sources=[],
            provider="local_clarification",
        )
        self._state(State.SPEAKING)
        self.emit(
            "speech",
            audio_b64=audio.base64(),
            sample_rate=audio.sample_rate,
            text=text,
            duration=audio.duration,
            speech_marks=audio.marks_payload(len(text)),
        )

    def playback_progress(self, turn_id: str, characters: int) -> None:
        if self.state == State.SPEAKING and self.turn_id == turn_id and self.answer:
            self._spoken_characters = min(
                max(self._spoken_characters, characters), len(self.answer.answer_text)
            )

    def playback_finished(self, turn_id: str) -> None:
        if self.state != State.SPEAKING or self.turn_id != turn_id:
            return
        if self.answer:
            self.journal.playback(turn_id, self.answer.answer_text)
            if self.history and self.history[-1].role == "assistant":
                self.history[-1].spoken_text = self.answer.answer_text
        self._state(State.LISTENING)
        self.activity()

    async def tick(self) -> None:
        if self.state != State.LISTENING:
            return
        elapsed = self.clock() - self.last_activity
        if elapsed >= self.settings.value.idle_seconds + self.settings.value.warning_seconds:
            await self.finish("idle_timeout")
        elif elapsed >= self.settings.value.idle_seconds:
            remaining = max(
                0,
                int(
                    self.settings.value.idle_seconds + self.settings.value.warning_seconds - elapsed
                ),
            )
            self.warning = True
            self.emit("idle_warning", visible=True, seconds=remaining)
