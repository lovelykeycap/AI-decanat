from __future__ import annotations

import hashlib
import io
import json
import lzma
import math
import os
import re
import sqlite3
import stat
import tempfile
import threading
import zipfile
import zlib
from collections import defaultdict
from pathlib import Path
from typing import Protocol

import numpy as np
from pydantic import ValidationError

from decanat.contracts import Document, Evidence, Manifest

MAX_ARCHIVE = 50 * 1024 * 1024
MAX_EXPANDED = 200 * 1024 * 1024
# Function words must not give an unrelated document a second RRF vote just because
# it contains «мне», «что» or «куда». The semantic encoder still receives the full question.
LEXICAL_STOP_WORDS = frozenset(
    "а и но или либо да нет не ни же ли бы то это вот в во на по при к ко с со у о об "
    "от до из за для без под над через между про я мне меня мной мы нам нас вы вам вас "
    "ты тебе тебя он она оно они его ее её их наш наша наши ваш ваша ваши мой моя мои "
    "свой своя свои как что чтобы кто где куда откуда когда почему зачем какой какая "
    "какое какие какого какой-то сколько этот эта эти здесь там тут так тоже уже еще ещё "
    "есть будет будут был была были быть нужно надо нужен нужна нужны могу можно хочу "
    "хотел хотелось пожалуйста пойти идти прийти этим этом этому этой этого его её ее".split()
)


class PackageError(ValueError):
    pass


def strict_json(raw: str | bytes):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise PackageError(f"Повторяющийся JSON-ключ: {key}")
            value[key] = item
        return value

    def no_constant(value):
        raise PackageError(f"Недопустимое JSON-значение: {value}")

    try:
        result = json.loads(raw, object_pairs_hook=pairs, parse_constant=no_constant)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        raise PackageError("Некорректный JSON или UTF-8") from exc

    def controls(value, depth=0):
        if depth > 64:
            raise PackageError("Слишком глубокая вложенность JSON")
        if isinstance(value, float) and not math.isfinite(value):
            raise PackageError("Недопустимое неограниченное JSON-число")
        if isinstance(value, str) and any(0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise PackageError("Некорректный Unicode в JSON")
        if isinstance(value, str) and any(ord(c) < 32 and c not in "\r\n\t" for c in value):
            raise PackageError("Недопустимый управляющий символ")
        if isinstance(value, list):
            for item in value:
                controls(item, depth + 1)
        if isinstance(value, dict):
            for key, item in value.items():
                controls(key, depth + 1)
                controls(item, depth + 1)

    controls(result)
    return result


def validate_package(data: bytes) -> tuple[Manifest, list[Document]]:
    if len(data) > MAX_ARCHIVE:
        raise PackageError("Архив превышает 50 MiB")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if sorted(item.filename for item in infos) != ["documents.jsonl", "manifest.json"]:
                raise PackageError("В корне ZIP должны быть только manifest.json и documents.jsonl")
            if sum(item.file_size for item in infos) > MAX_EXPANDED:
                raise PackageError("Распакованные данные превышают 200 MiB")
            for item in infos:
                mode = item.external_attr >> 16
                if item.flag_bits & 1 or stat.S_ISLNK(mode) or item.is_dir():
                    raise PackageError("Шифрование, каталоги и симлинки не поддерживаются")
                if stat.S_IFMT(mode) not in (0, stat.S_IFREG):
                    raise PackageError("Разрешены только обычные файлы")
            raw_manifest = archive.read("manifest.json")
            raw_documents = archive.read("documents.jsonl").decode("utf-8")
    except (
        zipfile.BadZipFile,
        UnicodeDecodeError,
        RuntimeError,
        OSError,
        NotImplementedError,
        EOFError,
        zlib.error,
        lzma.LZMAError,
    ) as exc:
        raise PackageError("Повреждённый ZIP или неверная кодировка UTF-8") from exc
    try:
        manifest = Manifest.model_validate(strict_json(raw_manifest))
    except ValidationError as exc:
        raise PackageError(f"manifest.json: {exc}") from exc
    documents = []
    seen = set()
    section_count = 0
    for line_no, line in enumerate(raw_documents.splitlines(), 1):
        if not line.strip():
            raise PackageError(f"documents.jsonl, строка {line_no}: пустая строка")
        try:
            document = Document.model_validate(strict_json(line))
        except (ValidationError, PackageError) as exc:
            raise PackageError(f"documents.jsonl, строка {line_no}: {exc}") from exc
        if document.id in seen:
            raise PackageError(f"documents.jsonl, строка {line_no}: повтор id документа")
        seen.add(document.id)
        section_count += len(document.sections)
        documents.append(document)
        if len(documents) > 10_000 or section_count > 50_000:
            raise PackageError("Превышен предел числа документов или фрагментов")
    if not documents:
        raise PackageError("База знаний пуста")
    return manifest, documents


class Encoder(Protocol):
    fingerprint: str

    def encode(self, texts: list[str]) -> np.ndarray: ...


class SentenceEncoder:
    def __init__(self, model: str, revision: str):
        self.model_name = model
        self.revision = revision
        self.fingerprint = f"{model}@{revision}"
        self._model = None
        self._lock = threading.Lock()

    def encode(self, texts: list[str]) -> np.ndarray:
        # Cancelling asyncio.to_thread does not stop native inference. Serialize subsequent
        # turns so a cancelled request cannot trigger duplicate model loads or concurrent use.
        with self._lock:
            if self._model is None:
                import torch
                from sentence_transformers import SentenceTransformer

                if not self.revision and not Path(self.model_name).is_dir():
                    raise PackageError("Для embedding-модели требуется закреплённая ревизия")
                torch.set_num_threads(4)
                self._model = SentenceTransformer(
                    self.model_name,
                    revision=self.revision or None,
                    local_files_only=True,
                    device="cpu",
                )
            return np.asarray(
                self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False),
                dtype=np.float32,
            )


def tokens(text: str) -> list[str]:
    return re.findall(r"[\w]+", text.lower().replace("ё", "е"), flags=re.UNICODE)


class KnowledgeStore:
    def __init__(self, root: Path, encoder: Encoder | None = None):
        self.root = root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = root / "knowledge.sqlite3"
        self.encoder = encoder

    @property
    def installed(self) -> bool:
        return self.path.exists()

    def _connect(self):
        return sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)

    def metadata(self) -> dict | None:
        if not self.installed:
            return None
        with self._connect() as db:
            value = dict(db.execute("SELECT key,value FROM metadata"))
        return {
            **json.loads(value["manifest"]),
            "sha256": value["sha256"],
            "encoder": value["encoder"],
        }

    def ensure_compatible(self) -> None:
        metadata = self.metadata()
        if metadata and self.encoder and metadata["encoder"] != self.encoder.fingerprint:
            raise PackageError("Индекс другой embedding-модели; перестройте базу в даунтайме")

    def warm_up(self) -> None:
        self.ensure_compatible()
        if self.encoder:
            self.encoder.encode(["Проверка готовности локального поиска"])

    def install(self, data: bytes, *, replace_kb: bool = False) -> dict:
        manifest, documents = validate_package(data)
        current = self.metadata()
        if current and current["kb_id"] != manifest.kb_id and not replace_kb:
            raise PackageError("Другой kb_id. Требуется явное разрешение замены базы")
        if (
            current
            and current["kb_id"] == manifest.kb_id
            and current["release_id"] == manifest.release_id
            and current["sha256"] != hashlib.sha256(data).hexdigest()
        ):
            raise PackageError("Содержимое изменено: укажите новый release_id")
        entries = []
        texts = []
        for document in documents:
            for section in document.sections:
                evidence = Evidence(
                    id=f"{document.id}/{section.id}",
                    document_title=document.title,
                    section_title=section.title,
                    source_label=document.source_label,
                    source_url=document.source_url,
                    locator=section.locator,
                    text=section.text,
                )
                index_text = " ".join(
                    [document.title, section.title, section.text, *section.search_phrases]
                )
                entries.append((evidence, " ".join(tokens(index_text))))
                texts.append(index_text)
        vectors = self.encoder.encode(texts) if self.encoder else None
        if vectors is not None and (len(vectors) != len(entries) or not np.isfinite(vectors).all()):
            raise PackageError("Не удалось построить корректный векторный индекс")
        fd, temporary_name = tempfile.mkstemp(prefix="kb-", suffix=".sqlite3", dir=self.root)
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            with sqlite3.connect(temporary) as db:
                db.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL)")
                db.execute(
                    "CREATE TABLE fragments (id TEXT PRIMARY KEY,evidence TEXT NOT NULL,vector BLOB)"
                )
                db.execute(
                    "CREATE VIRTUAL TABLE search USING fts5(id UNINDEXED,content,tokenize='unicode61')"
                )
                for i, (evidence, text) in enumerate(entries):
                    vector = (
                        vectors[i].astype(np.float32).tobytes() if vectors is not None else None
                    )
                    db.execute(
                        "INSERT INTO fragments VALUES (?,?,?)",
                        (evidence.id, evidence.model_dump_json(), vector),
                    )
                    db.execute("INSERT INTO search VALUES (?,?)", (evidence.id, text))
                db.executemany(
                    "INSERT INTO metadata VALUES (?,?)",
                    [
                        ("manifest", manifest.model_dump_json()),
                        ("sha256", hashlib.sha256(data).hexdigest()),
                        ("encoder", self.encoder.fingerprint if self.encoder else "lexical-only"),
                    ],
                )
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise PackageError("Ошибка проверки целостности новой базы")
            with temporary.open("rb") as stream:
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        return self.metadata()

    def search(
        self, question: str, history: list[str] | None = None, limit: int = 8
    ) -> list[Evidence]:
        if not self.installed:
            return []
        question_tokens = tokens(question)
        # Carry the subject of the last two user turns into short follow-up searches.
        context = " ".join((history or [])[-2:])
        query = question if len(question_tokens) >= 8 else f"{context} {question}"
        unique = list(dict.fromkeys(t for t in tokens(query) if t not in LEXICAL_STOP_WORDS))[:80]
        ranks: dict[str, float] = defaultdict(float)
        with self._connect() as db:
            if unique:
                fts = " OR ".join('"' + token.replace('"', '""') + '"' for token in unique)
                lexical = db.execute(
                    "SELECT id FROM search WHERE search MATCH ? ORDER BY bm25(search) LIMIT 24",
                    (fts,),
                )
                for rank, (source_id,) in enumerate(lexical):
                    ranks[source_id] += 1 / (60 + rank)
            if self.encoder:
                stored = db.execute("SELECT value FROM metadata WHERE key='encoder'").fetchone()[0]
                if stored != self.encoder.fingerprint:
                    raise PackageError(
                        "Индекс другой embedding-модели; обновите базу в режиме обслуживания"
                    )
                query_vector = self.encoder.encode([query])[0]
                norm = np.linalg.norm(query_vector)
                dense = []
                for source_id, blob in db.execute(
                    "SELECT id,vector FROM fragments WHERE vector IS NOT NULL"
                ):
                    vector = np.frombuffer(blob, dtype=np.float32)
                    similarity = float(
                        np.dot(vector, query_vector) / max(np.linalg.norm(vector) * norm, 1e-12)
                    )
                    if math.isfinite(similarity):
                        dense.append((similarity, source_id))
                for rank, (_, source_id) in enumerate(sorted(dense, reverse=True)[:24]):
                    ranks[source_id] += 1 / (60 + rank)
            results = []
            for source_id in sorted(ranks, key=lambda key: (-ranks[key], key))[:limit]:
                row = db.execute(
                    "SELECT evidence FROM fragments WHERE id=?", (source_id,)
                ).fetchone()
                results.append(Evidence.model_validate_json(row[0]))
        return results
