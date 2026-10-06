"""A small tool-use loop that answers questions with the same functions the MCP server exposes.

It calls Store methods in-process rather than going through MCP. The tool
names, descriptions and input schemas come from the MCP server definition,
so the two can't drift apart.
"""

import json
import os
from dataclasses import dataclass, field

import anyio

from .server import build_server
from .store import Store

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "medium"
# Models that accept server-side refusal fallbacks with fallbacks="default".
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1"}

SYSTEM = """\
You answer questions from the owner of Harbor & Pine Supply, a small online outdoor-gear store, using its database.

- Use sales_summary, top_customers, refund_rate and inventory_alerts when they fit. Their definitions of revenue and refunds are the ones the owner uses.
- For anything else, call describe_schema once, then run_sql. Aggregate in SQL instead of pulling raw rows.
- Order data runs from {first} to {last}. Read relative dates such as "last quarter" or "this year" against {last}, and state the exact dates you used.
- Reply in a few sentences with the numbers that matter. If the data can't answer the question, say so."""


@dataclass
class Answer:
    text: str
    stop_reason: str
    tool_calls: list[dict] = field(default_factory=list)


def tool_specs(store: Store) -> list[dict]:
    # FastMCP sets up root logging at INFO when built; WARNING keeps HTTP request lines out of ask.py's output.
    tools = anyio.run(build_server(store, log_level="WARNING").list_tools)
    return [{"name": t.name, "description": t.description, "input_schema": t.inputSchema} for t in tools]


def execute_tool(store: Store, allowed: set[str], name: str, args: dict) -> tuple[str, bool]:
    if name not in allowed:
        return f"unknown tool: {name}", True
    try:
        result = getattr(store, name)(**args)
    except (ValueError, TypeError) as exc:
        # Sent back to the model as a tool error so it can correct itself.
        return str(exc), True
    return json.dumps(result, default=str), False


def run_agent(
    question: str,
    store: Store,
    client,
    *,
    model: str | None = None,
    effort: str | None = None,
    max_turns: int = 12,
    on_tool_call=None,
) -> Answer:
    model = model or os.environ.get("BIZDATA_MODEL", DEFAULT_MODEL)
    effort = os.environ.get("BIZDATA_EFFORT", DEFAULT_EFFORT) if effort is None else effort

    tools = tool_specs(store)
    allowed = {t["name"] for t in tools}
    first, last = store.order_date_range()
    request = {
        "model": model,
        "max_tokens": 16000,
        "system": SYSTEM.format(first=first, last=last),
        "tools": tools,
    }
    if effort:
        request["output_config"] = {"effort": effort}
    if model in FALLBACK_MODELS:
        request["betas"] = ["server-side-fallback-2026-07-01"]
        request["fallbacks"] = "default"

    messages = [{"role": "user", "content": question}]
    calls = []
    for _ in range(max_turns):
        response = client.beta.messages.create(**request, messages=messages)
        # Append the response content untouched. Newer models check that earlier turns
        # weren't edited, so the history must only ever grow.
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "refusal":
            return Answer("The model declined to answer this question.", "refusal", calls)
        if response.stop_reason != "tool_use":
            text = "".join(b.text for b in response.content if b.type == "text").strip()
            if response.stop_reason == "max_tokens":
                text += "\n\n[answer cut off at the token limit]"
            return Answer(text, response.stop_reason, calls)

        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            output, is_error = execute_tool(store, allowed, block.name, block.input)
            calls.append({"name": block.name, "input": block.input, "is_error": is_error})
            if on_tool_call:
                on_tool_call(block.name, block.input, is_error)
            result = {"type": "tool_result", "tool_use_id": block.id, "content": output}
            if is_error:
                result["is_error"] = True
            results.append(result)
        # All results for one turn go back in a single message.
        messages.append({"role": "user", "content": results})

    return Answer(f"Stopped after {max_turns} rounds of tool calls without a final answer.", "max_turns", calls)
