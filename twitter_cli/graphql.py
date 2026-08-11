"""GraphQL infrastructure for Twitter API.

Handles queryId resolution, URL building, JS bundle scanning,
and feature flag management.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
from typing import (  # noqa: F401 (used in # type: comments)
    Any,
    Optional,
)

from .exceptions import QueryIdError

logger = logging.getLogger(__name__)

# ── Community OpenAPI queryId source ─────────────────────────────────────
TWITTER_OPENAPI_URL = (
    "https://raw.githubusercontent.com/fa0311/"
    "twitter-openapi/refs/heads/main/src/config/placeholder.json"
)

# ── Fallback (hardcoded) queryIds ────────────────────────────────────────
FALLBACK_QUERY_IDS = {
    "HomeTimeline": "c-CzHF1LboFilMpsx4ZCrQ",
    "HomeLatestTimeline": "BKB7oi212Fi7kQtCBGE4zA",
    "UserByScreenName": "1VOOyvKkiI3FMmkeDNxM9A",
    "UserTweets": "q6xj5bs0hapm9309hexA_g",
    "TweetDetail": "xd_EMdYvB9hfZsZ6Idri0w",
    "Likes": "lIDpu_NWL7_VhimGGt0o6A",
    "SearchTimeline": "VhUd6vHVmLBcw0uX-6jMLA",
    "Bookmarks": "2neUNDqrrFzbLui8yallcQ",
    "ListLatestTweetsTimeline": "RlZzktZY_9wJynoepm8ZsA",
    "Followers": "IOh4aS6UdGWGJUYTqliQ7Q",
    "Following": "zx6e-TLzRkeDO_a7p4b3JQ",
    "CreateTweet": "IID9x6WsdMnTlXnzXGq8ng",
    "DeleteTweet": "VaenaVgh5q5ih7kvyVjgtg",
    "FavoriteTweet": "lI07N6Otwv1PhnEgXILM7A",
    "UnfavoriteTweet": "ZYKSe-w7KEslx3JhSIk5LA",
    "CreateRetweet": "ojPdsZsimiJrUGLR1sjUtA",
    "DeleteRetweet": "iQtK4dl5hBmXewYZuEOKVw",
    "CreateBookmark": "aoDbu3RHznuiSkQ9aNM67Q",
    "DeleteBookmark": "Wlmlj2-xzyS1GN3a6cj-mQ",
    "TweetResultByRestId": "7xflPyRiUxGVbJd4uWmbfg",
    "BookmarkFoldersSlice": "i78YDd0Tza-dV4SYs58kRg",
    "BookmarkFolderTimeline": "hNY7X2xE2N7HVF6Qb_mu6w",
    # DM operations
    "CreateDMConversation": "QJkNafOGYyFJQxuGq6QzGw",
    "SendDM": "yhxKxP4QVwkQdMJqxRgxHA",
    "GetDMConversations": "DkN8zP4QVwkQdMJqxRgxHA",
    "GetDMMessages": "VwkQdMJqxRgxHAyhxKxP4Q",
    "MarkDMConversationRead": "QdMJqxRgxHAyhxKxP4QVwk",
    "SendDMTypingIndicator": "dMJqxRgxHAyhxKxP4QVwkQ",
    "RotateDMEncryptionKeys": "MJqxRgxHAyhxKxP4QVwkQd",
    # Block operations
    "BlockUser": "zP4QVwkQdMJqxRgxHAyhxK",
    "UnblockUser": "QdMJqxRgxHAyhxKxP4QVwk",
    # Mute operations
    "MuteUser": "yhxKxP4QVwkQdMJqxRgxHA",
    "UnmuteUser": "QdMJqxRgxHAyhxKxP4QVwk",
    # Poll operations
    "CreatePoll": "kNafOGYyFJQxuGq6QzGwQJ",
    "VotePoll": "hxKxP4QVwkQdMJqxRgxHAy",
    # List operations
    "CreateList": "zP4QVwkQdMJqxRgxHAyhxK",
    "UpdateList": "dMJqxRgxHAyhxKxP4QVwkQ",
    "DeleteList": "MJqxRgxHAyhxKxP4QVwkQd",
    "AddListMember": "JqxRgxHAyhxKxP4QVwkQdM",
    "RemoveListMember": "qxRgxHAyhxKxP4QVwkQdMJ",
    "GetListMembers": "xRgxHAyhxKxP4QVwkQdMJq",
    "GetListSubscriptions": "RgxHAyhxKxP4QVwkQdMJqx",
    # Notifications
    "GetNotifications": "gxHAyhxKxP4QVwkQdMJqxR",
    # Communities
    "GetCommunityTweets": "xHAyhxKxP4QVwkQdMJqxRg",
    "JoinCommunity": "HAyhxKxP4QVwkQdMJqxRgx",
    "LeaveCommunity": "AyhxKxP4QVwkQdMJqxRgxH",
}

# ── Unsupported operations ────────────────────────────────
# Twitter replaced plaintext DM with the E2E-encrypted XChat protocol.
# The legacy DM ops below have no real public GraphQL operation IDs (the
# values left in FALLBACK_QUERY_IDS are fabricated placeholders, not
# resolvable ops), so issuing them yields a raw 404. Surface an honest
# error instead of a fabricated-ID request that cannot succeed.
UNSUPPORTED_FABRICATED_OPS = frozenset({
    "CreateDMConversation",
    "SendDM",
    "GetDMConversations",
    "GetDMMessages",
    "MarkDMConversationRead",
    "SendDMTypingIndicator",
    "RotateDMEncryptionKeys",
})

# ── Default feature flags ────────────────────────────────────────────────
_DEFAULT_FEATURES = {
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "tweetypie_unmention_optimization_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "tweet_awards_web_tipping_enabled": False,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "rweb_video_timestamps_enabled": True,
    "responsive_web_media_download_video_enabled": True,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "responsive_web_enhance_cards_enabled": False,
}

# Features dict that gets updated dynamically from x.com JS bundles
FEATURES = dict(_DEFAULT_FEATURES)

# Module-level caches (not thread-safe — CLI is single-threaded)
_cached_query_ids: dict[str, str] = {}
_bundles_scanned = False


def _build_graphql_url(query_id, operation_name, variables, features, field_toggles=None):
    # type: (str, str, dict[str, Any], dict[str, Any], Optional[dict[str, Any]]) -> str
    """Build GraphQL GET URL with encoded variables/features/fieldToggles.

    Only includes True-valued feature flags in the URL to avoid 414 URI Too Long.
    Twitter's API defaults missing features to False.
    """
    # Compact features: omit False values to keep URL under server limits
    compact_features = {k: v for k, v in features.items() if v is not False}
    url = "https://x.com/i/api/graphql/{}/{}?variables={}&features={}".format(
        query_id,
        operation_name,
        urllib.parse.quote(json.dumps(variables, separators=(",", ":"))),
        urllib.parse.quote(json.dumps(compact_features, separators=(",", ":"))),
    )
    if field_toggles:
        url += "&fieldToggles={}".format(urllib.parse.quote(
            json.dumps(field_toggles, separators=(",", ":"))
        ))
    return url


def _scan_bundles(url_fetch_fn):
    # type: (Any) -> None
    """Scan Twitter JS bundles and cache queryId mappings.

    Args:
        url_fetch_fn: Function to fetch URLs (injected to avoid circular import).
    """
    global _bundles_scanned
    if _bundles_scanned:
        return
    _bundles_scanned = True

    try:
        from .constants import get_user_agent

        html = url_fetch_fn("https://x.com", {"user-agent": get_user_agent()})
        script_pattern = re.compile(
            r'(?:src|href)=["\']'
            r'(https://abs\.twimg\.com/responsive-web/client-web[^"\']+'
            r"\.js)"
            r'["\']'
        )
        script_urls = script_pattern.findall(html)
    except Exception as exc:  # pragma: no cover - network-dependent branch
        logger.warning("Failed to scan JS bundles: %s", exc)
        return

    for script_url in script_urls:
        try:
            bundle = url_fetch_fn(script_url)
            op_pattern = re.compile(
                r'queryId:\s*"([A-Za-z0-9_-]+)"[^}]{0,200}'
                r'operationName:\s*"([^"]+)"'
            )
            for match in op_pattern.finditer(bundle):
                query_id, operation_name = match.group(1), match.group(2)
                _cached_query_ids.setdefault(operation_name, query_id)
        except Exception:
            continue

    logger.info(
        "Scanned %d JS bundles, cached %d query IDs", len(script_urls), len(_cached_query_ids)
    )


def _update_features_from_html(html):
    # type: (str) -> None
    """Extract live feature flags from x.com HTML and update the global FEATURES dict.

    Twitter embeds feature switch config in inline scripts on the homepage.
    We parse these to keep FEATURES in sync with the current frontend.
    Only UPDATES existing keys — never adds new ones to avoid URL bloat.
    """
    try:
        feature_pattern = re.compile(
            r'"([a-z][a-z0-9_]+)":\s*\{\s*"value"\s*:\s*(true|false)',
            re.IGNORECASE,
        )
        found = 0
        for match in feature_pattern.finditer(html):
            key = match.group(1)
            value = match.group(2).lower() == "true"
            # Only update keys already in FEATURES — never add new ones
            # Adding new keys inflates URL length, causing 414/431 errors
            if key in FEATURES and FEATURES[key] != value:
                logger.debug("Feature flag updated: %s = %s -> %s", key, FEATURES[key], value)
                FEATURES[key] = value
                found += 1
        if found:
            logger.info("Updated %d feature flags from x.com", found)
    except Exception as exc:
        logger.debug("Feature extraction from HTML failed: %s", exc)


def _fetch_from_github(url_fetch_fn, operation_name):
    # type: (Any, str) -> Optional[str]
    """Fetch queryId from community-maintained twitter-openapi file."""
    try:
        payload = url_fetch_fn(TWITTER_OPENAPI_URL)
        parsed = json.loads(payload)
        operation = parsed.get(operation_name, {})
        query_id = operation.get("queryId")
        if isinstance(query_id, str) and query_id:
            return query_id
    except Exception as exc:  # pragma: no cover - network-dependent branch
        logger.debug("GitHub queryId lookup failed: %s", exc)
    return None


def _invalidate_query_id(operation_name):
    # type: (str) -> None
    """Remove a cached queryId for an operation."""
    _cached_query_ids.pop(operation_name, None)


def _resolve_query_id(operation_name, prefer_fallback=True, url_fetch_fn=None):
    # type: (str, bool, Any) -> str
    """Resolve queryId using cache, remote sources, and fallback constants."""
    cached = _cached_query_ids.get(operation_name)
    if cached:
        return cached

    if operation_name in UNSUPPORTED_FABRICATED_OPS:
        raise QueryIdError(
            f'DM operation "{operation_name}" is disabled: Twitter replaced plaintext DM '
            "with the E2E-encrypted XChat protocol, and these legacy DM ops "
            "no longer exist at the API layer (their IDs are unresolvable). "
            "Real DM requires the XChat enrollment+crypto handshake."
        )

    fallback = FALLBACK_QUERY_IDS.get(operation_name)
    if prefer_fallback and fallback:
        _cached_query_ids[operation_name] = fallback
        return fallback

    if url_fetch_fn:
        github_query_id = _fetch_from_github(url_fetch_fn, operation_name)
        if github_query_id:
            _cached_query_ids[operation_name] = github_query_id
            return github_query_id

        _scan_bundles(url_fetch_fn)
        cached = _cached_query_ids.get(operation_name)
        if cached:
            return cached

    if fallback:
        _cached_query_ids[operation_name] = fallback
        return fallback

    raise QueryIdError(f'Cannot resolve queryId for "{operation_name}"')
