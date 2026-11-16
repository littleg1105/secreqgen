"""Tests of tuning_lib.openrouter_client. A fake transport is used: no network."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import types
from pathlib import Path

import httpx
import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))
sys.path.insert(0, str(_ROOT / "src"))

try:  # the guard is written by another builder; use a stand-in only if it is absent
    import tuning_lib.guard  # noqa: F401
except ImportError:  # pragma: no cover
    _stub = types.ModuleType("tuning_lib.guard")
    _stub.assert_no_private_text = lambda text, **kw: None  # type: ignore[attr-defined]
    sys.modules["tuning_lib.guard"] = _stub

from tuning_lib import openrouter_client as oc  # noqa: E402

SECRET = "sk-or-v1-TESTSECRET-0123456789"
GEMMA = oc.HOSTED_MODELS["gemma4:26b"]
QWEN = oc.HOSTED_MODELS["qwen3.8:27b"]


def good_body(pin: oc.ModelPin = GEMMA, **over) -> dict:
    body = {
        "id": "gen-abc",
        "model": pin.model_id + "-20260101",
        "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "hi"}}],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "cost": 0.0012,
            "completion_tokens_details": {"reasoning_tokens": 0},
            "prompt_tokens_details": {"cached_tokens": 7},
        },
        "openrouter_metadata": {
            "endpoints": {
                "available": [
                    {"provider": "Other", "selected": False},
                    {"provider": pin.expected_provider_name, "selected": True},
                ]
            },
            "attempts": [{"provider": pin.expected_provider_name}],
        },
    }
    body.update(over)
    return body


class Fake:
    """Fake transport: gives queued responses and keeps the requests."""

    def __init__(self, *responses) -> None:
        self.queue = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make(fake: Fake, pin: oc.ModelPin = GEMMA, sleeps: list | None = None, **kw):
    sleeps = sleeps if sleeps is not None else []
    return oc.OpenRouterClient(
        pin,
        oc.ApiKey(SECRET),
        transport=httpx.MockTransport(fake),
        sleep=sleeps.append,
        **kw,
    )


def ok(body: dict | None = None, status: int = 200, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(status, json=body if body is not None else good_body(), headers=headers)


# -- request -----------------------------------------------------------------


def test_request_body_exact_keys_and_values_gemma():
    body = oc.build_request_body(GEMMA, "sys", "user")
    assert set(body) == {
        "model", "messages", "stream", "temperature", "top_p", "top_k",
        "max_completion_tokens", "reasoning", "provider", "plugins",
    }
    assert body["model"] == "google/gemma-4-26b-a4b-it"
    assert body["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "user"},
    ]
    assert body["stream"] is False
    assert body["temperature"] == 0.1
    assert body["top_p"] == 0.95
    assert body["top_k"] == 64
    assert body["max_completion_tokens"] == 4096
    assert body["reasoning"] == {"effort": "none"}
    assert body["provider"] == {
        "order": ["parasail/bf16"], "only": ["parasail/bf16"], "allow_fallbacks": False,
        "require_parameters": True, "data_collection": "deny",
    }
    assert body["plugins"] == [{"id": "context-compression", "enabled": False}]
    for forbidden in ("response_format", "user", "session_id", "metadata", "trace",
                      "models", "seed", "transforms", "route"):
        assert forbidden not in body


def test_request_body_qwen_extra_sampling():
    body = oc.build_request_body(QWEN, None, "user")
    assert body["top_k"] == 20
    assert body["min_p"] == 0.0
    assert body["presence_penalty"] == 0.0
    assert body["repetition_penalty"] == 1.0
    assert body["messages"] == [{"role": "user", "content": "user"}]
    assert body["provider"]["order"] == ["deepinfra/bf16"]


def test_empty_system_prompt_left_out():
    body = oc.build_request_body(GEMMA, "", "u")
    assert [m["role"] for m in body["messages"]] == ["user"]


def test_extra_sampling_clash_refused():
    pin = oc.ModelPin(**{**GEMMA.__dict__, "extra_sampling": {"temperature": 1.0}})
    with pytest.raises(oc.FatalHostedError):
        oc.build_request_body(pin, None, "u")


def test_headers_exact():
    h = oc.build_headers(oc.ApiKey(SECRET))
    assert h == {
        "Authorization": f"Bearer {SECRET}",
        "Content-Type": "application/json",
        "X-OpenRouter-Metadata": "enabled",
        "X-OpenRouter-Cache": "false",
    }
    lowered = {k.lower() for k in h}
    assert not lowered & {"http-referer", "referer", "x-title", "x-openrouter-title",
                          "x-openrouter-categories", "user-agent"}


def test_sent_request_matches_design():
    fake = Fake(ok())
    client = make(fake)
    client.generate_reply("hello", "be brief")
    req = fake.requests[0]
    assert str(req.url) == oc.API_URL
    assert req.method == "POST"
    sent = json.loads(req.content)
    assert sent == oc.build_request_body(GEMMA, "be brief", "hello")
    assert req.headers["authorization"] == f"Bearer {SECRET}"
    assert "referer" not in req.headers
    assert "x-title" not in req.headers


# -- success -----------------------------------------------------------------


def test_reply_fields_usage_and_cost():
    client = make(Fake(ok()))
    r = client.generate_reply("hello")
    assert r.text == "hi"
    assert r.generation_id == "gen-abc"
    assert r.provider == "Parasail"
    assert r.provider_source == "metadata"
    assert r.finish_reason == "stop"
    assert (r.prompt_tokens, r.completion_tokens, r.cached_tokens) == (100, 20, 7)
    assert r.reasoning_tokens == 0 and r.reasoning_tokens_source == "usage"
    assert r.cost_usd == pytest.approx(0.0012)
    assert r.transport_retries == 0
    assert r.latency_ms >= 0
    assert client.last_reply is r
    d = r.to_dict()
    json.dumps(d)
    assert SECRET not in json.dumps(d)


def test_generate_old_surface():
    client = make(Fake(ok()))
    resp = client.generate("hello", "sys", format="json")
    assert resp.content == "hi"
    assert resp.total_tokens == 120
    assert resp.model.startswith(GEMMA.model_id)
    assert resp.raw_response["generation_id"] == "gen-abc"
    assert client.model_name == GEMMA.model_id


def test_generate_structured_output_not_implemented():
    client = make(Fake(), structured_output=True)
    with pytest.raises(NotImplementedError):
        client.generate("x")


def test_none_content_becomes_empty_string():
    body = good_body()
    body["choices"][0]["message"]["content"] = None
    assert make(Fake(ok(body))).generate_reply("x").text == ""


def test_finish_reason_length_is_stored_not_raised():
    body = good_body()
    body["choices"][0]["finish_reason"] = "length"
    assert make(Fake(ok(body))).generate_reply("x").finish_reason == "length"


def test_reasoning_field_absent_assumed_zero():
    body = good_body()
    del body["usage"]["completion_tokens_details"]
    r = make(Fake(ok(body))).generate_reply("x")
    assert r.reasoning_tokens == 0
    assert r.reasoning_tokens_source == "absent_assumed_zero"


# -- reasoning refused -------------------------------------------------------


def test_reasoning_tokens_refused_with_partial_reply():
    body = good_body()
    body["usage"]["completion_tokens_details"]["reasoning_tokens"] = 55
    client = make(Fake(ok(body)))
    with pytest.raises(oc.ContractViolation) as ei:
        client.generate_reply("x")
    assert ei.value.reply is not None
    assert ei.value.reply.cost_usd == pytest.approx(0.0012)
    assert ei.value.reply.reasoning_tokens == 55
    assert client.last_reply is None


def test_reasoning_text_refused_even_with_absent_count():
    body = good_body()
    del body["usage"]["completion_tokens_details"]
    body["choices"][0]["message"]["reasoning"] = "thinking..."
    with pytest.raises(oc.ContractViolation):
        make(Fake(ok(body))).generate_reply("x")


def test_think_tags_in_content_refused_with_zero_reasoning_tokens():
    body = good_body()
    body["choices"][0]["message"]["content"] = '<think>x</think>{"requirements": []}'
    with pytest.raises(oc.ContractViolation) as ei:
        make(Fake(ok(body))).generate_reply("x")
    assert ei.value.reply is not None


def test_cache_hit_header_refused():
    fake = Fake(ok(headers={"X-OpenRouter-Cache-Status": "hit"}))
    with pytest.raises(oc.ContractViolation, match="cached reply"):
        make(fake).generate_reply("x")


def test_zero_prompt_tokens_refused():
    body = good_body()
    body["usage"]["prompt_tokens"] = 0
    with pytest.raises(oc.ContractViolation, match="prompt_tokens 0"):
        make(Fake(ok(body))).generate_reply("x")


# -- retries -----------------------------------------------------------------


def test_retry_after_429_uses_retry_after_header():
    fake = Fake(httpx.Response(429, headers={"Retry-After": "5"}, json={}), ok())
    sleeps: list[float] = []
    r = make(fake, sleeps=sleeps).generate_reply("x")
    assert sleeps == [5.0]
    assert r.transport_retries == 1
    assert len(fake.requests) == 2


def test_retry_after_capped_at_60():
    fake = Fake(httpx.Response(429, headers={"Retry-After": "999"}, json={}), ok())
    sleeps: list[float] = []
    make(fake, sleeps=sleeps).generate_reply("x")
    assert sleeps == [60.0]


def test_retry_after_500_uses_backoff():
    fake = Fake(httpx.Response(500, json={}), httpx.Response(502, json={}), ok())
    sleeps: list[float] = []
    r = make(fake, sleeps=sleeps).generate_reply("x")
    assert sleeps == [2.0, 4.0]
    assert r.transport_retries == 2


def test_retries_exhausted_raises_transient():
    fake = Fake(*[httpx.Response(503, json={}) for _ in range(4)])
    sleeps: list[float] = []
    with pytest.raises(oc.TransientHostedError) as ei:
        make(fake, sleeps=sleeps).generate_reply("x")
    assert ei.value.status == 503
    assert sleeps == [2.0, 4.0, 8.0]
    assert len(fake.requests) == 4


def test_timeout_retried_then_transient():
    fake = Fake(*[httpx.ReadTimeout("slow") for _ in range(2)], ok())
    sleeps: list[float] = []
    assert make(fake, sleeps=sleeps).generate_reply("x").transport_retries == 2
    fake2 = Fake(*[httpx.ReadTimeout("slow") for _ in range(4)])
    with pytest.raises(oc.TransientHostedError):
        make(fake2).generate_reply("x")


def test_connect_error_is_transient_without_retry():
    fake = Fake(httpx.ConnectError("no route"))
    with pytest.raises(oc.TransientHostedError):
        make(fake).generate_reply("x")
    assert len(fake.requests) == 1


@pytest.mark.parametrize(
    "status,fragment",
    [(400, "bad param"), (401, "refused"), (403, "refused"), (402, "credit"),
     (404, "no endpoint matches"), (418, "HTTP 418")],
)
def test_fatal_statuses(status, fragment):
    fake = Fake(httpx.Response(status, json={"error": {"message": "bad param"}}))
    with pytest.raises(oc.FatalHostedError) as ei:
        make(fake).generate_reply("x")
    assert fragment in str(ei.value)
    assert ei.value.status == status
    assert len(fake.requests) == 1


def test_server_message_cut_and_key_scrubbed():
    msg = "x" * 900 + SECRET
    fake = Fake(httpx.Response(400, json={"error": {"message": msg + SECRET}}))
    with pytest.raises(oc.FatalHostedError) as ei:
        make(fake).generate_reply("x")
    assert len(str(ei.value)) < 600
    assert SECRET not in str(ei.value)
    fake = Fake(httpx.Response(400, json={"error": {"message": "echo " + SECRET}}))
    with pytest.raises(oc.FatalHostedError) as ei:
        make(fake).generate_reply("x")
    assert SECRET not in str(ei.value)


# -- errors inside HTTP 200 --------------------------------------------------


def test_error_in_200_with_retryable_code_is_retried():
    fake = Fake(ok({"error": {"code": 429, "message": "slow down"}}), ok())
    sleeps: list[float] = []
    r = make(fake, sleeps=sleeps).generate_reply("x")
    assert r.transport_retries == 1 and sleeps == [2.0]


def test_error_in_200_with_other_code_is_fatal():
    fake = Fake(ok({"error": {"code": 400, "message": "moderation"}}))
    with pytest.raises(oc.FatalHostedError, match="moderation"):
        make(fake).generate_reply("x")


def _choice_error(err: dict) -> dict:
    body = good_body()
    body["choices"] = [{"error": err}]
    return body


def _choice_timeout(code=504) -> dict:
    return _choice_error(
        {
            "code": code,
            "message": "Upstream idle timeout exceeded",
            "metadata": {"error_type": "timeout"},
        }
    )


def test_choice_error_timeout_in_200_is_retried():
    fake = Fake(ok(_choice_timeout()), ok())
    sleeps: list[float] = []
    r = make(fake, sleeps=sleeps).generate_reply("x")
    assert r.text == "hi" and len(fake.requests) == 2
    assert r.transport_retries == 1 and sleeps == [2.0]
    assert fake.requests[0].content == fake.requests[1].content


def test_top_level_error_timeout_in_200_is_retried():
    err = {"code": 504, "message": "t", "metadata": {"error_type": "timeout"}}
    fake = Fake(ok({"error": err}), ok())
    r = make(fake).generate_reply("x")
    assert r.text == "hi" and len(fake.requests) == 2


def test_choice_error_every_call_ends_transient_not_fatal():
    fake = Fake(*[ok(_choice_timeout()) for _ in range(4)])
    sleeps: list[float] = []
    with pytest.raises(oc.TransientHostedError) as ei:
        make(fake, sleeps=sleeps).generate_reply("x")
    assert not isinstance(ei.value, oc.FatalHostedError)
    assert sleeps == [2.0, 4.0, 8.0] and len(fake.requests) == 4


def test_choice_error_code_400_is_fatal_at_first_call():
    fake = Fake(ok(_choice_error({"code": 400, "message": "bad"})))
    with pytest.raises(oc.FatalHostedError):
        make(fake).generate_reply("x")
    assert len(fake.requests) == 1


@pytest.mark.parametrize("code", ["504", "429", 408, "408"])
def test_choice_error_code_as_string_or_408_is_retried(code):
    fake = Fake(ok(_choice_error({"code": code, "message": "x"})), ok())
    assert make(fake).generate_reply("x").text == "hi"
    assert len(fake.requests) == 2


@pytest.mark.parametrize(
    "err", [{"message": "no code"}, {"code": 401, "message": "k"}, {"code": 402}, {"code": 403}]
)
def test_other_errors_in_200_stay_fatal(err):
    with pytest.raises(oc.FatalHostedError):
        make(Fake(ok(_choice_error(err)))).generate_reply("x")


def test_200_not_json_is_fatal():
    fake = Fake(httpx.Response(200, content=b"<html>"))
    with pytest.raises(oc.FatalHostedError):
        make(fake).generate_reply("x")


def test_missing_choices_and_choice_error_are_fatal():
    with pytest.raises(oc.FatalHostedError):
        make(Fake(ok(good_body(choices=[])))).generate_reply("x")
    body = good_body()
    body["choices"][0]["error"] = {"message": "boom"}
    with pytest.raises(oc.FatalHostedError):
        make(Fake(ok(body))).generate_reply("x")


def test_missing_or_bad_cost_and_tokens_are_fatal():
    for mutate in (
        lambda u: u.pop("cost"),
        lambda u: u.__setitem__("cost", "0.1"),
        lambda u: u.pop("prompt_tokens"),
        lambda u: u.__setitem__("completion_tokens", 1.5),
    ):
        body = good_body()
        mutate(body["usage"])
        with pytest.raises(oc.FatalHostedError):
            make(Fake(ok(body))).generate_reply("x")


# -- provider checks ---------------------------------------------------------


def test_wrong_provider_refused_with_reply():
    body = good_body()
    body["openrouter_metadata"]["endpoints"]["available"] = [{"provider": "Evil", "selected": True}]
    with pytest.raises(oc.ContractViolation) as ei:
        make(Fake(ok(body))).generate_reply("x")
    assert ei.value.reply is not None and ei.value.reply.provider == "Evil"


def test_provider_compare_ignores_case_and_spaces():
    body = good_body()
    body["openrouter_metadata"]["endpoints"]["available"] = [{"provider": "para sail", "selected": True}]
    assert make(Fake(ok(body))).generate_reply("x").provider == "para sail"


def test_wrong_model_refused():
    with pytest.raises(oc.ContractViolation):
        make(Fake(ok(good_body(model="other/model")))).generate_reply("x")


def test_two_attempts_refused():
    body = good_body()
    body["openrouter_metadata"]["attempts"] = [{}, {}]
    with pytest.raises(oc.ContractViolation):
        make(Fake(ok(body))).generate_reply("x")


def test_provider_from_generation_endpoint():
    body = good_body()
    del body["openrouter_metadata"]
    gen = httpx.Response(200, json={"data": {"provider_name": "Parasail"}})
    fake = Fake(ok(body), gen)
    sleeps: list[float] = []
    r = make(fake, sleeps=sleeps).generate_reply("x")
    assert r.provider == "Parasail" and r.provider_source == "generation_endpoint"
    req = fake.requests[1]
    assert req.method == "GET"
    assert dict(req.url.params) == {"id": "gen-abc"}
    assert req.content == b""
    assert sleeps == [1.0]


def test_generation_endpoint_retries_then_fatal():
    body = good_body()
    del body["openrouter_metadata"]
    fake = Fake(ok(body), *[httpx.Response(404, json={}) for _ in range(3)])
    sleeps: list[float] = []
    with pytest.raises(oc.FatalHostedError, match="provider cannot be read"):
        make(fake, sleeps=sleeps).generate_reply("x")
    assert sleeps == [1.0, 2.0, 4.0]


# -- the key -----------------------------------------------------------------


def test_key_never_shown():
    key = oc.ApiKey(SECRET)
    assert SECRET not in repr(key) and SECRET not in str(key)
    assert repr(key) == "ApiKey(***)"
    client = make(Fake())
    assert SECRET not in repr(client) and SECRET not in str(client)


def test_key_not_in_exception_or_log(caplog):
    caplog.set_level(logging.DEBUG)
    fake = Fake(httpx.Response(500, json={}), httpx.Response(401, json={"error": {"message": "no"}}))
    with pytest.raises(oc.FatalHostedError) as ei:
        make(fake).generate_reply("x")
    assert SECRET not in str(ei.value) and SECRET not in repr(ei.value)
    assert SECRET not in caplog.text
    fake = Fake(httpx.ConnectError("fail " + SECRET))
    with pytest.raises(oc.TransientHostedError) as ei2:
        make(fake).generate_reply("x")
    assert SECRET not in str(ei2.value)


def test_load_api_key_from_environment_without_calling_run():
    def boom(*a, **k):
        raise AssertionError("run must not be called")

    key = oc.load_api_key(environ={"OPENROUTER_API_KEY": f"  {SECRET} "}, run=boom)
    assert key.reveal() == SECRET


def test_load_api_key_from_keychain_mock():
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, stdout=SECRET + "\n", stderr="")

    key = oc.load_api_key(environ={}, run=fake_run, account="someone")
    assert key.reveal() == SECRET
    cmd, kw = calls[0]
    assert cmd == ["/usr/bin/security", "find-generic-password", "-s", "openrouter-api-key",
                   "-a", "someone", "-w"]
    assert kw == {"capture_output": True, "text": True, "timeout": 20}


@pytest.mark.parametrize("mode", ["rc", "empty", "timeout", "missing"])
def test_load_api_key_failures_hold_no_secret(mode):
    def fake_run(cmd, **kw):
        if mode == "timeout":
            raise subprocess.TimeoutExpired(cmd, 20)
        if mode == "missing":
            raise FileNotFoundError(SECRET)
        if mode == "rc":
            return subprocess.CompletedProcess(cmd, 44, stdout=SECRET, stderr=SECRET)
        return subprocess.CompletedProcess(cmd, 0, stdout="  \n", stderr="")

    with pytest.raises(oc.FatalHostedError) as ei:
        oc.load_api_key(environ={}, run=fake_run, account="someone")
    assert "key was not found" in str(ei.value)
    assert "add-generic-password" in str(ei.value)
    assert SECRET not in str(ei.value)


def test_import_has_no_side_effect():
    # The module-level pins are plain data and the constants are the design values.
    assert oc.KEYCHAIN_SERVICE == "openrouter-api-key"
    assert oc.API_URL.startswith("https://openrouter.ai/")
