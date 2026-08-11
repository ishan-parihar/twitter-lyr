"""Tweet formatter for terminal output (rich) and JSON export."""

from __future__ import annotations

import sys

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from .models import Tweet, UserProfile
from .timeutil import format_local_time, format_relative_time


def _make_console() -> Console:
    """Create a Console that works correctly on Windows pipes.

    On Windows, rich may use WriteConsoleW API directly instead of writing
    to stdout, making output invisible to pipe/subprocess capture.
    Using force_terminal=False in non-TTY contexts prevents this.
    Output goes to stderr to avoid polluting structured stdout output.
    """
    if sys.platform == "win32" and not sys.stderr.isatty():
        return Console(stderr=True, force_terminal=False)
    return Console(stderr=True)


def format_number(n: int) -> str:
    """Format number with K/M suffixes."""
    if n >= 1_000_000:
        return "%.1fM" % (n / 1_000_000)
    if n >= 1_000:
        return "%.1fK" % (n / 1_000)
    return str(n)


def print_tweet_table(
    tweets: list[Tweet],
    console: Console | None = None,
    title: str | None = None,
    full_text: bool = False,
) -> None:
    """Print tweets as a rich table."""
    if console is None:
        console = _make_console()

    if not title:
        title = "📱 Twitter — %d tweets" % len(tweets)

    table = Table(title=title, show_lines=True, expand=True)
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("Author", style="cyan", width=18, no_wrap=True)
    table.add_column("Tweet", ratio=3)
    table.add_column("Stats", style="green", width=22, no_wrap=True)
    table.add_column("Score", style="yellow", width=6, justify="right")

    for i, tweet in enumerate(tweets):
        # Author
        verified = " ✓" if tweet.author.verified else ""
        author_text = f"@{tweet.author.screen_name}{verified}"
        if tweet.is_retweet and tweet.retweeted_by:
            author_text += f"\n🔄 @{tweet.retweeted_by}"

        # Tweet text
        text = tweet.text.replace("\n", " ").strip()
        if not full_text and len(text) > 120:
            text = text[:117] + "..."

        # Media indicators
        if tweet.media:
            media_icons = []
            for m in tweet.media:
                if m.type == "photo":
                    media_icons.append("📷")
                elif m.type == "video":
                    media_icons.append("📹")
                else:
                    media_icons.append("🎞️")
            text += " " + " ".join(media_icons)

        # Quoted tweet
        if tweet.quoted_tweet:
            qt = tweet.quoted_tweet
            qt_text = qt.text.replace("\n", " ")
            if not full_text and len(qt_text) > 60:
                qt_text = qt_text[:57] + "..."
            text += f"\n┌ @{qt.author.screen_name}: {qt_text}"

        # Tweet link
        text += f"\n🔗 x.com/{tweet.author.screen_name}/status/{tweet.id}"

        # Stats
        rel_time = format_relative_time(tweet.created_at)
        stats = f"❤️ {format_number(tweet.metrics.likes)}  🔄 {format_number(tweet.metrics.retweets)}\n💬 {format_number(tweet.metrics.replies)}  👁️ {format_number(tweet.metrics.views)}\n🕐 {rel_time}"

        # Score
        score_str = f"{tweet.score:.1f}" if tweet.score is not None else "-"

        table.add_row(str(i + 1), author_text, text, stats, score_str)

    console.print(table)


def print_tweet_detail(tweet: Tweet, console: Console | None = None) -> None:
    """Print a single tweet in detail using a rich panel."""
    if console is None:
        console = _make_console()

    verified = " ✓" if tweet.author.verified else ""
    header = f"@{tweet.author.screen_name}{verified} ({tweet.author.name})"

    body_parts = []

    if tweet.is_retweet and tweet.retweeted_by:
        body_parts.append(f"🔄 Retweeted by @{tweet.retweeted_by}\n")

    body_parts.append(tweet.text)

    if tweet.media:
        body_parts.append("")
        for m in tweet.media:
            icon = "📷" if m.type == "photo" else ("📹" if m.type == "video" else "🎞️")
            body_parts.append(f"{icon} {m.type}: {m.url}")

    if tweet.urls:
        body_parts.append("")
        for url in tweet.urls:
            body_parts.append(f"🔗 {url}")

    if tweet.quoted_tweet:
        qt = tweet.quoted_tweet
        body_parts.append("")
        body_parts.append(f"┌── Quoted @{qt.author.screen_name} ──")
        body_parts.append(qt.text)

    body_parts.append("")
    body_parts.append(
        f"❤️ {format_number(tweet.metrics.likes)}  🔄 {format_number(tweet.metrics.retweets)}  💬 {format_number(tweet.metrics.replies)}  🔖 {format_number(tweet.metrics.bookmarks)}  👁️ {format_number(tweet.metrics.views)}"
    )
    local_time = format_local_time(tweet.created_at)
    rel_time = format_relative_time(tweet.created_at)
    body_parts.append(
        f"🕐 {local_time} ({rel_time}) · https://x.com/{tweet.author.screen_name}/status/{tweet.id}"
    )

    console.print(
        Panel(
            "\n".join(body_parts),
            title=header,
            border_style="blue",
            expand=True,
        )
    )


def article_to_markdown(tweet: Tweet) -> str:
    """Convert a Twitter Article tweet into a Markdown document."""
    title = tweet.article_title or "Twitter Article"
    lines = [
        f"# {title}",
        "",
        f"- Author: @{tweet.author.screen_name} ({tweet.author.name})",
        "- Published: %s" % (tweet.created_at or "unknown"),
        f"- URL: https://x.com/{tweet.author.screen_name}/status/{tweet.id}",
        f"- Likes: {format_number(tweet.metrics.likes)}",
        f"- Retweets: {format_number(tweet.metrics.retweets)}",
        f"- Replies: {format_number(tweet.metrics.replies)}",
        f"- Bookmarks: {format_number(tweet.metrics.bookmarks)}",
        f"- Views: {format_number(tweet.metrics.views)}",
    ]

    if tweet.article_text:
        lines.extend(["", tweet.article_text.strip()])

    return "\n".join(lines).strip() + "\n"


def print_article(tweet: Tweet, console: Console | None = None) -> None:
    """Print a Twitter Article with rich formatting."""
    if console is None:
        console = _make_console()

    verified = " ✓" if tweet.author.verified else ""
    title = tweet.article_title or "Twitter Article"
    meta_parts = [
        f"By @{tweet.author.screen_name}{verified} ({tweet.author.name})",
        f"🕐 {tweet.created_at}",
        f"🔗 x.com/{tweet.author.screen_name}/status/{tweet.id}",
        "",
        f"❤️ {format_number(tweet.metrics.likes)}  🔄 {format_number(tweet.metrics.retweets)}  💬 {format_number(tweet.metrics.replies)}  🔖 {format_number(tweet.metrics.bookmarks)}  👁️ {format_number(tweet.metrics.views)}",
    ]
    console.print(
        Panel(
            "\n".join(meta_parts),
            title=f"📰 {title}",
            border_style="blue",
            expand=True,
        )
    )

    if tweet.article_text:
        console.print()
        console.print(Markdown(tweet.article_text))


def print_filter_stats(
    original_count: int,
    filtered: list[Tweet],
    console: Console | None = None,
) -> None:
    """Print filter statistics."""
    if console is None:
        console = _make_console()

    console.print("📊 Filter: %d → %d tweets" % (original_count, len(filtered)))
    if filtered:
        top_score = filtered[0].score or 0.0
        bottom_score = filtered[-1].score or 0.0
        console.print(f"   Score range: {bottom_score:.1f} ~ {top_score:.1f}")


def print_user_profile(user: UserProfile, console: Console | None = None) -> None:
    """Print user profile as a rich panel."""
    if console is None:
        console = _make_console()

    verified = " ✓" if user.verified else ""
    header = f"@{user.screen_name}{verified} ({user.name})"

    lines = []
    if user.bio:
        lines.append(user.bio)
        lines.append("")

    if user.location:
        lines.append(f"📍 {user.location}")
    if user.url:
        lines.append(f"🔗 {user.url}")
    if user.location or user.url:
        lines.append("")

    lines.append(
        f"👥 {format_number(user.followers_count)} followers · {format_number(user.following_count)} following · {format_number(user.tweets_count)} tweets · {format_number(user.likes_count)} likes"
    )

    if user.created_at:
        lines.append(f"📅 Joined {user.created_at}")
    lines.append(f"🔗 x.com/{user.screen_name}")

    console.print(
        Panel(
            "\n".join(lines),
            title=header,
            border_style="cyan",
            expand=True,
        )
    )


def print_user_table(
    users: list[UserProfile],
    console: Console | None = None,
    title: str | None = None,
) -> None:
    """Print a list of users as a rich table."""
    if console is None:
        console = _make_console()

    if not title:
        title = "👥 Users — %d" % len(users)

    table = Table(title=title, show_lines=True, expand=True)
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("User", style="cyan", width=20, no_wrap=True)
    table.add_column("Bio", ratio=3)
    table.add_column("Stats", style="green", width=22, no_wrap=True)

    for i, user in enumerate(users):
        verified = " ✓" if user.verified else ""
        user_text = f"@{user.screen_name}{verified}\n{user.name}"

        bio = (user.bio or "").replace("\n", " ").strip()
        if len(bio) > 100:
            bio = bio[:97] + "..."

        stats = f"👥 {format_number(user.followers_count)} followers\n📝 {format_number(user.following_count)} following"

        table.add_row(str(i + 1), user_text, bio, stats)

    console.print(table)
