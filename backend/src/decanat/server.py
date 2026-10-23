from __future__ import annotations

import asyncio
import base64
import binascii
import secrets
from contextlib import asynccontextmanager, suppress
from functools import partial
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, ValidationError

from decanat.config import RuntimeLease, Settings, SettingsStore
from decanat.contracts import ClientCommand
from decanat.journal import Journal
from decanat.knowledge import MAX_ARCHIVE, KnowledgeStore, PackageError, SentenceEncoder
from decanat.providers import Providers
from decanat.security import AdminAuth
from decanat.session import Conversation, SessionError
from decanat.speech import LocalSpeech


class Login(BaseModel):
    password: str = Field(min_length=1, max_length=256)


async def complete_mutation(operation, *args, on_success=None, **kwargs):
    """Do not leave a mutating worker running after releasing maintenance on cancellation.

    run_in_executor returns a Future (not another Task cancelled at loop shutdown).
    Defer caller cancellation until the atomic install has actually finished.
    """
    future = asyncio.get_running_loop().run_in_executor(None, partial(operation, *args, **kwargs))
    cancelled = False
    while not future.done():
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    try:
        result = future.result()
    except Exception:
        if cancelled:
            raise asyncio.CancelledError from None
        raise
    if on_success:
        on_success(result)
    if cancelled:
        raise asyncio.CancelledError
    return result


def create_app(root: Path, ipc_token: str, *, conversation: Conversation | None = None) -> FastAPI:
    if len(ipc_token) < 32:
        raise ValueError("IPC token must contain at least 32 characters")
    settings = conversation.settings if conversation else SettingsStore(root)
    encoder = (
        SentenceEncoder(settings.value.embedding_model, settings.value.embedding_revision)
        if settings.value.dense_enabled
        else None
    )
    kb = conversation.kb if conversation else KnowledgeStore(root, encoder)
    journal = conversation.journal if conversation else Journal(root)
    speech = LocalSpeech(settings)
    controller = conversation or Conversation(
        settings, kb, Providers(settings), journal, speech=speech
    )
    auth = AdminAuth(root)
    controller_lock = asyncio.Lock()
    client_connected = False
    audio_agent = None
    audio_error = "Локальная речь не включена"
    knowledge_error = ""
    restart_required = False
    runtime_fields = (
        "whisper_model_path",
        "whisper_use_gpu",
        "silero_model_path",
        "silero_speaker",
        "vad_model_path",
        "apm_library_path",
        "speech_enabled",
        "input_device",
        "output_device",
    )
    startup_settings = settings.snapshot()

    @asynccontextmanager
    async def lifespan(app):
        nonlocal audio_agent, audio_error, knowledge_error
        lease = RuntimeLease(root)
        lease.__enter__()
        try:
            await asyncio.to_thread(kb.warm_up)
        except Exception:
            knowledge_error = "Проверьте локальную модель поиска и перестройте индекс в даунтайме"
        if settings.value.speech_enabled:
            try:
                from decanat.audio_device import AudioDeviceAgent

                await speech.warm_up()
                audio_agent = AudioDeviceAgent(controller, Path(settings.value.apm_library_path))
                await audio_agent.start()
                audio_error = ""
            except Exception:
                if audio_agent:
                    await audio_agent.close()
                audio_agent = None
                audio_error = "Проверьте локальные модели, аудиоустройства и библиотеку WebRTC AEC"

        async def housekeeping():
            ticks = 0
            while True:
                await asyncio.sleep(1)
                async with controller_lock:
                    await controller.tick()
                ticks += 1
                if ticks % 3600 == 0:
                    await asyncio.to_thread(journal.purge)

        task = asyncio.create_task(housekeeping())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            try:
                await controller.finish("shutdown")
                if audio_agent:
                    await audio_agent.close()
            finally:
                lease.__exit__(None, None, None)

    app = FastAPI(
        title="ИИ-деканат: локальный backend",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.state.controller = controller
    app.state.auth = auth
    app.state.settings = settings

    @app.middleware("http")
    async def origin_guard(request: Request, call_next):
        # The native client does not use a browser origin. Reject CSRF/DNS-rebinding traffic.
        if request.headers.get("origin") or request.url.hostname not in {
            "127.0.0.1",
            "localhost",
            "testserver",
        }:
            from fastapi.responses import JSONResponse

            return JSONResponse({"detail": "Недопустимый источник запроса"}, status_code=403)
        return await call_next(request)

    def ipc(authorization: str = Header(default="")):
        if not secrets.compare_digest(authorization, f"Bearer {ipc_token}"):
            raise HTTPException(401, "Требуется локальное подключение приложения")

    def admin(x_admin_token: str = Header(default=""), _=Depends(ipc)):
        if not auth.check(x_admin_token):
            raise HTTPException(401, "Требуется вход сотрудника")
        return x_admin_token

    @app.get("/v1/health", dependencies=[Depends(ipc)])
    def health():
        return {
            "version": "0.1.0",
            "state": controller.state.value,
            "kb_installed": kb.installed,
            "knowledge_ready": kb.installed and not knowledge_error,
            "knowledge_error": knowledge_error,
            "admin_configured": auth.configured,
            "speech": speech.diagnostics(),
            "mode": "voice" if settings.value.speech_enabled else "text_diagnostics",
            "audio_device_ready": bool(audio_agent and audio_agent.ready and not restart_required),
            "audio_error": audio_error,
            "restart_required": restart_required,
            "avatar_assets_verified": False,
        }

    @app.post("/v1/admin/login", dependencies=[Depends(ipc)])
    def login(body: Login):
        try:
            token = auth.login(body.password)
        except ValueError as exc:
            raise HTTPException(429, str(exc)) from exc
        if not token:
            raise HTTPException(401, "Неверный пароль или сотрудник ещё не настроен")
        return {"admin_token": token, "expires_in": 1800}

    @app.post("/v1/admin/logout")
    def logout(token=Depends(admin)):
        auth.logout(token)
        return {"ok": True}

    @app.get("/v1/admin/settings", dependencies=[Depends(admin)])
    def get_settings():
        return settings.snapshot()

    @app.put("/v1/admin/settings", dependencies=[Depends(admin)])
    async def put_settings(body: Settings):
        nonlocal restart_required
        async with controller_lock:
            if controller.state.value != "idle" or controller.maintenance:
                raise HTTPException(409, "Настройки меняются только без активного разговора")
            # Changing an indexed model requires reimport before the next student session.
            new_encoder = (
                SentenceEncoder(body.embedding_model, body.embedding_revision)
                if body.dense_enabled
                else None
            )
            metadata = kb.metadata()
            fingerprint = new_encoder.fingerprint if new_encoder else "lexical-only"
            if metadata and metadata["encoder"] != fingerprint:
                raise HTTPException(
                    409,
                    "Остановите приложение и перестройте индекс с новой моделью через CLI",
                )
            settings.save(body)
            restart_required = any(
                getattr(body, key) != startup_settings[key] for key in runtime_fields
            )
            if (kb.encoder.fingerprint if kb.encoder else "lexical-only") != fingerprint:
                kb.encoder = new_encoder
            controller.providers.unavailable.clear()
            return {"settings": settings.snapshot(), "restart_required": restart_required}

    @app.get("/v1/admin/diagnostics", dependencies=[Depends(admin)])
    def diagnostics():
        import platform
        import shutil

        from decanat.audio_device import device_inventory

        return {
            "health": health(),
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "audio": device_inventory(),
            "codex_found": bool(shutil.which(settings.value.codex_binary)),
            "api_key_configured": bool(settings.api_key()),
            "api_model_configured": bool(settings.value.api_model),
            "knowledge": kb.metadata(),
        }

    @app.post("/v1/admin/api-key", dependencies=[Depends(admin)])
    async def set_api_key(request: Request):
        data = await request.body()
        if len(data) > 1024:
            raise HTTPException(413, "Слишком длинный ключ")
        key = data.decode("utf-8").strip()
        if not key:
            raise HTTPException(400, "Ключ пуст")
        try:
            await asyncio.to_thread(settings.set_api_key, key)
        except Exception as exc:
            raise HTTPException(503, "Защищённое хранилище ОС недоступно") from exc
        controller.providers.unavailable.pop("api", None)
        return {"stored": True}

    @app.post("/v1/admin/password", dependencies=[Depends(admin)])
    async def password(body: Login):
        try:
            await asyncio.to_thread(auth.set_password, body.password)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"ok": True, "login_required": True}

    @app.get("/v1/admin/journal", dependencies=[Depends(admin)])
    def get_journal(limit: int = 100, offset: int = 0):
        if not 1 <= limit <= 200 or offset < 0:
            raise HTTPException(422, "Некорректные параметры страницы")
        return {"items": journal.list(limit, offset)}

    @app.get("/v1/admin/knowledge", dependencies=[Depends(admin)])
    def knowledge_info():
        return {"knowledge": kb.metadata()}

    @app.post("/v1/admin/knowledge", dependencies=[Depends(admin)])
    async def knowledge_upload(request: Request, replace_kb: bool = False):
        def installed(metadata):
            nonlocal knowledge_error
            knowledge_error = ""
            controller.emit("knowledge_updated", knowledge=metadata)

        async with controller_lock:
            if controller.state.value != "idle" or controller.maintenance:
                raise HTTPException(409, "Обновление базы возможно только в даунтайме")
            controller.maintenance = True
            controller.emit("maintenance", enabled=True)
        try:
            data = bytearray()
            async for chunk in request.stream():
                if len(data) + len(chunk) > MAX_ARCHIVE:
                    raise HTTPException(413, "Архив превышает 50 MiB")
                data.extend(chunk)
            try:
                metadata = await complete_mutation(
                    kb.install, bytes(data), replace_kb=replace_kb, on_success=installed
                )
            except PackageError as exc:
                raise HTTPException(422, str(exc)) from exc
            return {"knowledge": metadata}
        finally:
            controller.maintenance = False
            controller.emit("maintenance", enabled=False)

    @app.post("/v1/admin/provider-reset", dependencies=[Depends(admin)])
    def provider_reset():
        controller.providers.unavailable.clear()
        return {"ok": True}

    @app.websocket("/v1/conversation")
    async def websocket(ws: WebSocket):
        nonlocal client_connected
        supplied = ws.headers.get("authorization", "")
        if ws.headers.get("origin") or not secrets.compare_digest(supplied, f"Bearer {ipc_token}"):
            await ws.close(code=1008)
            return
        if client_connected:
            await ws.close(code=1013)
            return
        client_connected = True
        await ws.accept()
        queue = controller.subscribe()

        async def send_events():
            while True:
                event = await queue.get()
                await ws.send_text(event.model_dump_json())
                if event.kind == "resync_required":
                    await ws.close(code=1013)
                    return

        sender = asyncio.create_task(send_events())
        try:
            controller.emit("snapshot", **controller.snapshot())
            while True:
                raw = await ws.receive_text()
                if len(raw) > 4_010_000:
                    await ws.close(code=1009)
                    break
                try:
                    command = ClientCommand.model_validate_json(raw)
                    async with controller_lock:
                        if (
                            command.type not in {"start"}
                            and command.session_id != controller.session_id
                        ):
                            raise SessionError("Устаревшая сессия")
                        if command.type == "start":
                            if restart_required:
                                raise SessionError(
                                    "Настройки изменены. Сотруднику нужно перезапустить приложение"
                                )
                            if knowledge_error:
                                raise SessionError("База знаний требует настройки сотрудником")
                            if settings.value.speech_enabled and not (
                                audio_agent and audio_agent.ready
                            ):
                                raise SessionError(audio_error or "Аудиоустройство недоступно")
                            controller.start()
                        elif command.type == "finish":
                            await controller.finish()
                        elif command.type == "avatar_ready":
                            controller.avatar_ready()
                        elif command.type == "text":
                            await controller.submit(command.text)
                        elif command.type == "speech_start":
                            await controller.interrupt()
                        elif command.type == "audio_end":
                            pcm = base64.b64decode(command.audio_b64, validate=True)
                            await controller.audio(pcm, command.sample_rate)
                        elif command.type == "playback_progress":
                            controller.playback_progress(command.turn_id, command.played_characters)
                        elif command.type == "playback_finished":
                            controller.playback_finished(command.turn_id)
                        elif command.type == "activity":
                            controller.activity()
                        elif command.type == "sources":
                            controller.activity()
                            controller.emit(
                                "sources", items=[s.model_dump() for s in controller.sources]
                            )
                except (ValidationError, SessionError, binascii.Error) as exc:
                    message = (
                        str(exc)
                        if isinstance(exc, SessionError)
                        else "Некорректная команда приложения"
                    )
                    controller.emit("command_error", message=message)
        except WebSocketDisconnect:
            pass
        finally:
            sender.cancel()
            with suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                await sender
            controller.unsubscribe(queue)
            async with controller_lock:
                await controller.finish("client_disconnected")
                controller.avatar_ready()
            client_connected = False

    return app
