from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Literal

from pydantic import Field

from decanat.contracts import StrictModel


class Settings(StrictModel):
    provider: Literal["codex", "api"] = "codex"
    api_fallback: bool = False
    provider_timeout: int = Field(default=60, ge=10, le=180)
    codex_binary: str = "codex"
    codex_model: str = ""
    api_model: str = ""
    embedding_model: str = "BAAI/bge-m3"
    embedding_revision: str = ""
    dense_enabled: bool = False
    whisper_model_path: str = ""
    whisper_use_gpu: bool = False
    silero_model_path: str = ""
    silero_speaker: str = "aidar"
    vad_model_path: str = ""
    apm_library_path: str = ""
    speech_enabled: bool = False
    idle_seconds: int = Field(default=60, ge=5, le=600)
    warning_seconds: int = Field(default=15, ge=3, le=60)
    input_device: str = ""
    output_device: str = ""


def data_directory() -> Path:
    return Path(os.environ.get("DECANAT_DATA_DIR", ".runtime")).resolve()


def write_private(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".tmp-" + secrets.token_hex(4))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class SettingsStore:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "settings.json"
        self.value = (
            Settings.model_validate_json(self.path.read_text())
            if self.path.exists()
            else Settings()
        )

    def save(self, value: Settings) -> None:
        write_private(self.path, value.model_dump_json(indent=2))
        self.value = value

    def api_key(self) -> str | None:
        key = os.environ.get("OPENAI_API_KEY")
        if key:
            return key
        try:
            import keyring

            return keyring.get_password("ai-decanat", "openai-api-key")
        except Exception:
            return None

    def set_api_key(self, key: str) -> None:
        import keyring

        keyring.set_password("ai-decanat", "openai-api-key", key)

    def snapshot(self) -> dict:
        return json.loads(self.value.model_dump_json())


class RuntimeLease:
    """Cross-process exclusion: offline CLI changes cannot race the running application."""

    def __init__(self, root: Path):
        self.root = root
        self.stream = None

    def __enter__(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.stream = (self.root / "runtime.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                self.stream.seek(0)
                self.stream.write(b"0")
                self.stream.flush()
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            self.stream = None
            raise ValueError(
                "Приложение уже запущено. Для изменения файлов сначала остановите его"
            ) from exc
        return self

    def __exit__(self, *args):
        if self.stream:
            if os.name == "nt":
                import msvcrt

                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            self.stream.close()
            self.stream = None
