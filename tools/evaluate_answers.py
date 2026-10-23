"""Run explicit offline retrieval checks or real-provider evaluation on fictional data.

Network/provider calls happen only with --provider codex or --provider api.
This does not replace factual acceptance on the university's approved knowledge base.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import re
import time
import zipfile
from pathlib import Path

from decanat.cli import make_kb
from decanat.config import RuntimeLease, SettingsStore, write_private
from decanat.contracts import AnswerStatus, Message, StrictModel
from decanat.providers import ProviderError, Providers

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "examples/evaluation"


class Case(StrictModel):
    id: str
    question: str
    history: list[Message]
    expected_status: AnswerStatus
    source_ids: list[str]
    required_patterns: list[str]
    forbidden_patterns: list[str]


def fixture_package(directory: Path) -> bytes:
    # Stable ZIP headers let the same immutable fixture release be reimported.
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name in ("manifest.json", "documents.jsonl"):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, (directory / name).read_bytes())
    return output.getvalue()


def answer_problems(case: Case, answer) -> list[str]:
    problems = []
    if answer.status != case.expected_status:
        problems.append(f"status: expected {case.expected_status}, got {answer.status}")
    missing = set(case.source_ids) - set(answer.source_ids)
    if missing:
        problems.append("missing answer sources: " + ", ".join(sorted(missing)))
    for pattern in case.required_patterns:
        if not re.search(pattern, answer.answer_text, re.IGNORECASE):
            problems.append("required fact pattern missing: " + pattern)
    for pattern in case.forbidden_patterns:
        if re.search(pattern, answer.answer_text, re.IGNORECASE):
            problems.append("forbidden pattern present: " + pattern)
    return problems


async def evaluate(args) -> dict:
    settings = SettingsStore(ROOT / ".runtime")
    settings.value.api_fallback = False
    if args.provider:
        settings.value.provider = args.provider
    if args.lexical_only:
        settings.value.dense_enabled = False
    elif not settings.value.dense_enabled:
        raise ValueError("Install/configure embeddings first, or explicitly use --lexical-only")
    case_bytes = (FIXTURES / "cases.json").read_bytes()
    cases = [Case.model_validate_json(json.dumps(c)) for c in json.loads(case_bytes)]
    if args.case:
        known = {c.id for c in cases}
        if set(args.case) - known:
            raise ValueError("Unknown case ID")
        cases = [c for c in cases if c.id in args.case]
    data = fixture_package(FIXTURES / "knowledge")
    kb = make_kb(ROOT / ".runtime/evaluation", settings)
    started = time.monotonic()
    metadata = await asyncio.to_thread(kb.install, data)
    setup_seconds = time.monotonic() - started
    providers = Providers(settings)
    report = {
        "scope": "fictional development corpus; regex checks are not a semantic/factual guarantee",
        "provider": args.provider or "none: retrieval only",
        "encoder": metadata["encoder"],
        "corpus_sha256": hashlib.sha256(data).hexdigest(),
        "cases_sha256": hashlib.sha256(case_bytes).hexdigest(),
        "setup_seconds": round(setup_seconds, 3),
        "planned_cases": len(cases),
        "cases": [],
    }
    for case in cases:
        started = time.monotonic()
        evidence = await asyncio.to_thread(
            kb.search, case.question, [m.text for m in case.history if m.role == "user"]
        )
        row = {
            "id": case.id,
            "question": case.question,
            "retrieval_seconds": round(time.monotonic() - started, 3),
            "retrieved_ids": [e.id for e in evidence],
            "problems": [],
        }
        missing = set(case.source_ids) - {e.id for e in evidence}
        if missing:
            row["problems"].append("missing retrieval sources: " + ", ".join(sorted(missing)))
        exhausted = False
        if args.provider:
            started = time.monotonic()
            try:
                answer, provider = await providers.answer(case.question, case.history, evidence)
                row.update(answer=answer.model_dump(mode="json"), provider=provider)
                row["problems"].extend(answer_problems(case, answer))
            except (ProviderError, TimeoutError) as exc:
                code = exc.code if isinstance(exc, ProviderError) else "timeout"
                row["problems"].append("provider failure: " + code)
                exhausted = code in {"quota_exhausted", "auth_required"}
            row["provider_seconds"] = round(time.monotonic() - started, 3)
        row["passed"] = not row["problems"]
        report["cases"].append(row)
        print(
            f"{'PASS' if row['passed'] else 'FAIL'} {case.id}: {json.dumps(row['problems'], ensure_ascii=False)}",
            flush=True,
        )
        write_private(args.output, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        if exhausted:
            break
    report["passed"] = len(report["cases"]) == len(cases) and all(
        c["passed"] for c in report["cases"]
    )
    write_private(args.output, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["codex", "api"])
    parser.add_argument("--lexical-only", action="store_true")
    parser.add_argument("--case", action="append")
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime/evaluation/report.json")
    args = parser.parse_args()
    with RuntimeLease(ROOT / ".runtime"):
        report = asyncio.run(evaluate(args))
    print("Report:", args.output)
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
