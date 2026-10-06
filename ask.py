"""Ask the store database a question in plain English.

    python ask.py "Which product category grew fastest last quarter?"

Tool calls are printed to stderr as they happen; the answer goes to stdout.
"""

import argparse
import json
import os
import sys
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent / ".env"

NO_KEY = """\
ask.py calls the Claude API, so it needs an API key.

  1. Create a key in the Claude Console: https://platform.claude.com/
  2. Set it for this shell:
       macOS / Linux:   export ANTHROPIC_API_KEY="sk-ant-..."
       PowerShell:      $env:ANTHROPIC_API_KEY = "sk-ant-..."
     or put ANTHROPIC_API_KEY=sk-ant-... in a .env file next to ask.py.

The MCP server (bizdata-mcp) doesn't need a key; Claude Desktop or Claude Code brings its own."""


def load_dotenv(path: Path) -> None:
    # Just KEY=VALUE lines. Real environment variables win.
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if value.strip():
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Ask a question about the store's data.")
    parser.add_argument("question", nargs="+")
    parser.add_argument("--model", help="defaults to $BIZDATA_MODEL or claude-opus-5-5")
    args = parser.parse_args(argv)

    load_dotenv(ENV_FILE)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(NO_KEY, file=sys.stderr)
        return 2

    import anthropic

    from bizdata.agent import run_agent
    from bizdata.store import Store

    def show(name, tool_input, is_error):
        mark = "error" if is_error else "ok"
        print(f"  [{mark}] {name} {json.dumps(tool_input)}", file=sys.stderr)

    try:
        answer = run_agent(
            " ".join(args.question),
            Store.from_env(source="agent"),
            anthropic.Anthropic(),
            model=args.model,
            on_tool_call=show,
        )
    except anthropic.AuthenticationError:
        print("The API rejected the key in ANTHROPIC_API_KEY.", file=sys.stderr)
        return 1
    except anthropic.RateLimitError:
        print("Rate limited by the API. Wait a minute and try again.", file=sys.stderr)
        return 1
    except anthropic.APIStatusError as exc:
        print(f"API error {exc.status_code}: {exc.message}", file=sys.stderr)
        return 1
    except anthropic.APIConnectionError:
        print("Couldn't reach the API. Check your connection.", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1

    print(answer.text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
