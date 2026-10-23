from datetime import UTC, datetime, timedelta

import pytest
from conftest import package
from decanat.contracts import AnswerStatus, GeneratedAnswer
from decanat.providers import ProviderError, validate_answer
from decanat.security import AdminAuth
from decanat.server import create_app
from fastapi.testclient import TestClient

TOKEN = "local-desktop-token-that-is-long-enough"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def test_hash_password_expiry_and_rate_limit(tmp_path):
    from conftest import Clock

    clock = Clock()
    auth = AdminAuth(tmp_path, clock)
    auth.set_password("long-test-password")
    assert "long-test-password" not in auth.path.read_text()
    token = auth.login("long-test-password")
    assert auth.check(token)
    clock.value += 1801
    assert not auth.check(token)
    for _ in range(5):
        assert auth.login("wrong") is None
    with pytest.raises(ValueError, match="пять"):
        auth.login("long-test-password")


def test_journal_retention_is_not_session_context(controller):
    old = datetime.now(UTC) - timedelta(days=31)
    controller.journal.record(
        turn_id="old",
        session_id="session",
        question="вопрос",
        answer="ответ",
        status="answer",
        provider="api",
        kb_id="kb",
        release_id="one",
        sources=[],
        created_at=old,
    )
    assert controller.journal.purge() == 1
    assert controller.journal.list() == []


def test_provider_cannot_invent_citations(kb):
    answer = GeneratedAnswer(
        status=AnswerStatus.ANSWER, answer_text="Утверждение", source_ids=["imaginary/source"]
    )
    with pytest.raises(ProviderError, match="источники"):
        validate_answer(answer, kb.search("справка"))


def test_provider_factual_answer_requires_citation(kb):
    answer = GeneratedAnswer(status=AnswerStatus.ANSWER, answer_text="Утверждение", source_ids=[])
    with pytest.raises(ProviderError):
        validate_answer(answer, kb.search("справка"))


def test_http_auth_admin_and_maintenance(controller, tmp_path):
    app = create_app(tmp_path, TOKEN, conversation=controller)
    app.state.auth.set_password("employee-password")
    with TestClient(app) as client:
        assert client.get("/v1/health").status_code == 401
        assert (
            client.get(
                "/v1/health", headers={**HEADERS, "Origin": "https://other.site"}
            ).status_code
            == 403
        )
        assert client.get("/v1/admin/journal", headers=HEADERS).status_code == 401
        response = client.post(
            "/v1/admin/login", headers=HEADERS, json={"password": "employee-password"}
        )
        admin = {**HEADERS, "X-Admin-Token": response.json()["admin_token"]}
        assert client.get("/v1/admin/journal", headers=admin).status_code == 200
        controller.start()
        assert (
            client.post("/v1/admin/knowledge", headers=admin, content=package()).status_code == 409
        )
        controller.history.clear()
        controller.session_id = None
        from decanat.contracts import State

        controller.state = State.IDLE
        assert (
            client.post("/v1/admin/knowledge", headers=admin, content=b"broken").status_code == 422
        )
        assert not controller.maintenance
        assert (
            client.post("/v1/admin/knowledge", headers=admin, content=package()).status_code == 200
        )
        assert client.post("/v1/admin/logout", headers=admin).status_code == 200
        assert client.get("/v1/admin/settings", headers=admin).status_code == 401


def test_websocket_session_protocol_and_clear(controller, tmp_path):
    app = create_app(tmp_path, TOKEN, conversation=controller)
    with (
        TestClient(app) as client,
        client.websocket_connect("/v1/conversation", headers=HEADERS) as ws,
    ):
        snapshot = ws.receive_json()
        assert snapshot["kind"] == "snapshot"
        ws.send_json({"type": "start"})
        standing = ws.receive_json()
        assert standing["payload"]["state"] == "standing_up"
        session = standing["session_id"]
        ws.receive_json()  # stand animation command
        ws.send_json({"type": "avatar_ready", "session_id": session})
        assert ws.receive_json()["payload"]["state"] == "listening"
        ws.send_json({"type": "text", "session_id": "old-student", "text": "справка"})
        assert ws.receive_json()["kind"] == "command_error"
        ws.send_json({"type": "finish", "session_id": session})
        events = [ws.receive_json() for _ in range(4)]
        assert any(e["kind"] == "cleared" for e in events)


def test_pending_audio_configuration_blocks_new_session_until_restart(controller, tmp_path):
    app = create_app(tmp_path, TOKEN, conversation=controller)
    app.state.auth.set_password("employee-password")
    with TestClient(app) as client:
        token = client.post(
            "/v1/admin/login", headers=HEADERS, json={"password": "employee-password"}
        ).json()["admin_token"]
        admin = {**HEADERS, "X-Admin-Token": token}
        body = client.get("/v1/admin/settings", headers=admin).json()
        body["input_device"] = "new microphone"
        response = client.put("/v1/admin/settings", headers=admin, json=body)
        assert response.status_code == 200
        assert response.json()["restart_required"]
        assert client.get("/v1/health", headers=HEADERS).json()["restart_required"]
        with client.websocket_connect("/v1/conversation", headers=HEADERS) as ws:
            ws.receive_json()
            ws.send_json({"type": "start"})
            assert "перезапустить" in ws.receive_json()["payload"]["message"]
            assert controller.session_id is None
        # Saving another setting must not clear an outstanding restart requirement.
        body["provider"] = "api"
        assert client.put("/v1/admin/settings", headers=admin, json=body).json()["restart_required"]


def test_diagnostics_requires_staff_and_omits_secrets(controller, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "decanat.audio_device.device_inventory", lambda: {"available": True, "devices": []}
    )
    monkeypatch.setattr(controller.settings, "api_key", lambda: "not-for-diagnostics")
    app = create_app(tmp_path, TOKEN, conversation=controller)
    app.state.auth.set_password("employee-password")
    with TestClient(app) as client:
        assert client.get("/v1/admin/diagnostics", headers=HEADERS).status_code == 401
        token = client.post(
            "/v1/admin/login", headers=HEADERS, json={"password": "employee-password"}
        ).json()["admin_token"]
        response = client.get("/v1/admin/diagnostics", headers={**HEADERS, "X-Admin-Token": token})
        assert response.json()["api_key_configured"]
        assert "not-for-diagnostics" not in response.text


def test_login_rate_limit_survives_concurrent_http_workers(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    auth = AdminAuth(tmp_path)
    auth.set_password("employee-password")

    def attempt(_):
        try:
            assert auth.login("wrong-password") is None
            return "rejected"
        except ValueError:
            return "limited"

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(attempt, range(12)))
    assert results.count("rejected") == 5
    assert results.count("limited") == 7


async def test_cancelled_upload_keeps_maintenance_until_mutating_thread_finishes(
    controller, tmp_path, monkeypatch
):
    import asyncio
    import threading
    from types import SimpleNamespace

    entered, release = threading.Event(), threading.Event()
    original = controller.kb.install

    def slow_install(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    async def stream():
        yield package()

    monkeypatch.setattr(controller.kb, "install", slow_install)
    queue = controller.subscribe()
    app = create_app(tmp_path, TOKEN, conversation=controller)
    endpoint = next(
        r.endpoint for r in app.routes if r.path == "/v1/admin/knowledge" and "POST" in r.methods
    )
    request = asyncio.create_task(endpoint(SimpleNamespace(stream=stream)))
    assert await asyncio.to_thread(entered.wait, 1)
    request.cancel()
    await asyncio.sleep(0)
    assert controller.maintenance
    with pytest.raises(ValueError, match="обслуживании"):
        controller.start()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert not controller.maintenance
    assert controller.kb.search("справка")
    events = []
    while not queue.empty():
        events.append(queue.get_nowait().kind)
    assert "knowledge_updated" in events
    controller.unsubscribe(queue)
