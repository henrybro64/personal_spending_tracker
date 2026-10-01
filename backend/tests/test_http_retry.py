import httpx
import pytest

from backend import ai_insights
from backend.http_retry import request_with_retry, retry_delay_seconds


class ScriptedSend:
    def __init__(self, results: list[object]) -> None:
        self.results = list(results)
        self.calls = 0

    async def __call__(self) -> httpx.Response:
        self.calls += 1
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item  # type: ignore[return-value]


async def _no_sleep(_delay: float) -> None:
    return None


def test_retry_delay_includes_jitter_and_honors_retry_after() -> None:
    low = retry_delay_seconds(
        attempt=1,
        base_delay=0.75,
        max_delay=5.0,
        random_value=0.0,
        retry_after=None,
    )
    high = retry_delay_seconds(
        attempt=1,
        base_delay=0.75,
        max_delay=5.0,
        random_value=1.0,
        retry_after=None,
    )
    assert low != high
    assert (
        retry_delay_seconds(
            attempt=2,
            base_delay=0.75,
            max_delay=5.0,
            random_value=0.0,
            retry_after=2.0,
        )
        == 2.0
    )


@pytest.mark.anyio
async def test_provider_retries_network_failure_then_succeeds() -> None:
    scripted = ScriptedSend(
        [httpx.ConnectError("connection reset"), httpx.Response(200)]
    )
    response = await request_with_retry(
        scripted,
        provider="OpenAI",
        sleep=_no_sleep,
        random_value=lambda: 0.0,
    )
    assert response.status_code == 200
    assert scripted.calls == 2


@pytest.mark.anyio
async def test_provider_retries_503_then_succeeds() -> None:
    scripted = ScriptedSend(
        [httpx.Response(503, text="unavailable"), httpx.Response(200)]
    )
    response = await request_with_retry(
        scripted,
        provider="Perplexity",
        sleep=_no_sleep,
        random_value=lambda: 0.0,
    )
    assert response.status_code == 200
    assert scripted.calls == 2


@pytest.mark.anyio
async def test_provider_honors_retry_after() -> None:
    delays: list[float] = []

    async def record(delay: float) -> None:
        delays.append(delay)

    scripted = ScriptedSend(
        [
            httpx.Response(429, headers={"Retry-After": "2"}, text="slow"),
            httpx.Response(200),
        ]
    )
    await request_with_retry(
        scripted,
        provider="OpenAI",
        sleep=record,
        random_value=lambda: 0.0,
    )
    assert delays == [2.0]


@pytest.mark.anyio
async def test_provider_does_not_retry_auth_or_request_errors() -> None:
    scripted = ScriptedSend([httpx.Response(401, text="bad key")])
    response = await request_with_retry(
        scripted,
        provider="OpenAI",
        sleep=_no_sleep,
        random_value=lambda: 0.0,
    )
    assert response.status_code == 401
    assert scripted.calls == 1


@pytest.mark.anyio
async def test_provider_stops_after_the_attempt_limit() -> None:
    scripted = ScriptedSend(
        [
            httpx.ConnectError("one"),
            httpx.ConnectError("two"),
            httpx.ConnectError("three"),
        ]
    )
    with pytest.raises(httpx.ConnectError):
        await request_with_retry(
            scripted,
            provider="OpenAI",
            sleep=_no_sleep,
            random_value=lambda: 0.0,
        )
    assert scripted.calls == 3


class _FakeClient:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *_args: object) -> bool:
        return False

    async def post(self, *_args: object, **_kwargs: object) -> httpx.Response:
        self.calls += 1
        return self.responses.pop(0)


@pytest.mark.anyio
async def test_openai_retries_a_transient_error_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(
        [
            httpx.Response(503, text="unavailable"),
            httpx.Response(200, json={"output_text": "Observations\n- saved"}),
        ]
    )
    monkeypatch.setattr("backend.http_retry.asyncio.sleep", _no_sleep)

    response = await ai_insights._post_openai(
        client,  # type: ignore[arg-type]
        api_key="test-key",
        model="gpt-test",
        system_prompt="system",
        user_prompt="user",
    )

    assert response.status_code == 200
    assert "saved" in response.json()["output_text"]
    assert client.calls == 2


@pytest.mark.anyio
async def test_openai_does_not_retry_an_auth_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient([httpx.Response(401, text="invalid api key")])
    monkeypatch.setattr("backend.http_retry.asyncio.sleep", _no_sleep)

    response = await ai_insights._post_openai(
        client,  # type: ignore[arg-type]
        api_key="test-key",
        model="gpt-test",
        system_prompt="system",
        user_prompt="user",
    )

    assert response.status_code == 401
    assert client.calls == 1


@pytest.mark.anyio
async def test_perplexity_retries_a_transient_error_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(
        [
            httpx.Response(504, text="timeout"),
            httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "Observations\n- kept"}}],
                    "citations": [],
                },
            ),
        ]
    )
    monkeypatch.setattr("backend.http_retry.asyncio.sleep", _no_sleep)

    response = await ai_insights._post_perplexity(
        client,  # type: ignore[arg-type]
        api_key="test-key",
        model="sonar",
        system_prompt="system",
        user_prompt="user",
        language="en",
    )

    assert response.status_code == 200
    assert "kept" in response.json()["choices"][0]["message"]["content"]
    assert client.calls == 2
