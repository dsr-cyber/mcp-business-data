# mcp-business-data

An MCP server that lets Claude answer questions about a small business's own data, plus a short command-line agent that does the same thing through the Claude API.

The data here belongs to a made-up outdoor gear shop, Harbor & Pine Supply: about 3,000 customers, 46 products, 4,300 orders and 330 refunds over 18 months (April 2025 to September 2026), generated with seasonality, category growth, a few loyal repeat buyers, one tent with a zipper problem, and some items about to run out. I built it to show the pattern I use with clients: give the model a few well-defined business tools, a read-only SQL escape hatch with real guard rails, and a log of everything it ran.

Swap the SQLite file for your own database and most of this carries over. See [Adapting it to your own database](#adapting-it-to-your-own-database).

## Quick start

Needs Python 3.11 or newer.

```bash
git clone <this repo> mcp-business-data
cd mcp-business-data
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python scripts/make_sample_db.py   # writes data/harbor_pine.db (seeded, same data every run)
pytest
```

Then connect it to Claude Desktop or Claude Code (below) and ask something like "which products should I reorder this week?"

## The tools

| Tool | What it answers |
|---|---|
| `sales_summary(start, end, group_by)` | Orders, units, revenue, refunds and net revenue by day, week, month, quarter, category, product or channel, with a totals row. |
| `top_customers(n, start, end)` | Best customers by net revenue after refunds. |
| `refund_rate(by, start, end)` | Refunded dollars as a share of revenue, by category, product, month or channel. |
| `inventory_alerts(min_days_of_cover)` | Active products that are out of stock, at or below their reorder point, or under N days of cover at the last 30 days' sales rate. |
| `describe_schema()` | Tables, columns, row counts, foreign keys, and notes such as "revenue lives on order_items, not orders". |
| `run_sql(query)` | One read-only SELECT for anything the other tools don't cover. Returns `columns`, `rows`, `row_count` and `truncated`. |

The schema is also published as an MCP resource, `schema://tables`, in Markdown.

The business tools exist because "revenue" is ambiguous. Does it include shipping? Cancelled orders? Are refunds subtracted, and by order date or refund date? Left to write its own SQL, a model will pick an answer, and it won't always pick the same one. The fixed tools encode the store's definitions once (completed orders only, shipping excluded, refunds attributed to the original order's date), and the schema notes repeat them for when the model does fall back to `run_sql`. The business tools only run SQL written ahead of time. Values go in as parameters, and `group_by` picks from a fixed dict of SQL expressions rather than being pasted into the query.

Every call, from the server or from `ask.py`, is appended to `logs/audit.jsonl`:

```json
{"ts": "2026-10-06T15:22:57.245+00:00", "source": "mcp", "tool": "sales_summary", "args": {"start": "2026-04-01", "end": "2026-06-30", "group_by": "category"}, "rows": 6, "duration_ms": 8.06, "ok": true}
{"ts": "2026-10-06T15:22:57.358+00:00", "source": "mcp", "tool": "run_sql", "args": {"query": "DROP TABLE orders"}, "rows": null, "duration_ms": 0.03, "ok": false, "error": "QueryError: only SELECT (or WITH ... SELECT) queries are allowed"}
```

## How the read-only guard works

`run_sql` runs SQL that a model wrote, so it goes through three independent layers. Any one of them would stop a write on its own.

1. Text check (`src/bizdata/sqlguard.py`). Comments are stripped and string literals blanked, then the query must be a single statement starting with `SELECT` or `WITH`, with no `INSERT`, `UPDATE`, `DELETE`, `DROP`, `PRAGMA`, `ATTACH` and so on outside of quotes. This is the weakest layer. Its job is to give the model a clear error message it can act on.
2. Read-only connection. The file is opened with SQLite's `mode=ro` URI flag and `PRAGMA query_only = ON`. SQLite itself refuses to write.
3. SQLite authorizer. SQLite calls back for every operation in the compiled statement, and the callback allows only `SELECT`, column reads, recursive CTEs, and function calls not on a short block list (`load_extension`, `zeroblob`, `randomblob` and a few others). That covers anything the text check might have misparsed, because it judges what SQLite actually compiled rather than what the text looked like.

On top of that:

- Results stop at 200 rows (`BIZDATA_MAX_ROWS`). The response says `"truncated": true` so the model knows it's looking at part of the answer.
- A progress handler cancels a query after 5 seconds (`BIZDATA_TIMEOUT_S`). The test suite checks this with an endless recursive CTE.
- Strings and blobs are capped at 1 MB, and attaching other databases is turned off.
- Columns listed in `BIZDATA_HIDDEN_COLUMNS` (default `customers.email`) read as NULL through `run_sql`, including in `WHERE` clauses, so `WHERE email LIKE 'a%'` can't be used to guess them. `describe_schema` tells the model the column is hidden.

The tests in `tests/test_run_sql.py` call the executor directly with the text check skipped, to show layers 2 and 3 hold by themselves.

### What it doesn't protect against

- Reading. Anything not hidden can be read, by design. If a table shouldn't be visible to the model, it shouldn't be in this database file, or its columns should be hidden.
- Where the data goes. Rows that tools return are sent to whichever model is using them. With Claude Desktop, Claude Code or `ask.py`, that means Anthropic's API. Check that this is acceptable for your data before connecting it.
- Prompt injection through data. If a customer types instructions into a free-text field and a tool returns that text, the model reads it. With read-only tools the damage is limited to a misleading answer, but it's not zero.
- Load. A 5-second query can still be an expensive one. On a busy production database, point this at a replica.
- The HTTP transport has no authentication. It binds to 127.0.0.1 by default. Don't expose it to a network without putting authentication in front of it.

## Sample: "Which product category grew fastest last quarter?"

I haven't run `ask.py` against the API for this README, so there is no model transcript here. What follows is real output from a local run: the server started over stdio, and these are the tool calls a model would make for this question (output trimmed to the fields that matter).

```text
>>> sales_summary {"start": "2026-04-01", "end": "2026-06-30", "group_by": "category"}
>>> sales_summary {"start": "2026-07-01", "end": "2026-09-30", "group_by": "category"}

category       Q2 net       Q3 net   change
Camping     64,706.55    76,523.35   +18.3%
Water       41,345.00    48,856.00   +18.2%
Hiking      35,804.25    35,682.05    -0.3%
Apparel     16,906.20    19,337.60   +14.4%
Climbing    16,301.05    14,960.30    -8.2%
Accessories  8,666.65     9,309.00    +7.4%
```

Quarter over quarter, Camping edges out Water. That's mostly the season, though. Run the same call for Q3 2025 and the picture changes: Climbing is up 49% on the same quarter last year, Camping 38%, Accessories 35%, and Water is flat. A good answer mentions both, and the system prompt in `ask.py` tells the model to state which dates it compared.

A few more calls from the same run:

```text
>>> inventory_alerts {}
{"sku": "HIK-008", "name": "Trail Map Case", "stock_on_hand": 0, "reorder_point": 5, "units_last_30d": 9, "days_of_cover": 0.0, "status": "out of stock"}
{"sku": "ACC-003", "name": "Multi-Tool", "stock_on_hand": 1, "reorder_point": 6, "units_last_30d": 11, "days_of_cover": 2.7, "status": "at or below reorder point"}
...  as_of 2026-09-30

>>> refund_rate {"by": "product", "start": "2026-01-01", "end": "2026-09-30"}
{"product": "Cedar Flat 4P Family Tent", "units_sold": 138, "units_refunded": 25, "revenue": 47080.1, "refunded": 8602.85, "refund_rate_pct": 18.27}
{"product": "Sun Hoodie", "units_sold": 24, "units_refunded": 4, "revenue": 1269.0, "refunded": 216.0, "refund_rate_pct": 17.02}
...

>>> run_sql {"query": "SELECT email FROM customers LIMIT 2"}
{"columns": ["email"], "rows": [[null], [null]], "row_count": 2, "truncated": false}

>>> run_sql {"query": "DROP TABLE orders"}
Error executing tool run_sql: only SELECT (or WITH ... SELECT) queries are allowed
```

The family tent is the planted quality problem. The hoodie is noise from 24 units, which is why `units_sold` sits next to every rate.

## Connecting it to Claude

The server runs over stdio by default. Use absolute paths, since neither client starts it from this folder.

### Claude Code

```bash
claude mcp add --env BIZDATA_DB=/path/to/mcp-business-data/data/harbor_pine.db --transport stdio harbor-pine-data \
  -- /path/to/mcp-business-data/.venv/bin/bizdata-mcp
```

On Windows the command is `C:\path\to\mcp-business-data\.venv\Scripts\bizdata-mcp.exe`. Add `--scope project` to write it to a `.mcp.json` you can commit for your team, or `--scope user` to have it in every project.

### Claude Desktop

Add this to `claude_desktop_config.json` (Settings > Developer > Edit Config), then restart the app.

```json
{
  "mcpServers": {
    "harbor-pine-data": {
      "command": "/path/to/mcp-business-data/.venv/bin/bizdata-mcp",
      "env": {
        "BIZDATA_DB": "/path/to/mcp-business-data/data/harbor_pine.db"
      }
    }
  }
}
```

### Over HTTP

Streamable HTTP, for a server that other machines reach through an authenticating proxy:

```bash
bizdata-mcp --http --host 127.0.0.1 --port 8000
claude mcp add --transport http harbor-pine-data http://127.0.0.1:8000/mcp
```

### Settings

All optional, as environment variables:

| Variable | Default |
|---|---|
| `BIZDATA_DB` | `data/harbor_pine.db` in this repo |
| `BIZDATA_AUDIT_LOG` | `logs/audit.jsonl` in this repo |
| `BIZDATA_MAX_ROWS` | `200` |
| `BIZDATA_TIMEOUT_S` | `5` |
| `BIZDATA_HIDDEN_COLUMNS` | `customers.email` (comma-separated `table.column`; set it empty to hide nothing) |

## ask.py

A command-line agent for when you want answers without a chat app, or want to see the tool-use loop itself.

```bash
export ANTHROPIC_API_KEY=sk-ant-...      # or put it in .env (see .env.example)
python ask.py "Which product category grew fastest last quarter?"
```

It calls the same `Store` methods the server wraps, in-process, with no MCP in between. The tool names, descriptions and input schemas are read from the MCP server definition, so there's one place to edit them, and a test fails if a tool and its method ever disagree. The loop is `run_agent` in `src/bizdata/agent.py`, about 60 lines: send the question with the tools, run whatever tools come back, return all the results in one message, repeat until the model answers or 12 rounds pass. A bad tool call goes back to the model as an error so it can correct itself.

It uses `claude-opus-5-5` at `medium` effort. Change them with `--model`, `BIZDATA_MODEL` or `BIZDATA_EFFORT` (set `BIZDATA_EFFORT=` empty for older models that don't accept the effort setting). For current models it also turns on the API's server-side refusal fallback, so a request the main model declines is retried on a fallback model instead of coming back empty. Without a key it prints setup instructions and exits.

## Adapting it to your own database

The SQLite-specific parts are small: `Store.connect` and `Store._execute_guarded` (connection, authorizer, timeout), `describe_schema` (uses `PRAGMA table_info`), the date functions inside `reports.py`, and the DDL in `schema.py`. Rewrite `TABLE_NOTES`, `COLUMN_NOTES` and `CONVENTIONS` in `schema.py` for your tables. That file has more effect on answer quality than any of the code.

For a server database, don't rely on the text check. Let the database enforce read-only access.

### PostgreSQL

- Connect as a role that only has `SELECT` on the tables you want exposed. For hidden columns, use column-level grants (`GRANT SELECT (id, name, state) ON customers TO ai_reader`) or a view.
- Set the limits on the role, so they hold however the connection is opened: `ALTER ROLE ai_reader SET default_transaction_read_only = on; ALTER ROLE ai_reader SET statement_timeout = '5s';`
- With psycopg 3, also set `conn.read_only = True`, and use `cursor.fetchmany(max_rows + 1)` for the row cap, as here.
- `describe_schema` reads `information_schema.columns`. In the reports, replace `strftime('%Y-%m', ...)` with `to_char(date_trunc('month', ...), 'YYYY-MM')`, and the `:name` parameters with `%(name)s`.
- Point it at a read replica if one exists.

### MySQL

- A user with `SELECT` only, plus column-level grants or views for anything sensitive.
- Per session: `SET SESSION TRANSACTION READ ONLY` and `SET SESSION max_execution_time = 5000` (milliseconds; it applies to SELECT statements).
- Parameters become `%(name)s` with PyMySQL or mysql-connector, and `strftime` becomes `DATE_FORMAT`.

Keep the business tools close to the questions the owner actually asks. Five tools that match how they talk about the business beat thirty generic ones.

## Limitations

- The sample data is generated. The numbers illustrate the tools and say nothing about any real store.
- `inventory_alerts` measures sales velocity up to the most recent order in the data rather than today's date, so it works on a static sample. On a live database those are the same thing.
- One database, one set of credentials, no per-user permissions. Everyone connected to the server sees the same data.
- `run_sql` returns at most 200 rows. The model is told when it hit the cap, but it can still reason from a partial result if it ignores the flag.
- The MCP SDK is pinned to the 1.x line (`mcp>=1.26,<2`). Version 2 renames `FastMCP` to `MCPServer` and moves transport settings onto `run()`. I haven't ported to it yet.
- `ask.py` is tested against a fake client and against the real SDK talking to a mocked HTTP endpoint, not against the live API.

## Layout

```text
src/bizdata/
  server.py     MCP server: tool definitions and descriptions, schema resource
  store.py      read-only connection, authorizer, run_sql, describe_schema, audited methods
  sqlguard.py   text check for model-written SQL
  reports.py    fixed SQL for the business tools
  schema.py     DDL plus the notes the model reads
  audit.py      JSONL audit log
  agent.py      tool-use loop used by ask.py
scripts/make_sample_db.py
ask.py
tests/
```

MIT licensed.
