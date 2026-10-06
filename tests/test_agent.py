import importlib.util
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bizdata.agent import run_agent, tool_specs
from bizdata.store import Store


def text(t):
    return SimpleNamespace(type="text", text=t)


def tool_use(id, name, input):
    return SimpleNamespace(type="tool_use", id=id, name=name, input=input)


def response(stop_reason, *content):
    return SimpleNamespace(stop_reason=stop_reason, content=list(content))


class FakeClient:
    """Plays back canned responses and records what was sent."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        # Copy the message list: the agent keeps appending to the same list object.
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


def test_tool_loop_runs_tools_and_returns_answer(store, audit_path):
    first = response(
        "tool_use",
        text("Checking sales by category."),
        tool_use("tu_1", "sales_summary", {"group_by": "category"}),
        tool_use("tu_2", "refund_rate", {"by": "category"}),
    )
    client = FakeClient(first, response("end_turn", text("Hiking leads with $290 net.")))
    seen = []

    answer = run_agent("Which category sells best?", store, client, on_tool_call=lambda *a: seen.append(a))

    assert answer.text == "Hiking leads with $290 net."
    assert answer.stop_reason == "end_turn"
    assert [c["name"] for c in answer.tool_calls] == ["sales_summary", "refund_rate"]
    assert seen == [("sales_summary", {"group_by": "category"}, False), ("refund_rate", {"by": "category"}, False)]

    req = client.requests[0]
    assert req["model"] == "claude-opus-5-5"
    assert req["fallbacks"] == "default"
    assert req["betas"] == ["server-side-fallback-2026-07-01"]
    assert "2026-01-10 to 2026-03-01" in req["system"]
    assert {t["name"] for t in req["tools"]} >= {"run_sql", "sales_summary"}

    followup = client.requests[1]["messages"]
    assert followup[1] == {"role": "assistant", "content": first.content}
    results = followup[2]["content"]
    assert [r["tool_use_id"] for r in results] == ["tu_1", "tu_2"]
    assert json.loads(results[0]["content"])["totals"]["net_revenue"] == 585
    assert "is_error" not in results[0]

    logged = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert [e["tool"] for e in logged] == ["sales_summary", "refund_rate"]


def test_bad_tool_input_goes_back_as_error(store):
    client = FakeClient(
        response("tool_use", tool_use("tu_1", "run_sql", {"query": "DELETE FROM orders"})),
        response("tool_use", tool_use("tu_2", "drop_everything", {})),
        response("end_turn", text("I can only read data.")),
    )
    answer = run_agent("Delete the orders", store, client)

    first_result = client.requests[1]["messages"][-1]["content"][0]
    assert first_result["is_error"] is True
    assert "only SELECT" in first_result["content"]
    second_result = client.requests[2]["messages"][-1]["content"][0]
    assert second_result["is_error"] is True
    assert answer.text == "I can only read data."
    assert store.run_sql("SELECT COUNT(*) FROM orders")["rows"] == [[5]]


def test_refusal_is_reported(store):
    answer = run_agent("anything", store, FakeClient(response("refusal")))
    assert answer.stop_reason == "refusal"


def test_gives_up_after_max_turns(store):
    looping = [response("tool_use", tool_use(f"tu_{i}", "inventory_alerts", {})) for i in range(3)]
    answer = run_agent("loop", store, FakeClient(*looping), max_turns=3)
    assert answer.stop_reason == "max_turns"
    assert len(answer.tool_calls) == 3


def test_other_models_skip_fallbacks_and_effort(store):
    client = FakeClient(response("end_turn", text("ok")))
    run_agent("hi", store, client, model="claude-haiku-4-5", effort="")
    assert "fallbacks" not in client.requests[0]
    assert "output_config" not in client.requests[0]


def test_every_tool_maps_to_a_store_method(store):
    for spec in tool_specs(store):
        method = getattr(Store, spec["name"])
        params = set(inspect.signature(method).parameters) - {"self"}
        assert params == set(spec["input_schema"]["properties"]), spec["name"]


def test_ask_without_key_explains_setup(monkeypatch, capsys, tmp_path):
    spec = importlib.util.spec_from_file_location("ask", Path(__file__).parent.parent / "ask.py")
    ask = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ask)
    monkeypatch.setattr(ask, "ENV_FILE", tmp_path / "missing.env")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert ask.main(["what sold best?"]) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_request_shape_with_real_sdk(store):
    """Runs the loop through the actual anthropic client against a mocked HTTP endpoint."""
    anthropic = pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")

    def message(id, stop_reason, content):
        return {"id": id, "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": content,
                "stop_reason": stop_reason, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}

    replies = [
        message("msg_1", "tool_use", [
            {"type": "text", "text": "Checking."},
            {"type": "tool_use", "id": "toolu_1", "name": "top_customers", "input": {"n": 1}},
        ]),
        message("msg_2", "end_turn", [{"type": "text", "text": "Alice Ng."}]),
    ]
    sent = []

    def handler(request):
        sent.append((request.headers, json.loads(request.content)))
        return httpx2.Response(200, json=replies[len(sent) - 1])

    client = anthropic.Anthropic(
        api_key="test", base_url="http://api.invalid", max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    answer = run_agent("Who is my best customer?", store, client)

    assert answer.text == "Alice Ng."
    headers, first = sent[0]
    assert headers["anthropic-beta"] == "server-side-fallback-2026-07-01"
    assert first["fallbacks"] == "default"
    assert first["output_config"] == {"effort": "medium"}
    _, second = sent[1]
    assert [m["role"] for m in second["messages"]] == ["user", "assistant", "user"]
    assert [c["type"] for c in second["messages"][1]["content"]] == ["text", "tool_use"]
    result = second["messages"][2]["content"][0]
    assert result["tool_use_id"] == "toolu_1"
    assert json.loads(result["content"])["rows"][0]["name"] == "Alice Ng"
