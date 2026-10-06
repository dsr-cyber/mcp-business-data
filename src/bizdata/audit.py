import functools
import inspect
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


class AuditLog:
    """Append-only JSONL file, one line per tool call."""

    def __init__(self, path: Path | str | None):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()

    def write(self, **entry) -> None:
        if self.path is None:
            return
        entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), **entry}
        line = json.dumps(entry, default=str, ensure_ascii=False)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")


def _row_count(result) -> int | None:
    if not isinstance(result, dict):
        return None
    if "row_count" in result:
        return result["row_count"]
    for key in ("rows", "tables"):
        if isinstance(result.get(key), list):
            return len(result[key])
    return None


def audited(method):
    """Log every call of a Store method, including the ones that raise."""
    signature = inspect.signature(method)

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        bound = signature.bind(self, *args, **kwargs)
        bound.apply_defaults()
        call_args = dict(bound.arguments)
        call_args.pop("self")
        started = time.perf_counter()
        try:
            result = method(self, *args, **kwargs)
        except Exception as exc:
            self.audit.write(
                source=self.source, tool=method.__name__, args=call_args, rows=None,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                ok=False, error=f"{type(exc).__name__}: {exc}",
            )
            raise
        self.audit.write(
            source=self.source, tool=method.__name__, args=call_args, rows=_row_count(result),
            duration_ms=round((time.perf_counter() - started) * 1000, 2), ok=True,
        )
        return result

    return wrapper
