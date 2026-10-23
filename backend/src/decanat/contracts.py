from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_-]{1,80}$")]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Manifest(StrictModel):
    schema_version: Literal["1.0"]
    kb_id: Identifier
    release_id: Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9._-]{1,80}$")]
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    language: Literal["ru"]
    prepared_by: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
    ]
    prepared_at: str
    documents_file: Literal["documents.jsonl"]

    @field_validator("prepared_at")
    @classmethod
    def timestamp_has_zone(cls, value: str) -> str:
        import re
        from datetime import datetime

        if not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value
        ):
            raise ValueError("Требуется дата RFC 3339 с часовым поясом")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Требуется дата RFC 3339 с часовым поясом")
        return value


class Section(StrictModel):
    id: Identifier
    title: Title
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]
    locator: Title
    search_phrases: Annotated[list[Title], Field(max_length=20)]

    @field_validator("text", "title", "locator")
    @classmethod
    def printable(cls, value: str) -> str:
        if any(ord(c) < 32 and c not in "\n\r\t" for c in value):
            raise ValueError("Недопустимый управляющий символ")
        return value


class Document(StrictModel):
    id: Identifier
    title: Title
    source_label: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
    ]
    source_url: Annotated[str, StringConstraints(max_length=2048)] | None
    sections: Annotated[list[Section], Field(min_length=1)]

    @field_validator("source_url")
    @classmethod
    def https_only(cls, value: str | None) -> str | None:
        from urllib.parse import urlsplit

        if value is not None:
            parsed = urlsplit(value)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ValueError("Разрешена только HTTPS-ссылка без логина и пароля")
        return value

    @model_validator(mode="after")
    def unique_sections(self) -> Document:
        ids = [s.id for s in self.sections]
        if len(ids) != len(set(ids)):
            raise ValueError("Идентификаторы фрагментов в документе должны быть уникальны")
        return self


class Evidence(StrictModel):
    id: str
    document_title: str
    section_title: str
    source_label: str
    source_url: str | None
    locator: str
    text: str


class AnswerStatus(StrEnum):
    ANSWER = "answer"
    CLARIFY = "clarify"
    INSUFFICIENT = "insufficient_evidence"
    CONFLICT = "conflicting_evidence"


class GeneratedAnswer(StrictModel):
    """Only the provider-generated fields; IDs are assigned by the application."""

    status: AnswerStatus
    answer_text: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)
    ]
    source_ids: Annotated[list[str], Field(max_length=12)]


class AnswerResult(GeneratedAnswer):
    session_id: str
    turn_id: str
    kb_release_id: str


class State(StrEnum):
    IDLE = "idle"
    STANDING_UP = "standing_up"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    SPEAKING = "speaking"
    SITTING_DOWN = "sitting_down"


class Message(StrictModel):
    role: Literal["user", "assistant"]
    text: str
    interrupted: bool = False
    spoken_text: str = ""


class Event(StrictModel):
    sequence: int
    kind: str
    session_id: str | None
    turn_id: str | None
    payload: dict


class ClientCommand(StrictModel):
    type: Literal[
        "start",
        "finish",
        "text",
        "speech_start",
        "audio_end",
        "playback_progress",
        "playback_finished",
        "avatar_ready",
        "activity",
        "sources",
    ]
    session_id: str | None = None
    turn_id: str | None = None
    text: Annotated[str, StringConstraints(max_length=4000)] = ""
    sample_rate: Literal[16000, 24000, 48000] = 16000
    audio_b64: Annotated[str, StringConstraints(max_length=4_000_000)] = ""
    played_characters: Annotated[int, Field(ge=0, le=8000)] = 0
