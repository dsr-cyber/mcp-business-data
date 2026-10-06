import os
import sqlite3
import time
from pathlib import Path

from . import schema, sqlguard
from .audit import AuditLog, audited

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "data" / "harbor_pine.db"
DEFAULT_AUDIT_LOG = PROJECT_ROOT / "logs" / "audit.jsonl"

# Functions that can allocate large blobs or reach outside the database.
BLOCKED_FUNCTIONS = {"load_extension", "randomblob", "zeroblob", "readfile", "writefile", "edit", "fts3_tokenizer"}


class QueryError(ValueError):
    pass


class Store:
    def __init__(
        self,
        db_path: Path | str,
        *,
        audit_path: Path | str | None = None,
        max_rows: int = 200,
        timeout_s: float = 5.0,
        hidden_columns: set[str] | frozenset[str] = frozenset(),
        source: str = "mcp",
    ):
        self.db_path = Path(db_path).resolve()
        self.audit = AuditLog(audit_path)
        self.max_rows = max_rows
        self.timeout_s = timeout_s
        self.hidden_columns = {c.strip().lower() for c in hidden_columns if c.strip()}
        self.source = source

    @classmethod
    def from_env(cls, source: str = "mcp") -> "Store":
        hidden = os.environ.get("BIZDATA_HIDDEN_COLUMNS", "customers.email")
        return cls(
            os.environ.get("BIZDATA_DB", DEFAULT_DB),
            audit_path=os.environ.get("BIZDATA_AUDIT_LOG", DEFAULT_AUDIT_LOG),
            max_rows=int(os.environ.get("BIZDATA_MAX_ROWS", 200)),
            timeout_s=float(os.environ.get("BIZDATA_TIMEOUT_S", 5)),
            hidden_columns=set(hidden.split(",")),
            source=source,
        )

    def connect(self, *, guarded: bool = True) -> sqlite3.Connection:
        """Open the database read-only.

        guarded=True is for SQL we didn't write: it adds the authorizer, size
        limits and the timeout. The business tools run fixed, reviewed SQL and
        use guarded=False, which is still read-only at the file level.
        """
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"database not found: {self.db_path} (run scripts/make_sample_db.py or set BIZDATA_DB)"
            )
        con = sqlite3.connect(self.db_path.as_uri() + "?mode=ro", uri=True)
        con.execute("PRAGMA query_only = ON")
        if guarded:
            con.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
            con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1_000_000)
            con.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 100_000)
            con.set_authorizer(self._authorize)
        return con

    def _authorize(self, action, arg1, arg2, db_name, trigger):
        if action == sqlite3.SQLITE_READ:
            if f"{arg1}.{arg2}".lower() in self.hidden_columns:
                return sqlite3.SQLITE_IGNORE  # the column reads as NULL
            return sqlite3.SQLITE_OK
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION:
            return sqlite3.SQLITE_DENY if (arg2 or "").lower() in BLOCKED_FUNCTIONS else sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    def _execute_guarded(self, sql: str, params=(), max_rows: int | None = None) -> dict:
        cap = self.max_rows if max_rows is None else max_rows
        con = self.connect(guarded=True)
        deadline = time.monotonic() + self.timeout_s
        # Returning non-zero from the progress handler aborts the running statement.
        con.set_progress_handler(lambda: int(time.monotonic() > deadline), 5_000)
        try:
            cur = con.execute(sql, params)
            columns = [d[0] for d in cur.description or []]
            fetched = cur.fetchmany(cap + 1)
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc):
                raise QueryError(f"query took longer than {self.timeout_s:g}s and was stopped") from None
            raise QueryError(f"SQLite error: {exc}") from None
        except sqlite3.DatabaseError as exc:
            raise QueryError(f"SQLite error: {exc}") from None
        finally:
            con.close()
        rows = [[_jsonable(v) for v in row] for row in fetched[:cap]]
        return {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": len(fetched) > cap}

    @audited
    def run_sql(self, query: str) -> dict:
        try:
            statement = sqlguard.validate(query)
        except sqlguard.UnsafeQuery as exc:
            raise QueryError(str(exc)) from None
        return self._execute_guarded(statement)

    @audited
    def describe_schema(self) -> dict:
        con = self.connect(guarded=False)
        try:
            names = [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )]
            tables = []
            for name in names:
                columns = []
                for _, col, col_type, notnull, _, pk in con.execute(f'PRAGMA table_info("{name}")'):
                    entry = {"name": col, "type": col_type, "not_null": bool(notnull), "primary_key": bool(pk)}
                    note = schema.COLUMN_NOTES.get((name, col))
                    if f"{name}.{col}".lower() in self.hidden_columns:
                        note = "Hidden from run_sql: always reads as NULL."
                    if note:
                        entry["note"] = note
                    columns.append(entry)
                fks = [
                    f"{fk[3]} -> {fk[2]}.{fk[4]}"
                    for fk in con.execute(f'PRAGMA foreign_key_list("{name}")')
                ]
                row_count = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                tables.append({
                    "name": name,
                    "note": schema.TABLE_NOTES.get(name, ""),
                    "row_count": row_count,
                    "columns": columns,
                    "foreign_keys": fks,
                })
        finally:
            con.close()
        return {"dialect": "SQLite", "tables": tables, "conventions": schema.CONVENTIONS}

    def schema_markdown(self) -> str:
        info = self.describe_schema()
        lines = [f"# Database schema ({info['dialect']})", ""]
        for table in info["tables"]:
            lines.append(f"## {table['name']} ({table['row_count']} rows)")
            if table["note"]:
                lines.append(table["note"])
            lines.append("")
            for col in table["columns"]:
                flags = " PK" if col["primary_key"] else ""
                note = f" - {col['note']}" if col.get("note") else ""
                lines.append(f"- `{col['name']}` {col['type']}{flags}{note}")
            for fk in table["foreign_keys"]:
                lines.append(f"- FK {fk}")
            lines.append("")
        lines.append("## Conventions")
        lines.extend(f"- {c}" for c in info["conventions"])
        return "\n".join(lines) + "\n"


def _jsonable(value):
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    return value
