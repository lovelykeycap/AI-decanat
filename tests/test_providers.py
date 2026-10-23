import asyncio

import pytest
from decanat.providers import CodexProvider, ProviderError, Providers, classify_error


async def test_cancellation_terminates_subprocess(controller, monkeypatch):
    terminated = []

    class Process:
        returncode = None

        async def communicate(self, prompt):
            await asyncio.sleep(30)

        def terminate(self):
            terminated.append(True)
            self.returncode = 1

        async def wait(self):
            return self.returncode

    captured = {}

    async def spawn(*args, **kwargs):
        captured["args"], captured["env"] = args, kwargs["env"]
        return Process()

    monkeypatch.setattr("decanat.providers.shutil.which", lambda name: "/bin/codex")
    monkeypatch.setattr("decanat.providers.asyncio.create_subprocess_exec", spawn)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-go-to-subscription-mode")
    adapter = CodexProvider(controller.settings)
    task = asyncio.create_task(adapter.generate("справка", [], []))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert terminated
    args = captured["args"]
    assert "--ignore-user-config" in args and "--ephemeral" in args
    assert "features.shell_tool=false" in args
    assert 'web_search="disabled"' in args
    assert "OPENAI_API_KEY" not in captured["env"]


async def test_invalid_answer_never_triggers_paid_fallback(controller):
    class Invalid:
        async def generate(self, *args):
            raise ProviderError("invalid_output", "bad", False)

    controller.settings.value.api_fallback = True
    api = controller.providers.adapters["api"]
    providers = Providers(controller.settings, codex=Invalid(), api=api)
    with pytest.raises(ProviderError):
        await providers.answer("справка", [], controller.kb.search("справка"))
    assert not api.calls


def test_temporary_request_rate_is_not_exhausted_subscription():
    assert classify_error("429 rate_limit_exceeded").code == "rate_limited"
    assert classify_error("429 insufficient_quota").code == "quota_exhausted"
    assert classify_error("You have reached your usage limit").code == "quota_exhausted"
