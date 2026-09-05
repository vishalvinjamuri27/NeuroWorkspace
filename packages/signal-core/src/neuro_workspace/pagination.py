"""Stable cursor pagination on `(sort_column, id)`.

Offset pagination (`LIMIT/OFFSET`) has two real problems at the scale this
project is meant to demonstrate: pages shift under concurrent inserts (a row
inserted ahead of the current page pushes every later row's offset by one,
so a client paging through can skip or repeat rows), and a large offset is
still a full index walk-and-discard, not a jump -- page 500 costs roughly
500x page 1. A cursor keyed on a column already covered by this project's
composite indexes (`created_at, id` for recordings; `onset_seconds, id` for
annotations) turns "page N" into an index range scan starting exactly where
the client left off, and is stable regardless of concurrent writes because
it is anchored to a specific row, not a row count.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any

from sqlalchemy import Select, and_, or_
from sqlalchemy.orm import Session

from neuro_workspace.errors import ValidationError

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


def clamp_limit(limit: int) -> int:
    return max(1, min(limit, MAX_PAGE_SIZE))


def _encode(kind: str, value: str | float, row_id: str) -> str:
    payload = json.dumps([kind, value, row_id])
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def encode_datetime_cursor(value: datetime, row_id: str) -> str:
    return _encode("dt", value.isoformat(), row_id)


def encode_float_cursor(value: float, row_id: str) -> str:
    return _encode("f", value, row_id)


def _decode(token: str) -> tuple[datetime | float, str]:
    try:
        kind, raw_value, row_id = json.loads(base64.urlsafe_b64decode(token.encode("ascii")))
        if kind == "dt":
            return datetime.fromisoformat(raw_value), row_id
        if kind == "f":
            return float(raw_value), row_id
    except Exception as exc:
        raise ValidationError(
            "invalid_cursor", "The pagination cursor is invalid or expired."
        ) from exc
    raise ValidationError("invalid_cursor", "The pagination cursor is invalid or expired.")


def paginate[T](
    session: Session,
    stmt: Select[tuple[T]],
    *,
    # Typed as Any rather than ColumnElement[datetime] | ColumnElement[float]:
    # an ORM-mapped column (e.g. Recording.created_at) is an
    # InstrumentedAttribute, which mypy's SQLAlchemy plugin does not treat as
    # a subtype of ColumnElement[...] even though it satisfies the same
    # comparison operators at runtime.
    sort_column: Any,
    id_column: Any,
    cursor: str | None,
    limit: int,
    descending: bool = True,
) -> tuple[list[T], bool]:
    """Apply a cursor filter and fetch one page plus a lookahead row.

    `stmt` must already be ordered by `sort_column, id_column` in the same
    direction as `descending`. Fetching `limit + 1` rows lets this tell
    "there is another page" apart from "the result set ended exactly on a
    page boundary" without a separate COUNT(*) query. Returns `(page,
    has_more)`; the caller builds the next cursor from the last row of
    `page` since encoding it (datetime vs float) is type-specific.
    """

    limit = clamp_limit(limit)
    if cursor:
        sort_value, row_id = _decode(cursor)
        if descending:
            stmt = stmt.where(
                or_(
                    sort_column < sort_value,
                    and_(sort_column == sort_value, id_column < row_id),
                )
            )
        else:
            stmt = stmt.where(
                or_(
                    sort_column > sort_value,
                    and_(sort_column == sort_value, id_column > row_id),
                )
            )
    rows = list(session.scalars(stmt.limit(limit + 1)))
    has_more = len(rows) > limit
    page = rows[:limit]
    return page, has_more
