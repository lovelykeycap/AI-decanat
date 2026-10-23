import io
import json
import zipfile
from pathlib import Path

import pytest
from decanat.config import SettingsStore
from decanat.contracts import AnswerStatus, GeneratedAnswer
from decanat.journal import Journal
from decanat.knowledge import KnowledgeStore
from decanat.providers import Providers
from decanat.session import Conversation

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "knowledge"


def package(documents=None, manifest=None, extra=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(manifest)
            if manifest is not None
            else (EXAMPLES / "manifest.json").read_bytes(),
        )
        archive.writestr(
            "documents.jsonl",
            json.dumps(documents, ensure_ascii=False)
            if documents is not None
            else (EXAMPLES / "documents.jsonl").read_bytes(),
        )
        if extra:
            archive.writestr(*extra)
    return output.getvalue()


class AnswerProvider:
    def __init__(self):
        self.calls = []

    async def generate(self, question, history, evidence):
        self.calls.append((question, list(history), evidence))
        return GeneratedAnswer(
            status=AnswerStatus.ANSWER,
            answer_text="Справка готовится три рабочих дня.",
            source_ids=[evidence[0].id],
        )


class Clock:
    value = 1000.0

    def __call__(self):
        return self.value


@pytest.fixture
def kb(tmp_path):
    kb = KnowledgeStore(tmp_path)
    kb.install(package())
    return kb


@pytest.fixture
def controller(tmp_path, kb):
    settings = SettingsStore(tmp_path)
    provider = AnswerProvider()
    providers = Providers(settings, codex=provider, api=provider)
    result = Conversation(settings, kb, providers, Journal(tmp_path), clock=Clock())
    return result
