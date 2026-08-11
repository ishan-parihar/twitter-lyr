"""Serialization helpers for Tweet, UserProfile, DM, List, Poll, and Community models."""

from __future__ import annotations

import json
from typing import (  # noqa: F401 (used in # type: comments)
    Any,
    Optional,
)

from .models import (
    Author,
    BookmarkFolder,
    Community,
    DMParticipant,
    Metrics,
    Poll,
    PollOption,
    Tweet,
    TweetMedia,
    UserProfile,
)
from .timeutil import format_iso8601, format_local_time

# ── Tweet serialization ──────────────────────────────────────────────────────


def tweet_to_dict(tweet: Tweet) -> dict[str, Any]:
    """Convert a Tweet dataclass into a JSON-safe dict.

    Text is truncated at 500 chars (AXI §3) with a ``truncated`` flag.
    """
    text = tweet.text
    truncated = False
    if len(text) > 500:
        text = text[:497] + "..."
        truncated = True
    data = {
        "id": tweet.id,
        "text": text,
        "author": {
            "id": tweet.author.id,
            "name": tweet.author.name,
            "screenName": tweet.author.screen_name,
            "profileImageUrl": tweet.author.profile_image_url,
            "verified": tweet.author.verified,
        },
        "metrics": {
            "likes": tweet.metrics.likes,
            "retweets": tweet.metrics.retweets,
            "replies": tweet.metrics.replies,
            "quotes": tweet.metrics.quotes,
            "views": tweet.metrics.views,
            "bookmarks": tweet.metrics.bookmarks,
        },
        "createdAt": tweet.created_at,
        "createdAtLocal": format_local_time(tweet.created_at),
        "createdAtISO": format_iso8601(tweet.created_at),
        "media": [
            {
                "type": media.type,
                "url": media.url,
                "width": media.width,
                "height": media.height,
            }
            for media in tweet.media
        ],
        "urls": list(tweet.urls),
        "isRetweet": tweet.is_retweet,
        "retweetedBy": tweet.retweeted_by,
        "lang": tweet.lang,
        "score": tweet.score,
        "isSubscriberOnly": tweet.is_subscriber_only,
        "isPromoted": tweet.is_promoted,
    }
    if truncated:
        data["truncated"] = True
    if tweet.article_title is not None:
        data["articleTitle"] = tweet.article_title
    if tweet.article_text is not None:
        data["articleText"] = tweet.article_text
    if tweet.quoted_tweet:
        data["quotedTweet"] = {
            "id": tweet.quoted_tweet.id,
            "text": tweet.quoted_tweet.text,
            "author": {
                "screenName": tweet.quoted_tweet.author.screen_name,
                "name": tweet.quoted_tweet.author.name,
            },
        }
    return data


def tweet_from_dict(data: dict[str, Any]) -> Tweet:
    """Convert a dict into a Tweet dataclass."""
    author_data = data.get("author") or {}
    metrics_data = data.get("metrics") or {}
    media_data = data.get("media") or []
    quoted_data = data.get("quotedTweet")

    quoted_tweet = None  # type: Optional[Tweet]
    if isinstance(quoted_data, dict):
        quoted_author = quoted_data.get("author") or {}
        quoted_tweet = Tweet(
            id=str(quoted_data.get("id") or ""),
            text=str(quoted_data.get("text") or ""),
            author=Author(
                id="",
                name=str(quoted_author.get("name") or ""),
                screen_name=str(quoted_author.get("screenName") or ""),
            ),
            metrics=Metrics(),
            created_at="",
        )

    return Tweet(
        id=str(data.get("id") or ""),
        text=str(data.get("text") or ""),
        author=Author(
            id=str(author_data.get("id") or ""),
            name=str(author_data.get("name") or ""),
            screen_name=str(author_data.get("screenName") or ""),
            profile_image_url=str(author_data.get("profileImageUrl") or ""),
            verified=bool(author_data.get("verified", False)),
        ),
        metrics=Metrics(
            likes=int(metrics_data.get("likes") or 0),
            retweets=int(metrics_data.get("retweets") or 0),
            replies=int(metrics_data.get("replies") or 0),
            quotes=int(metrics_data.get("quotes") or 0),
            views=int(metrics_data.get("views") or 0),
            bookmarks=int(metrics_data.get("bookmarks") or 0),
        ),
        created_at=str(data.get("createdAt") or ""),
        media=[
            TweetMedia(
                type=str(item.get("type") or ""),
                url=str(item.get("url") or ""),
                width=item.get("width"),
                height=item.get("height"),
            )
            for item in media_data
        ],
        urls=data.get("urls") or [],
        is_retweet=bool(data.get("isRetweet", False)),
        lang=str(data.get("lang") or ""),
        retweeted_by=data.get("retweetedBy"),
        quoted_tweet=quoted_tweet,
        score=data.get("score"),
        article_title=data.get("articleTitle"),
        article_text=data.get("articleText"),
        is_subscriber_only=bool(data.get("isSubscriberOnly", False)),
        is_promoted=bool(data.get("isPromoted", False)),
    )


def tweets_to_json(tweets: list[Tweet]) -> str:
    """Serialize tweets to JSON string."""
    return json.dumps([tweet_to_dict(t) for t in tweets], ensure_ascii=False, indent=2)


# ── Compact serialization (LLM-friendly minimal fields) ──────────────────────


def tweet_to_compact_dict(tweet: Tweet) -> dict[str, Any]:
    """Convert a Tweet into a compact dict with minimal fields for LLM consumption."""
    text = tweet.text.replace("\n", " ").strip()
    if len(text) > 140:
        text = text[:137] + "..."
    # Short time: "Mar 07 05:51" from "Sat Mar 07 05:51:02 +0000 2026"
    parts = tweet.created_at.split()
    if len(parts) >= 4:
        time_str = f"{parts[1]} {parts[2]} {parts[3][:5]}"
    else:
        time_str = tweet.created_at
    return {
        "id": tweet.id,
        "author": f"@{tweet.author.screen_name}",
        "text": text,
        "likes": tweet.metrics.likes,
        "rts": tweet.metrics.retweets,
        "time": time_str,
    }


def tweets_to_compact_json(tweets: list[Tweet], fields: str | None = None) -> str:
    """Serialize Tweet objects to compact JSON (minimal fields for LLM/pipe usage)."""
    return json.dumps(
        [tweet_to_compact_dict(tweet) for tweet in tweets],
        ensure_ascii=False,
        indent=2,
    )


def tweets_to_data(tweets: list[Tweet], fields: str | None = None) -> list[dict[str, Any]]:
    """Convert tweets to list of dicts for structured output."""
    return [tweet_to_dict(t) for t in tweets]


def tweets_from_json(json_str: str) -> list[Tweet]:
    """Parse tweets from JSON string.

    Handles both:
    - Direct array: [tweet1, tweet2, ...]
    - Envelope format: {"ok": true, "data": [tweet1, ...]}
    """
    data = json.loads(json_str)
    if isinstance(data, dict) and data.get("ok") is True and isinstance(data.get("data"), list):
        data = data["data"]
    if not isinstance(data, list):
        raise ValueError("Tweet JSON payload must be a list")
    return [tweet_from_dict(item) for item in data]


# ── UserProfile serialization ────────────────────────────────────────────────


def user_profile_to_dict(profile: UserProfile) -> dict[str, Any]:
    """Convert UserProfile to JSON-safe dict."""
    return {
        "id": profile.id,
        "name": profile.name,
        "screenName": profile.screen_name,
        "bio": profile.bio,
        "location": profile.location,
        "url": profile.url,
        "followers": profile.followers_count,
        "following": profile.following_count,
        "tweets": profile.tweets_count,
        "likes": profile.likes_count,
        "verified": profile.verified,
        "profileImageUrl": profile.profile_image_url,
        "createdAt": profile.created_at,
    }


def users_to_data(users: list[UserProfile]) -> list[dict[str, Any]]:
    """Convert users to list of dicts for structured output."""
    return [user_profile_to_dict(u) for u in users]


def bookmark_folders_to_data(folders: list[BookmarkFolder]) -> list[dict[str, Any]]:
    """Convert bookmark folders to list of dicts."""
    return [{"id": f.id, "name": f.name} for f in folders]


# ── DM serialization ─────────────────────────────────────────────────────────


def dm_participant_to_dict(p: DMParticipant) -> dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name,
        "screenName": p.screen_name,
        "profileImageUrl": p.profile_image_url,
    }


def dm_conversations_to_data(conversations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dm_conversation_to_dict(c) for c in conversations]


def dm_conversation_to_dict(conv: dict[str, Any]) -> dict[str, Any]:
    """Convert a DM conversation dict to serializable format."""
    participants = conv.get("participants", [])
    last_message = conv.get("last_message")
    return {
        "id": conv.get("id", ""),
        "participants": [
            {"screen_name": p.get("screen_name", ""), "name": p.get("name", "")}
            for p in participants
        ],
        "lastMessage": dm_message_to_dict(last_message) if last_message else None,
        "updatedAt": conv.get("updated_at", ""),
    }


def dm_messages_to_data(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dm_message_to_dict(m) for m in messages]


def dm_message_to_dict(msg: dict[str, Any]) -> dict[str, Any]:
    """Convert a DM message dict to serializable format."""
    return {
        "id": msg.get("id", ""),
        "sender_screen_name": msg.get("sender_screen_name", ""),
        "text": msg.get("text", ""),
        "created_at": msg.get("created_at", ""),
        "media": msg.get("media", []),
    }


# ── List serialization ───────────────────────────────────────────────────────


def lists_to_data(lists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [list_to_dict(item) for item in lists]


def list_to_dict(lst: dict[str, Any]) -> dict[str, Any]:
    """Convert a Twitter list dict to serializable format."""
    owner = lst.get("owner")
    return {
        "id": lst.get("id", ""),
        "name": lst.get("name", ""),
        "description": lst.get("description", ""),
        "private": lst.get("private", False),
        "memberCount": lst.get("member_count", 0),
        "subscriberCount": lst.get("subscriber_count", 0),
        "owner": user_profile_to_dict(owner) if owner else None,
        "createdAt": lst.get("created_at", ""),
    }


# ── Poll serialization ───────────────────────────────────────────────────────


def poll_option_to_dict(opt: PollOption) -> dict[str, Any]:
    return {"position": opt.position, "text": opt.text, "count": opt.count}


def poll_to_dict(p: Poll) -> dict[str, Any]:
    return {
        "options": [poll_option_to_dict(o) for o in p.options],
        "durationMinutes": p.duration_minutes,
        "endDatetime": p.end_datetime,
        "votingStatus": p.voting_status,
    }


# ── Community serialization ──────────────────────────────────────────────────


def community_to_dict(c: Community) -> dict[str, Any]:
    return {
        "id": c.id,
        "name": c.name,
        "description": c.description,
        "memberCount": c.member_count,
        "private": c.private,
        "ownerId": c.owner_id,
        "createdAt": c.created_at,
    }
