from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import platform
import secrets
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from decanat.config import RuntimeLease, SettingsStore, data_directory, write_private
from decanat.contracts import Document, GeneratedAnswer, Manifest
from decanat.journal import Journal
from decanat.knowledge import KnowledgeStore, SentenceEncoder, validate_package
from decanat.providers import Providers
from decanat.security import AdminAuth
from decanat.session import Conversation


def make_kb(root: Path, settings: SettingsStore) -> KnowledgeStore:
    value = settings.value
    encoder = (
        SentenceEncoder(value.embedding_model, value.embedding_revision)
        if value.dense_enabled
        else None
    )
    return KnowledgeStore(root, encoder)


async def chat(root: Path, settings: SettingsStore) -> None:
    controller = Conversation(settings, make_kb(root, settings), Providers(settings), Journal(root))
    queue = controller.subscribe()
    controller.start()
    controller.avatar_ready()
    print(
        "Текстовая диагностика backend. Аватар и речь этим режимом не проверяются. /exit — выход."
    )
    try:
        while controller.active:
            question = await asyncio.to_thread(input, "Вы: ")
            if question.strip() == "/exit":
                break
            await controller.submit(question)
            while True:
                event = await queue.get()
                if event.kind == "answer":
                    print("Помощник:", event.payload["answer"]["answer_text"])
                    print(
                        "Источники:", ", ".join(s["id"] for s in event.payload["sources"]) or "нет"
                    )
                    break
                if event.kind == "error":
                    print(event.payload["message"], file=sys.stderr)
                    break
    finally:
        await controller.finish()
        controller.avatar_ready()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="ИИ-аватар деканата: локальный backend")
    result.add_argument("--data-dir", type=Path, default=data_directory())
    commands = result.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("init", help="Создать настройки и пароль сотрудника")
    setup.add_argument("--generate-password", action="store_true")
    commands.add_parser("doctor", help="Проверить локальные компоненты без запросов к провайдерам")
    commands.add_parser("devices", help="Показать аудиоустройства без записи микрофона")
    serve = commands.add_parser("serve", help="Запустить локальный HTTP/WebSocket backend")
    serve.add_argument("--port", type=int, default=8765)
    commands.add_parser("chat", help="Текстовая диагностика с реальным выбранным провайдером")
    kb = commands.add_parser("kb", help="Работа с подготовленной базой")
    actions = kb.add_subparsers(dest="action", required=True)
    for name in ("validate", "import"):
        action = actions.add_parser(name)
        action.add_argument("package", type=Path)
        if name == "import":
            action.add_argument("--replace-kb", action="store_true")
    pack = actions.add_parser("pack")
    pack.add_argument("directory", type=Path)
    pack.add_argument("output", type=Path)
    search = actions.add_parser("search")
    search.add_argument("question")
    search.add_argument("--previous", action="append", default=[])
    schema = commands.add_parser("schemas", help="Экспортировать JSON Schema контракта")
    schema.add_argument("directory", type=Path)
    configure = commands.add_parser("configure", help="Изменить локальные настройки в даунтайме")
    configure.add_argument("key")
    configure.add_argument("json_value", help='JSON-значение, например true, 60 или "codex"')
    commands.add_parser("api-key", help="Сохранить API-ключ в защищённом хранилище ОС")
    return result


def main() -> None:
    args = parser().parse_args()
    root = args.data_dir.resolve()
    lease = None
    try:
        if args.command in {"configure", "init", "api-key", "chat"} or (
            args.command == "kb" and args.action == "import"
        ):
            lease = RuntimeLease(root)
            lease.__enter__()
        settings = SettingsStore(root)
        if args.command == "init":
            auth = AdminAuth(root)
            if auth.configured:
                raise ValueError("Пароль уже настроен; смените его через защищённые настройки")
            if args.generate_password:
                password = secrets.token_urlsafe(18)
                auth.set_password(password)
                write_private(root / "initial-password.txt", password + "\n")
                print(
                    f"Начальный пароль сохранён в {root / 'initial-password.txt'}. Удалите файл после входа."
                )
            else:
                password = getpass.getpass("Пароль сотрудника (не менее 10 символов): ")
                if password != getpass.getpass("Повторите пароль: "):
                    raise ValueError("Пароли не совпадают")
                auth.set_password(password)
            settings.save(settings.value)
            print("Локальные настройки созданы")
        elif args.command == "doctor":
            from decanat.speech import LocalSpeech

            binary = shutil.which(settings.value.codex_binary)
            print(
                json.dumps(
                    {
                        "platform": platform.platform(),
                        "architecture": platform.machine(),
                        "python": platform.python_version(),
                        "codex_found": bool(binary),
                        "api_key_configured": bool(settings.api_key()),
                        "api_model_configured": bool(settings.value.api_model),
                        "admin_configured": AdminAuth(root).configured,
                        "knowledge": make_kb(root, settings).metadata(),
                        "speech": LocalSpeech(settings).diagnostics(),
                        "dense_enabled": settings.value.dense_enabled,
                        "unreal_editor_found": any(
                            Path(p).exists()
                            for p in ("/Users/Shared/Epic Games", "C:/Program Files/Epic Games")
                        ),
                        "target_gpu_verified": False,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            if binary:
                subprocess.run([binary, "--version"], check=False)
        elif args.command == "devices":
            from decanat.audio_device import device_inventory

            print(json.dumps(device_inventory(), ensure_ascii=False, indent=2))
        elif args.command == "configure":
            value = {**settings.snapshot(), args.key: json.loads(args.json_value)}
            settings.save(type(settings.value).model_validate(value))
            print("Настройки сохранены. Команду используйте только при остановленном приложении.")
        elif args.command == "api-key":
            settings.set_api_key(getpass.getpass("API-ключ: ").strip())
            print("Ключ сохранён в защищённом хранилище ОС")
        elif args.command == "schemas":
            args.directory.mkdir(parents=True, exist_ok=True)
            for name, model in (
                ("manifest", Manifest),
                ("document", Document),
                ("answer", GeneratedAnswer),
            ):
                (args.directory / f"{name}.schema.json").write_text(
                    json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            print("JSON Schema экспортированы")
        elif args.command == "kb":
            kb = make_kb(root, settings)
            if args.action == "pack":
                args.output.parent.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    for name in ("manifest.json", "documents.jsonl"):
                        archive.write(args.directory / name, name)
                validate_package(args.output.read_bytes())
                print(f"Пакет проверен: {args.output}")
            elif args.action == "validate":
                manifest, documents = validate_package(args.package.read_bytes())
                print(
                    f"{manifest.title}, выпуск {manifest.release_id}, документов: {len(documents)}"
                )
            elif args.action == "import":
                print(
                    json.dumps(
                        kb.install(args.package.read_bytes(), replace_kb=args.replace_kb),
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            elif args.action == "search":
                print(
                    json.dumps(
                        [s.model_dump() for s in kb.search(args.question, args.previous)],
                        ensure_ascii=False,
                        indent=2,
                    )
                )
        elif args.command == "chat":
            asyncio.run(chat(root, settings))
        elif args.command == "serve":
            import uvicorn

            from decanat.server import create_app

            if not AdminAuth(root).configured:
                raise ValueError("Сначала выполните decanat init")
            token = secrets.token_urlsafe(32)
            connection_file = root / "desktop-connection.json"
            write_private(
                connection_file,
                json.dumps({"url": f"http://127.0.0.1:{args.port}", "token": token}),
            )
            print(f"Параметры локального подключения: {connection_file}")
            uvicorn.run(
                create_app(root, token),
                host="127.0.0.1",
                port=args.port,
                access_log=False,
                ws_max_size=4_010_000,
                log_level="warning",
            )
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        if lease:
            lease.__exit__(None, None, None)


if __name__ == "__main__":
    main()
