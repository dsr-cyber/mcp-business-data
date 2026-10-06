"""Text-level check for model-written SQL.

This is the first and weakest of three layers. It exists to give the model a
clear, early error ("only SELECT is allowed") instead of a cryptic SQLite one.
The real guarantees come from opening the file read-only and from the SQLite
authorizer in store.py, which see the statement SQLite actually compiled.
"""

import re


class UnsafeQuery(ValueError):
    pass


_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|ATTACH|DETACH|PRAGMA|VACUUM|REINDEX|ANALYZE|TRUNCATE|GRANT|REVOKE)\b"
)
# REPLACE is both a statement (REPLACE INTO) and a common string function.
_REPLACE_STATEMENT = re.compile(r"\bREPLACE\b(?!\s*\()")
_FIRST_WORD = re.compile(r"\s*([A-Za-z]+)")


def _skeleton(sql: str) -> str:
    """Return sql with comments removed and literals/quoted identifiers blanked out."""
    out = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        two = sql[i:i + 2]
        if two == "--":
            end = sql.find("\n", i)
            i = n if end == -1 else end + 1
            out.append(" ")
        elif two == "/*":
            end = sql.find("*/", i + 2)
            if end == -1:
                raise UnsafeQuery("unterminated /* comment")
            i = end + 2
            out.append(" ")
        elif ch in "'\"`[":
            close = "]" if ch == "[" else ch
            j = i + 1
            while True:
                j = sql.find(close, j)
                if j == -1:
                    raise UnsafeQuery("unterminated quoted string or identifier")
                # A doubled quote is an escaped quote, not the end of the literal.
                if close != "]" and sql[j + 1:j + 2] == close:
                    j += 2
                    continue
                break
            out.append(" x ")
            i = j + 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def validate(sql: str) -> str:
    """Return the statement without a trailing semicolon, or raise UnsafeQuery."""
    if not sql or not sql.strip():
        raise UnsafeQuery("empty query")

    body = _skeleton(sql).strip().rstrip(";")
    if ";" in body:
        raise UnsafeQuery("exactly one statement is allowed")

    first = _FIRST_WORD.match(body)
    if not first or first.group(1).upper() not in ("SELECT", "WITH"):
        raise UnsafeQuery("only SELECT (or WITH ... SELECT) queries are allowed")

    upper = body.upper()
    hit = _FORBIDDEN.search(upper) or _REPLACE_STATEMENT.search(upper)
    if hit:
        raise UnsafeQuery(f"{hit.group(1)} is not allowed in a read-only query")

    return sql.strip().rstrip(";").rstrip()
