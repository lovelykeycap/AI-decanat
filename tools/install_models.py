"""Fetch local speech/search models; pin upstream revisions and record content hashes.

Run only in downtime. Nothing is downloaded by the backend in a student conversation.
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

from decanat.config import RuntimeLease, SettingsStore, write_private
from decanat.speech import file_sha256

ROOT = Path(__file__).resolve().parents[1]


def verify(path: Path, expected: str) -> str:
    actual = file_sha256(path)
    if actual != expected:
        raise ValueError(f"Неверная SHA-256 модели {path.name}: файл не будет подключён")
    return actual


def download(url: str, path: Path, expected: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        temporary = path.with_suffix(path.suffix + ".partial")
        try:
            with (
                urllib.request.urlopen(url, timeout=120) as response,
                temporary.open("wb") as output,
            ):
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            verify(temporary, expected)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return {"path": str(path), "url": url, "sha256": verify(path, expected)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--speech", action="store_true")
    parser.add_argument("--embedding", action="store_true")
    parser.add_argument("--whisper-size", choices=["base", "small", "medium"], default="small")
    parser.add_argument("--configure", action="store_true")
    args = parser.parse_args()
    if not args.speech and not args.embedding:
        parser.error("Specify --speech and/or --embedding")
    # The same lease also prevents two installers overwriting each other's manifest.
    try:
        with RuntimeLease(ROOT / ".runtime"):
            install(args)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"{exc}\n")


def install(args):
    sources = json.loads((Path(__file__).parent / "model-sources.json").read_text())
    model_root = ROOT / "models"
    settings = SettingsStore(ROOT / ".runtime")
    record_path = model_root / "installed.json"
    record = json.loads(record_path.read_text()) if record_path.exists() else {}
    if args.speech:
        revision = sources["whisper"]["revision"]
        whisper = download(
            f"https://huggingface.co/ggerganov/whisper.cpp/resolve/{revision}/ggml-{args.whisper_size}.bin",
            model_root / f"ggml-{args.whisper_size}.bin",
            sources["whisper"]["sha256"][args.whisper_size],
        )
        whisper["revision"] = revision
        record["whisper"] = whisper
        print("Whisper downloaded and hashed", flush=True)
        record["vad"] = download(
            sources["vad"]["url"],
            model_root / "silero_vad.onnx",
            sources["vad"]["sha256"],
        )
        record["vad"]["revision"] = sources["vad"]["revision"]
        print("Silero VAD downloaded and hashed", flush=True)
        record["tts"] = download(
            sources["tts"]["url"],
            model_root / "silero_v5_ru.pt",
            sources["tts"]["sha256"],
        )
        record["tts"]["license_note"] = sources["tts"]["license_note"]
        print("Silero TTS downloaded and hashed", flush=True)
        if args.configure:
            settings.value.whisper_model_path = whisper["path"]
            settings.value.vad_model_path = record["vad"]["path"]
            settings.value.silero_model_path = record["tts"]["path"]
    if args.embedding:
        from huggingface_hub import snapshot_download

        revision = sources["embedding"]["revision"]
        directory = model_root / "bge-m3"
        snapshot_download(
            sources["embedding"]["repository"],
            revision=revision,
            local_dir=directory,
            allow_patterns=list(sources["embedding"]["files"]),
        )
        if not any(
            (directory / name).is_file() for name in ("pytorch_model.bin", "model.safetensors")
        ):
            raise RuntimeError("Embedding model weights were not downloaded")
        files = {
            name: verify(directory / name, expected)
            for name, expected in sources["embedding"]["files"].items()
        }
        record["embedding"] = {
            "path": str(directory),
            "revision": revision,
            "files": files,
            "model": "BAAI/bge-m3",
        }
        if args.configure:
            settings.value.embedding_model = str(directory)
            settings.value.embedding_revision = revision
            settings.value.dense_enabled = True
        print("BGE-M3 downloaded and hashed", flush=True)
    model_root.mkdir(parents=True, exist_ok=True)
    write_private(record_path, json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    if args.configure:
        settings.save(settings.value)
    print("Recorded model hashes in", record_path)


if __name__ == "__main__":
    main()
