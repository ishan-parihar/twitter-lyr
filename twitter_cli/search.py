"""Advanced search query builder.

Composes Twitter search operators into a raw query string for the
SearchTimeline GraphQL endpoint.

Reference: https://help.x.com/en/using-x/x-advanced-search
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date

_LANG_PATTERN = re.compile(r"^[A-Za-z][A-Za-z-]{1,14}$")


def _normalize_handle(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip().lstrip("@")
    return text or None


def _normalize_lang(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip().lower()
    if not text:
        return None
    if not _LANG_PATTERN.match(text):
        raise ValueError("--lang must be an ISO language code like en or zh-cn")
    return text


def _normalize_date(flag_name: str, value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{flag_name} must be in YYYY-MM-DD format") from exc
    return text


def build_search_query(
    query: str = "",
    *,
    from_user: str | None = None,
    to_user: str | None = None,
    lang: str | None = None,
    since: str | None = None,
    until: str | None = None,
    has: Sequence[str] | None = None,
    exclude: Sequence[str] | None = None,
    min_likes: int | None = None,
    min_retweets: int | None = None,
) -> str:
    """Build an advanced search query string.

    Args:
        query: Base search keywords.
        from_user: Only tweets from this user (screen_name).
        to_user: Only tweets directed at this user.
        lang: ISO 639-1 language code (e.g. "en", "fr", "ja").
        since: Start date in YYYY-MM-DD format.
        until: End date in YYYY-MM-DD format.
        has: List of content types to require. Accepted values:
            "links", "images", "videos", "media".
        exclude: List of content types to exclude. Accepted values:
            "retweets", "replies", "links".
        min_likes: Minimum number of likes (faves).
        min_retweets: Minimum number of retweets.

    Returns:
        Composed query string ready for the rawQuery API parameter.
    """
    parts: list[str] = []
    query_text = query.strip()
    from_user = _normalize_handle(from_user)
    to_user = _normalize_handle(to_user)
    lang = _normalize_lang(lang)
    since = _normalize_date("--since", since)
    until = _normalize_date("--until", until)

    if min_likes is not None and min_likes < 0:
        raise ValueError("--min-likes must be greater than or equal to 0")
    if min_retweets is not None and min_retweets < 0:
        raise ValueError("--min-retweets must be greater than or equal to 0")
    if since and until and since > until:
        raise ValueError("--since must be on or before --until")

    if query_text:
        parts.append(query_text)

    if from_user:
        parts.append(f"from:{from_user}")
    if to_user:
        parts.append(f"to:{to_user}")
    if lang:
        parts.append(f"lang:{lang}")
    if since:
        parts.append(f"since:{since}")
    if until:
        parts.append(f"until:{until}")
    if has:
        for item in has:
            parts.append(f"filter:{item.lower()}")
    if exclude:
        for item in exclude:
            item = item.lower()
            if item == "retweets":
                parts.append("-filter:retweets")
            elif item == "replies":
                parts.append("-filter:replies")
            elif item == "links":
                parts.append("-filter:links")
            else:
                parts.append(f"-filter:{item}")
    if min_likes is not None:
        parts.append("min_faves:%d" % min_likes)
    if min_retweets is not None:
        parts.append("min_retweets:%d" % min_retweets)

    return " ".join(parts)
