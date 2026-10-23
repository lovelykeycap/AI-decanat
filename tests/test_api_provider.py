import httpx
import pytest
from decanat.providers import APIProvider, ProviderError
from openai import AsyncOpenAI


async def test_real_sdk_parses_schema_output_with_mock_http(controller, monkeypatch):
    evidence = controller.kb.search("справка")
    source_id = evidence[0].id

    def response(request):
        import json

        body = json.loads(request.content)
        assert body["store"] is False
        assert body["text"]["format"]["type"] == "json_schema"
        assert "tools" not in body
        answer = json.dumps(
            {"status": "answer", "answer_text": "Три рабочих дня.", "source_ids": [source_id]}
        )
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "object": "response",
                "created_at": 1,
                "model": "test-model",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "id": "msg_test",
                        "status": "completed",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": answer, "annotations": []}],
                    }
                ],
            },
        )

    monkeypatch.setattr(controller.settings, "api_key", lambda: "not-a-real-key")
    controller.settings.value.api_model = "test-model"

    def client(**kwargs):
        return AsyncOpenAI(
            **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(response))
        )

    monkeypatch.setattr("openai.AsyncOpenAI", client)
    answer = await APIProvider(controller.settings).generate("Срок справки?", [], evidence)
    assert answer.answer_text == "Три рабочих дня."
    assert answer.source_ids == [source_id]


async def test_api_cannot_start_without_configured_key(controller, monkeypatch):
    monkeypatch.setattr(controller.settings, "api_key", lambda: None)
    with pytest.raises(ProviderError, match="не настроены"):
        await APIProvider(controller.settings).generate("вопрос", [], [])
