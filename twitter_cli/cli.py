"""CLI entry point for twitter-lyr.

Read commands:
    twitter-lyr feed                      # home timeline (For You)
    twitter-lyr feed -t following         # following feed
    twitter-lyr bookmarks                 # bookmarks
    twitter-lyr bookmarks folders         # list bookmark folders
    twitter-lyr bookmarks folders <id>    # tweets in a folder
    twitter-lyr search "query"            # search tweets
    twitter-lyr search "query" --from user  # advanced search
    twitter-lyr user elonmusk             # user profile
    twitter-lyr user-posts elonmusk       # user tweets
    twitter-lyr likes elonmusk            # user likes
    twitter-lyr tweet <id>                # tweet detail + replies
    twitter-lyr article <id>              # Twitter Article as Markdown
    twitter-lyr list <id>                 # list timeline
    twitter-lyr followers <handle>        # followers list
    twitter-lyr following <handle>        # following list
    twitter-lyr whoami                    # current user profile

Write commands:
    twitter-lyr post "text"               # post a tweet
    twitter-lyr post "text" -i photo.jpg  # post with image(s)
    twitter-lyr reply <id> "text"         # reply to a tweet
    twitter-lyr quote <id> "text"         # quote-tweet
    twitter-lyr delete <id>               # delete a tweet
    twitter-lyr like/unlike <id>          # like/unlike
    twitter-lyr bookmark/unbookmark <id>  # bookmark/unbookmark
    twitter-lyr retweet/unretweet <id>    # retweet/unretweet
    twitter-lyr follow/unfollow <handle>  # follow/unfollow
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
import urllib.parse
from collections.abc import Callable
from datetime import UTC
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional  # noqa: F401 (used in # type: comments)

import click
import yaml
from rich.console import Console

from . import __version__
from .auth import get_cookies
from .cache import resolve_cached_tweet, save_tweet_cache

if TYPE_CHECKING:
    from .oauth import AppOnlyToken, OAuth1Tokens  # noqa: F401 (used in # type: comments)

from .client import TwitterClient
from .config import load_config
from .exceptions import TwitterError
from .filter import filter_tweets
from .formatter import (
    article_to_markdown,
    print_article,
    print_filter_stats,
    print_tweet_detail,
    print_tweet_table,
    print_user_profile,
    print_user_table,
)
from .models import Tweet, UserProfile
from .output import (
    default_structured_format,
    emit_empty_state,
    emit_error,
    emit_structured,
    emit_toon,
    ensure_utf8_streams,
    error_payload,
    structured_output_options,
    success_payload,
    use_rich_output,
)
from .serialization import (
    tweet_to_dict,
    tweets_from_json,
    tweets_to_compact_json,
    tweets_to_data,
    tweets_to_json,
    user_profile_to_dict,
    users_to_data,
)

ConfigDict = dict[str, Any]
TweetList = list[Tweet]
FetchTweets = Callable[[int], TweetList]
OptionalPath = str | None
StructuredMode = str | None
WritePayload = dict[str, Any]
WriteOperation = Callable[[TwitterClient], WritePayload]

logger = logging.getLogger(__name__)
console = Console(stderr=True)
FEED_TYPES = ["for-you", "following"]
SEARCH_PRODUCTS = ["Top", "Latest", "Photos", "Videos"]
SEARCH_HAS_CHOICES = ["links", "images", "videos", "media"]
SEARCH_EXCLUDE_CHOICES = ["retweets", "replies", "links"]


def _agent_user_profile(profile: UserProfile) -> dict:
    """Normalize a Twitter/X profile for structured agent output."""
    data = user_profile_to_dict(profile)
    return {
        "id": data["id"],
        "name": data["name"],
        "username": data["screenName"],
        "screenName": data["screenName"],
        "bio": data["bio"],
        "location": data["location"],
        "url": data["url"],
        "followers": data["followers"],
        "following": data["following"],
        "tweets": data["tweets"],
        "likes": data["likes"],
        "verified": data["verified"],
        "profileImageUrl": data["profileImageUrl"],
        "createdAt": data["createdAt"],
    }


def _setup_logging(verbose):
    # type: (bool) -> None
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _install_session_hook_and_exit():
    # type: () -> None
    """Install session hooks for Claude Code/Codex (AXI §7)."""
    import json
    import shutil
    from pathlib import Path

    bin_path = shutil.which("twitter-lyr") or "twitter-lyr"
    home_dir = Path.home()

    hooks_installed = []

    # Claude Code: ~/.claude/settings.json
    claude_settings = home_dir / ".claude" / "settings.json"
    try:
        if claude_settings.exists():
            with open(claude_settings) as f:
                settings = json.load(f)
        else:
            settings = {}
        hooks = settings.get("hooks", {})
        session_start = hooks.get("SessionStart", [])
        already = any(
            h.get("command") == f"{bin_path} feed --format toon"
            for h in session_start
            if isinstance(h, dict)
        )
        if not already:
            session_start.append({"command": f"{bin_path} feed --format toon"})
            hooks["SessionStart"] = session_start
            settings["hooks"] = hooks
            claude_settings.parent.mkdir(parents=True, exist_ok=True)
            with open(claude_settings, "w") as f:
                json.dump(settings, f, indent=2)
            hooks_installed.append("Claude Code")
    except Exception as e:
        console.print(f"[red]Claude Code hook: {e}[/red]")

    # Codex: ~/.codex/hooks.json
    codex_hooks = home_dir / ".codex" / "hooks.json"
    try:
        if codex_hooks.exists():
            with open(codex_hooks) as f:
                hooks = json.load(f)
        else:
            hooks = {}
        session_start = hooks.get("SessionStart", [])
        already = any(
            h.get("command") == f"{bin_path} feed --format toon"
            for h in session_start
            if isinstance(h, dict)
        )
        if not already:
            session_start.append({"command": f"{bin_path} feed --format toon"})
            hooks["SessionStart"] = session_start
            codex_hooks.parent.mkdir(parents=True, exist_ok=True)
            with open(codex_hooks, "w") as f:
                json.dump(hooks, f, indent=2)
            hooks_installed.append("Codex")
    except Exception as e:
        console.print(f"[red]Codex hook: {e}[/red]")

    if hooks_installed:
        console.print(f"[green]✅ Installed hooks for: {', '.join(hooks_installed)}[/green]")
    else:
        console.print("[yellow]⚠️  Hooks already installed or no supported editors found[/yellow]")
    sys.exit(0)


def _install_agent_skill_and_exit():
    # type: () -> None
    """Create installable agent skill from home view (AXI §7)."""
    import shutil
    from pathlib import Path

    shutil.which("twitter-lyr") or "twitter-lyr"
    skill_dir = Path.home() / ".claude" / "skills" / "twitter-lyr"
    skill_dir.mkdir(parents=True, exist_ok=True)

    skill_content = """name: Twitter/X CLI
description: Twitter/X automation with timeline reading, search, posting, and engagement features
triggers:
  - "twitter-lyr post"
  - "twitter-lyr search"
  - "twitter-lyr timeline"
  - "twitter-lyr automation"
  - "social media posting"
  - "content creation"
  - "x twitter-lyr"

## Overview
Twitter CLI provides comprehensive Twitter/X automation:
- Timeline reading (home, following, bookmarks)
- Search tweets and users
- Post tweets, replies, and quote tweets
- Engagement (like, retweet, bookmark, follow)
- User profiles and analytics
- Article and media support

## Quick Start
```bash
# Show home timeline
twitter-lyr feed

# Search tweets
twitter-lyr search "query"

# Post a tweet
twitter-lyr post "Hello world"

# Get user profile
twitter-lyr user elonmusk

# Get tweet details
twitter-lyr tweet 1234567890
```

## Commands

### Reading
- `twitter-lyr feed` - Home timeline (For You)
- `twitter-lyr feed -t following` - Following feed
- `twitter-lyr bookmarks` - Bookmarks
- `twitter-lyr search "query"` - Search tweets
- `twitter-lyr user <handle>` - User profile
- `twitter-lyr user-posts <handle>` - User tweets
- `twitter-lyr tweet <id>` - Tweet detail + replies
- `twitter-lyr list <id>` - List timeline

### Writing
- `twitter-lyr post "text"` - Post a tweet
- `twitter-lyr post "text" -i photo.jpg` - Post with image(s)
- `twitter-lyr reply <id> "text"` - Reply to a tweet
- `twitter-lyr quote <id> "text"` - Quote-tweet
- `twitter-lyr delete <id>` - Delete a tweet
- `twitter-lyr like/unlike <id>` - Like/unlike
- `twitter-lyr retweet/unretweet <id>` - Retweet/unretweet
- `twitter-lyr follow/unfollow <handle>` - Follow/unfollow

### Output Formats
- `--format toon` - TOON format (default, token-efficient)
- `--format json` - JSON format
- `--format yaml` - YAML format
- `--format table` - Rich table format
- `--fields id,author,text` - Custom field selection
- `--full-text` - Show full tweet text (no truncation)

## Session Integration
Twitter CLI supports ObscuraCookieManager for browser cookie extraction:
```bash
# Automatic cookie extraction from browser
twitter-lyr feed
```

The CLI will automatically extract cookies from your browser when needed.

## Filtering
Enable score-based filtering:
```bash
twitter-lyr feed --filter
```

Configure filters in `~/.twitter-lyr/config.yaml`:
```yaml
filter:
  min_score: 50
  max_age_hours: 24
```
"""

    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(skill_content)

    console.print(f"[green]✅ Agent skill installed to: {skill_file}[/green]")
    console.print("[green]   Will load automatically on Twitter-related tasks[/green]")
    sys.exit(0)


def _load_tweets_from_json(path):
    # type: (str) -> list[Tweet]
    """Load tweets from a JSON file (previously exported)."""
    file_path = Path(path)
    if not file_path.exists():
        raise RuntimeError(f"Input file not found: {path}")

    try:
        raw = file_path.read_text(encoding="utf-8")
        return tweets_from_json(raw)
    except (ValueError, OSError) as exc:
        raise RuntimeError(f"Invalid tweet JSON file {path}: {exc}") from exc


def _get_client(config=None, quiet=False):
    # type: (Optional[dict[str, Any]], bool) -> TwitterClient
    """Create an authenticated API client."""
    if not quiet:
        console.print("\n🔐 Getting Twitter cookies...")
    cookies = get_cookies()
    rate_limit_config = (config or {}).get("rateLimit")
    return TwitterClient(
        cookies["auth_token"],
        cookies["ct0"],
        rate_limit_config,
        cookie_string=cookies.get("cookie_string"),
    )


def _error_code_from_exc(exc: Exception) -> str:
    """Extract structured error code from an exception."""
    return getattr(exc, "error_code", "api_error")


def _exit_with_error(exc: Exception) -> None:
    if emit_error(_error_code_from_exc(exc), str(exc)):
        sys.exit(1)
    console.print(f"[red]❌ {exc}[/red]")
    sys.exit(1)


def _run_guarded(action):
    # type: (Callable[[], Any]) -> Any
    try:
        return action()
    except (TwitterError, RuntimeError) as exc:
        _exit_with_error(exc)


def _resolve_fetch_count(max_count, configured):
    # type: (Optional[int], int) -> int
    """Resolve fetch count with bounds checks."""
    if max_count is not None:
        if max_count <= 0:
            raise RuntimeError("--max must be greater than 0")
        return max_count
    return max(configured, 1)


def _resolve_configured_count(config, max_count):
    # type: (dict, Optional[int]) -> int
    return _resolve_fetch_count(max_count, config.get("fetch", {}).get("count", 50))


def _normalize_tweet_id(value):
    # type: (str) -> str
    """Extract a numeric tweet ID from raw input or a full X/Twitter URL."""
    raw = value.strip()
    if not raw:
        raise RuntimeError("Tweet ID or URL is required")

    parsed = urllib.parse.urlparse(raw)
    candidate = raw
    if parsed.scheme and parsed.netloc:
        path = parsed.path.rstrip("/")
        match = re.search(r"/(?:status|article)/(\d+)$", path)
        if not match:
            raise RuntimeError(f"Invalid tweet URL: {value}")
        candidate = match.group(1)
    else:
        candidate = raw.rstrip("/").split("/")[-1]
        candidate = candidate.split("?", 1)[0].split("#", 1)[0]

    if not candidate.isdigit():
        raise RuntimeError(f"Invalid tweet ID: {value}")
    return candidate


def _apply_filter(tweets, do_filter, config, rich_output=True):
    # type: (list[Tweet], bool, dict, bool) -> list[Tweet]
    """Optionally apply tweet filtering."""
    if not do_filter:
        return tweets
    filter_config = config.get("filter", {})
    original_count = len(tweets)
    filtered = filter_tweets(tweets, filter_config)
    if rich_output:
        print_filter_stats(original_count, filtered, console)
        console.print()
    return filtered


def _structured_mode(as_json: bool, as_yaml: bool) -> StructuredMode:
    return default_structured_format(as_json=as_json, as_yaml=as_yaml)


def _emit_mode_payload(payload: object, mode: StructuredMode) -> bool:
    if not mode:
        return False
    emit_structured(payload, as_json=(mode == "json"), as_yaml=(mode == "yaml"))
    return True


def _print_lines(lines: list[str], mode: StructuredMode) -> None:
    if mode:
        return
    for line in lines:
        console.print(line)


def _handle_structured_runtime_error(
    exc: Exception,
    *,
    mode: StructuredMode,
    details: dict[str, Any] | None = None,
) -> None:
    if _emit_mode_payload(
        error_payload(_error_code_from_exc(exc), str(exc), details=details),
        mode,
    ):
        raise SystemExit(1) from None
    _exit_with_error(exc)


def _run_write_command(
    *,
    as_json: bool,
    as_yaml: bool,
    operation: WriteOperation,
    progress_lines: list[str] | None = None,
    success_lines: list[str] | None = None,
    error_details: dict[str, Any] | None = None,
) -> WritePayload | None:
    mode = _structured_mode(as_json=as_json, as_yaml=as_yaml)
    try:
        client = _get_client(load_config())
        _print_lines(progress_lines or [], mode)
        payload = operation(client)
    except (TwitterError, RuntimeError) as exc:
        _handle_structured_runtime_error(exc, mode=mode, details=error_details)
        return None

    if _emit_mode_payload(payload, mode):
        return payload

    _print_lines(success_lines or ["[green]✅ Done.[/green]"], mode)
    return payload


@click.group(
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"], "ignore_unknown_options": False},
)
@click.version_option(version=__version__, prog_name="twitter-lyr")
@click.option(
    "--config", "-C", type=click.Path(exists=True, path_type=Path), help="Config file path."
)
@click.option(
    "--compact/--full",
    "-c/-F",
    default=False,
    help="Compact output or full output (15 fields).",
)
@click.option("--full-text", is_flag=True, help="Show full tweet text (no truncation).")
@click.option("--debug", is_flag=True, help="Enable debug logging.")
@click.option("--quiet", "-q", is_flag=True, help="Suppress progress output.")
@click.option(
    "--fields",
    help="Comma-separated list of fields to include in output (e.g., id,author,text,likes,time).",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json", "yaml", "toon"]),
    default="toon",
    help="Output format. toon (default)",
)
@click.option("--verbose", "-V", is_flag=True, help="Enable debug logging.")
@click.option(
    "--install-hook",
    is_flag=True,
    help="Install session hooks for ambient context in Claude Code/Codex",
)
@click.option(
    "--install-skill", is_flag=True, help="Create installable agent skill for Claude Code"
)
@click.pass_context
def cli(
    ctx,
    config,
    compact,
    full_text,
    debug,
    quiet,
    fields,
    output_format,
    verbose,
    install_hook,
    install_skill,
):
    # type: (Any, Optional[Path], bool, bool, bool, bool, Optional[str], str, bool, bool, bool) -> None
    """Twitter/X CLI — read timelines, search, post, and more."""
    ensure_utf8_streams()
    _setup_logging(verbose or debug)
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config
    ctx.obj["compact"] = compact
    ctx.obj["full_text"] = full_text
    ctx.obj["debug"] = debug
    ctx.obj["quiet"] = quiet
    ctx.obj["fields"] = fields.split(",") if fields else None
    ctx.obj["output_format"] = output_format

    # Handle AXI flags first (these exit)
    if install_hook:
        _install_session_hook_and_exit()
    if install_skill:
        _install_agent_skill_and_exit()

    # Content-first: if no subcommand invoked, show home timeline
    if ctx.invoked_subcommand is None:
        if output_format not in ("json", "yaml"):
            import shutil

            bin_path = shutil.which("twitter-lyr") or "twitter-lyr"
            console.print(f"bin: {bin_path}")
            console.print("description: Twitter/X CLI — read timelines, search, post, and more")
            console.print()
        ctx.invoke(ctx.command.commands["feed"])


def _fetch_and_display(
    ctx,
    fetch_fn,
    label,
    emoji,
    max_count,
    as_json,
    as_yaml,
    as_toon,
    output_file,
    do_filter,
    config=None,
    compact=False,
    full_text=False,
    hint=None,
):
    # type: (Any, Any, str, str, Optional[int], bool, bool, bool, Optional[str], bool, Optional[dict], bool, bool, Optional[str]) -> None
    """Common fetch-filter-display logic for timeline-like commands."""
    if config is None:
        config = load_config()
    rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml, compact=compact)
    try:
        fetch_count = _resolve_configured_count(config, max_count)
        if rich_output:
            console.print("%s Fetching %s (%d tweets)...\n" % (emoji, label, fetch_count))
        start = time.time()
        tweets = fetch_fn(fetch_count)
        elapsed = time.time() - start
        if rich_output:
            console.print("✅ Fetched %d %s in %.1fs\n" % (len(tweets), label, elapsed))
    except (TwitterError, RuntimeError) as exc:
        _exit_with_error(exc)

    filtered = _apply_filter(tweets, do_filter, config, rich_output=rich_output)

    if output_file:
        Path(output_file).write_text(tweets_to_json(filtered), encoding="utf-8")
        if rich_output:
            console.print(f"💾 Saved to {output_file}\n")

    if compact:
        # Explicit --json/--yaml/--toon flags win over the global --format
        # default ("toon") so `--json` in compact mode still emits JSON.
        output_format = ctx.obj.get("output_format", "toon")
        fmt = output_format if not (as_json or as_yaml or as_toon) else (
            "json" if as_json else "yaml" if as_yaml else "toon"
        )
        if fmt == "toon":
            from .serialization import tweet_to_compact_dict

            emit_toon([tweet_to_compact_dict(t) for t in filtered])
        else:
            click.echo(tweets_to_compact_json(filtered, ctx.obj.get("fields")))
        return

    save_tweet_cache(filtered)

    # P5 empty-state: emit structured empty result with hint
    if not filtered:
        if emit_empty_state(
            label,
            "Try `twitter-lyr feed` or `twitter-lyr search <query>` to find tweets.",
            as_json=as_json,
            as_yaml=as_yaml,
            as_toon=as_toon,
        ):
            return
        console.print(
            f"[dim]No {label} found. Try `twitter-lyr feed` or `twitter-lyr search <query>`.[/dim]"
        )
        return

    if emit_structured(
        tweets_to_data(filtered, ctx.obj.get("fields")),
        as_json=as_json,
        as_yaml=as_yaml,
        as_toon=as_toon,
    ):
        return

    print_tweet_table(
        filtered,
        console,
        title="%s %s — %d tweets" % (emoji, label, len(filtered)),
        full_text=full_text,
    )
    _print_show_hint(hint=hint)
    console.print()


def _emit_timeline_structured(tweets, next_cursor, *, as_json, as_yaml, as_toon):
    # type: (TweetList, Optional[str], bool, bool, bool) -> bool
    """Emit timeline data with pagination metadata while keeping `data` a tweet list."""
    # Pagination metadata takes precedence over the P5 empty-state: an empty
    # page that carries a nextCursor is a legitimate paged result and must
    # keep the machine-readable pagination envelope.
    if not tweets and not next_cursor:
        return bool(emit_empty_state("tweets", "Try `twitter-lyr feed` or `twitter-lyr search <query>`.", as_json=as_json, as_yaml=as_yaml, as_toon=as_toon))
    payload = success_payload(tweets_to_data(tweets))
    payload["total_fetched"] = len(tweets)
    if next_cursor:
        payload["pagination"] = {"nextCursor": next_cursor}
    return emit_structured(payload, as_json=as_json, as_yaml=as_yaml, as_toon=as_toon)


def _run_bookmarks_command(
    ctx,
    max_count,
    as_json,
    as_yaml,
    as_toon,
    output_file,
    do_filter,
    compact=False,
    full_text=False,
):
    # type: (Any, Optional[int], bool, bool, bool, Optional[str], bool, bool, bool) -> None
    config = load_config()

    def _run():
        client = _get_client(config)
        _fetch_and_display(
            ctx,
            lambda count: client.fetch_bookmarks(count),
            "bookmarks",
            "🔖",
            max_count,
            as_json,
            as_yaml,
            as_toon,
            output_file,
            do_filter,
            config,
            compact=compact,
            full_text=full_text,
        )

    _run_guarded(_run)


def _inherit_option(ctx, name, value):
    # type: (click.Context, str, Any) -> Any
    """Allow parent group options to flow into subcommands when omitted locally."""
    if value is not None:
        return value
    parent = getattr(ctx, "parent", None)
    if parent is None:
        return value
    return parent.params.get(name)


def _inherit_flag(ctx, name, value):
    # type: (click.Context, str, bool) -> bool
    parent = getattr(ctx, "parent", None)
    if parent is None:
        return value
    return bool(value or parent.params.get(name, False))


@cli.command(name="feed")
@click.option(
    "--type",
    "-t",
    "feed_type",
    type=click.Choice(FEED_TYPES),
    default="for-you",
    help="Feed type: for-you (algorithmic) or following (chronological).",
)
@click.option(
    "--max", "-n", "max_count", type=int, default=None, help="Max number of tweets to fetch."
)
@click.option(
    "--cursor",
    type=str,
    default=None,
    help="Pagination cursor for continuing a previous feed request.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json", "yaml", "toon"]),
    default=None,
    help="Output format (overrides global --format).",
)
@click.option(
    "--fields",
    type=str,
    default=None,
    help="Comma-separated list of fields to include (overrides global --fields).",
)
@structured_output_options
@click.option(
    "--input", "-i", "input_file", type=str, default=None, help="Load tweets from JSON file."
)
@click.option(
    "--output",
    "-o",
    "output_file",
    type=str,
    default=None,
    help="Save filtered tweets to JSON file.",
)
@click.option("--filter", "do_filter", is_flag=True, help="Enable score-based filtering.")
@click.option("--full-text", is_flag=True, help="Show full tweet text in table output.")
@click.option(
    "--include-promoted/--no-include-promoted",
    default=False,
    help="Include promoted tweets when the timeline endpoint exposes them.",
)
@click.pass_context
def feed_cmd(
    ctx,
    feed_type,
    max_count,
    cursor,
    as_json,
    as_yaml,
    as_toon,
    output_format,
    fields,
    input_file,
    output_file,
    do_filter,
    full_text,
    include_promoted,
):
    # type: (Any, str, Optional[int], Optional[str], bool, bool, bool, Optional[str], Optional[str], Optional[str], Optional[str], bool, bool, bool) -> None
    """Fetch home timeline with optional filtering."""
    compact = ctx.obj.get("compact", False)
    rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml, compact=compact)
    next_cursor = None  # type: Optional[str]
    config = load_config()
    try:
        if input_file:
            if rich_output:
                console.print(f"📂 Loading tweets from {input_file}...")
            tweets = _load_tweets_from_json(input_file)
            if rich_output:
                console.print("   Loaded %d tweets" % len(tweets))
        else:
            fetch_count = _resolve_configured_count(config, max_count)
            client = _get_client(config, quiet=not rich_output)
            label = "following feed" if feed_type == "following" else "home timeline"
            if rich_output:
                console.print("📡 Fetching %s (%d tweets)...\n" % (label, fetch_count))
            start = time.time()
            if feed_type == "following":
                tweets, next_cursor = client.fetch_following_feed(
                    fetch_count,
                    include_promoted=include_promoted,
                    cursor=cursor,
                    return_cursor=True,
                )
            else:
                tweets, next_cursor = client.fetch_home_timeline(
                    fetch_count,
                    include_promoted=include_promoted,
                    cursor=cursor,
                    return_cursor=True,
                )
            elapsed = time.time() - start
            if rich_output:
                console.print("✅ Fetched %d tweets in %.1fs\n" % (len(tweets), elapsed))
    except (TwitterError, RuntimeError) as exc:
        _exit_with_error(exc)

    filtered = _apply_filter(tweets, do_filter, config, rich_output=rich_output)

    if output_file:
        Path(output_file).write_text(tweets_to_json(filtered), encoding="utf-8")
        if rich_output:
            console.print(f"💾 Saved filtered tweets to {output_file}\n")

    if compact:
        # Explicit --json/--yaml/--toon flags win over the global --format
        # default ("toon") so `--json` in compact mode still emits JSON.
        output_format = ctx.obj.get("output_format", "toon")
        fmt = output_format if not (as_json or as_yaml or as_toon) else (
            "json" if as_json else "yaml" if as_yaml else "toon"
        )
        if fmt == "toon":
            from .serialization import tweet_to_compact_dict

            emit_toon([tweet_to_compact_dict(t) for t in filtered])
        else:
            click.echo(tweets_to_compact_json(filtered, ctx.obj.get("fields")))
        return

    save_tweet_cache(filtered)

    if _emit_timeline_structured(
        filtered, next_cursor, as_json=as_json, as_yaml=as_yaml, as_toon=as_toon
    ):
        return

    title = "👥 Following" if feed_type == "following" else "📱 Twitter"
    title += " — %d tweets" % len(filtered)
    print_tweet_table(filtered, console, title=title, full_text=full_text)
    _print_show_hint(hint="Use `twitter-lyr search` for specific queries")
    console.print()


@cli.command()
@click.option(
    "--max", "-n", "max_count", type=int, default=None, help="Max number of tweets to fetch."
)
@structured_output_options
@click.option(
    "--output", "-o", "output_file", type=str, default=None, help="Save tweets to JSON file."
)
@click.option("--filter", "do_filter", is_flag=True, help="Enable score-based filtering.")
@click.option("--full-text", is_flag=True, help="Show full tweet text in table output.")
@click.pass_context
def favorites(ctx, max_count, as_json, as_yaml, as_toon, output_file, do_filter, full_text):
    # type: (Any, Optional[int], bool, bool, bool, Optional[str], bool, bool) -> None
    """Fetch bookmarked (favorite) tweets."""
    _run_bookmarks_command(
        ctx,
        max_count,
        as_json,
        as_yaml,
        as_toon,
        output_file,
        do_filter,
        compact=ctx.obj.get("compact", False),
        full_text=full_text,
    )


@cli.group(name="bookmarks", invoke_without_command=True)
@click.option(
    "--max", "-n", "max_count", type=int, default=None, help="Max number of tweets to fetch."
)
@structured_output_options
@click.option(
    "--output", "-o", "output_file", type=str, default=None, help="Save tweets to JSON file."
)
@click.option("--filter", "do_filter", is_flag=True, help="Enable score-based filtering.")
@click.option("--full-text", is_flag=True, help="Show full tweet text in table output.")
@click.pass_context
def bookmarks(ctx, max_count, as_json, as_yaml, as_toon, output_file, do_filter, full_text):
    # type: (Any, Optional[int], bool, bool, bool, Optional[str], bool, bool) -> None
    """Fetch bookmarked tweets, or manage bookmark folders."""
    if ctx.invoked_subcommand is None:
        _run_bookmarks_command(
            ctx,
            max_count,
            as_json,
            as_yaml,
            as_toon,
            output_file,
            do_filter,
            compact=ctx.obj.get("compact", False),
            full_text=full_text,
        )


@bookmarks.command(name="folders")
@click.argument("folder_id", required=False, default=None)
@click.option(
    "--max", "-n", "max_count", type=int, default=None, help="Max tweets to fetch from folder."
)
@click.option(
    "--since", type=str, default=None, help="Only show tweets after this date (YYYY-MM-DD)."
)
@structured_output_options
@click.option(
    "--output", "-o", "output_file", type=str, default=None, help="Save tweets to JSON file."
)
@click.option("--filter", "do_filter", is_flag=True, help="Enable score-based filtering.")
@click.option("--full-text", is_flag=True, help="Show full tweet text in table output.")
@click.pass_context
def bookmarks_folders(
    ctx, folder_id, max_count, since, as_json, as_yaml, as_toon, output_file, do_filter, full_text
):
    # type: (Any, Optional[str], Optional[int], Optional[str], bool, bool, bool, Optional[str], bool, bool) -> None
    """List bookmark folders, or fetch tweets from a folder.

    \b
    Examples:
        twitter-lyr bookmarks folders              # list all folders
        twitter-lyr bookmarks folders <id>         # tweets in folder
        twitter-lyr bookmarks folders <id> -n 50   # max 50 tweets
        twitter-lyr bookmarks folders <id> --since 2026-01-01
    """
    compact = ctx.obj.get("compact", False)
    max_count = _inherit_option(ctx, "max_count", max_count)
    as_json = _inherit_flag(ctx, "as_json", as_json)
    as_yaml = _inherit_flag(ctx, "as_yaml", as_yaml)
    output_file = _inherit_option(ctx, "output_file", output_file)
    do_filter = _inherit_flag(ctx, "do_filter", do_filter)
    full_text = _inherit_flag(ctx, "full_text", full_text)

    if folder_id is None:
        _run_list_bookmark_folders(as_json, as_yaml, compact, output_file)
    else:
        _run_bookmark_folder_timeline(
            ctx,
            folder_id,
            max_count,
            since,
            as_json,
            as_yaml,
            as_toon,
            output_file,
            do_filter,
            compact,
            full_text,
        )


def _run_list_bookmark_folders(as_json, as_yaml, compact, output_file=None):
    # type: (bool, bool, bool, Optional[str]) -> None
    config = load_config()
    rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml, compact=compact)

    def _run():
        client = _get_client(config)
        if rich_output:
            console.print("\U0001f4c2 Fetching bookmark folders...\n")
        folders = client.fetch_bookmark_folders()
        if rich_output:
            console.print("\u2705 Found %d bookmark folders\n" % len(folders))

        from .serialization import bookmark_folders_to_data

        data = bookmark_folders_to_data(folders)

        if output_file:
            import json as _json

            Path(output_file).write_text(
                _json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            if rich_output:
                console.print(f"💾 Saved to {output_file}\n")

        if compact:
            import json as _json

            click.echo(_json.dumps(data, ensure_ascii=False, indent=2))
            return

        if emit_structured(data, as_json=as_json, as_yaml=as_yaml):
            return

        # Rich table output
        from rich.table import Table

        table = Table(title="\U0001f4c2 Bookmark Folders \u2014 %d folders" % len(folders))
        table.add_column("ID", style="dim")
        table.add_column("Name", style="bold")
        for folder in folders:
            table.add_row(folder.id, folder.name)
        console.print(table)
        console.print()

    _run_guarded(_run)


def _parse_since_date(since_str):
    # type: (str) -> Any
    """Parse a YYYY-MM-DD date string into a datetime for filtering."""
    from datetime import datetime

    try:
        return datetime.strptime(since_str, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        raise RuntimeError("Invalid --since date format. Use YYYY-MM-DD (e.g. 2026-01-15).") from None


def _filter_tweets_since(tweets, since_str):
    # type: (list[Tweet], str) -> list[Tweet]
    """Filter tweets to only those created after the given date."""
    from email.utils import parsedate_to_datetime

    cutoff = _parse_since_date(since_str)
    filtered = []
    for tweet in tweets:
        if not tweet.created_at:
            continue
        try:
            tweet_dt = parsedate_to_datetime(tweet.created_at)
            if tweet_dt >= cutoff:
                filtered.append(tweet)
        except (ValueError, TypeError):
            continue
    return filtered


def _run_bookmark_folder_timeline(
    ctx,
    folder_id,
    max_count,
    since,
    as_json,
    as_yaml,
    as_toon,
    output_file,
    do_filter,
    compact,
    full_text=False,
):
    # type: (Any, str, Optional[int], Optional[str], bool, bool, bool, Optional[str], bool, bool, bool) -> None
    config = load_config()

    def _run():
        client = _get_client(config)

        def fetch_fn(count):
            tweets = client.fetch_bookmark_folder_timeline(folder_id, count)
            if since:
                tweets = _filter_tweets_since(tweets, since)
            return tweets

        _fetch_and_display(
            ctx,
            fetch_fn,
            f"bookmark folder {folder_id}",
            "\U0001f4c2",
            max_count,
            as_json,
            as_yaml,
            as_toon,
            output_file,
            do_filter,
            config,
            compact=compact,
            full_text=full_text,
        )

    _run_guarded(_run)


@cli.command()
@click.argument("screen_name")
@structured_output_options
def user(screen_name, as_json, as_yaml, as_toon):
    # type: (str, bool, bool, bool) -> None
    """View a user's profile. SCREEN_NAME is the @handle (without @)."""
    screen_name = screen_name.lstrip("@")
    config = load_config()
    try:
        rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml)
        client = _get_client(config, quiet=not rich_output)
        if rich_output:
            console.print(f"👤 Fetching user @{screen_name}...")
        profile = client.fetch_user(screen_name)
    except (TwitterError, RuntimeError) as exc:
        _exit_with_error(exc)

    if not emit_structured(user_profile_to_dict(profile), as_json=as_json, as_yaml=as_yaml):
        console.print()
        print_user_profile(profile, console)


@cli.command("user-posts")
@click.argument("screen_name")
@click.option(
    "--max", "-n", "max_count", type=int, default=None, help="Max number of tweets to fetch."
)
@structured_output_options
@click.option(
    "--output", "-o", "output_file", type=str, default=None, help="Save tweets to JSON file."
)
@click.option("--full-text", is_flag=True, help="Show full tweet text in table output.")
@click.pass_context
def user_posts(ctx, screen_name, max_count, as_json, as_yaml, as_toon, output_file, full_text):
    # type: (Any, str, int, bool, bool, bool, Optional[str], bool) -> None
    """List a user's tweets. SCREEN_NAME is the @handle (without @)."""
    screen_name = screen_name.lstrip("@")
    compact = ctx.obj.get("compact", False)
    config = load_config()

    def _run():
        rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml, compact=compact)
        client = _get_client(config, quiet=not rich_output)
        if rich_output:
            console.print(f"👤 Fetching @{screen_name}'s profile...")
        profile = client.fetch_user(screen_name)
        _fetch_and_display(
            ctx,
            lambda count: client.fetch_user_tweets(profile.id, count),
            f"@{screen_name} tweets",
            "📝",
            max_count,
            as_json,
            as_yaml,
            as_toon,
            output_file,
            False,
            config,
            compact=compact,
            full_text=full_text,
        )

    _run_guarded(_run)


@cli.command()
@click.argument("query", default="")
@click.option(
    "--type",
    "-t",
    "product",
    type=click.Choice(SEARCH_PRODUCTS, case_sensitive=False),
    default="Top",
    help="Search tab: Top, Latest, Photos, or Videos.",
)
@click.option("--from", "from_user", type=str, default=None, help="Only tweets from this user.")
@click.option("--to", "to_user", type=str, default=None, help="Only tweets directed at this user.")
@click.option(
    "--lang", type=str, default=None, help="Filter by language (ISO code, e.g. en, fr, ja)."
)
@click.option("--since", type=str, default=None, help="Tweets since date (YYYY-MM-DD).")
@click.option("--until", type=str, default=None, help="Tweets until date (YYYY-MM-DD).")
@click.option(
    "--has",
    type=click.Choice(SEARCH_HAS_CHOICES, case_sensitive=False),
    multiple=True,
    help="Require content type (links, images, videos, media). Repeatable.",
)
@click.option(
    "--exclude",
    type=click.Choice(SEARCH_EXCLUDE_CHOICES, case_sensitive=False),
    multiple=True,
    help="Exclude content type (retweets, replies, links). Repeatable.",
)
@click.option(
    "--min-likes", type=click.IntRange(min=0), default=None, help="Minimum number of likes."
)
@click.option(
    "--min-retweets", type=click.IntRange(min=0), default=None, help="Minimum number of retweets."
)
@click.option(
    "--max", "-n", "max_count", type=int, default=None, help="Max number of tweets to fetch."
)
@structured_output_options
@click.option(
    "--output", "-o", "output_file", type=str, default=None, help="Save tweets to JSON file."
)
@click.option("--filter", "do_filter", is_flag=True, help="Enable score-based filtering.")
@click.option("--full-text", is_flag=True, help="Show full tweet text in table output.")
@click.pass_context
def search(
    ctx,
    query,
    product,
    from_user,
    to_user,
    lang,
    since,
    until,
    has,
    exclude,
    min_likes,
    min_retweets,
    max_count,
    as_json,
    as_yaml,
    as_toon,
    output_file,
    do_filter,
    full_text,
):
    # type: (Any, str, str, Optional[str], Optional[str], Optional[str], Optional[str], Optional[str], tuple, tuple, Optional[int], Optional[int], int, bool, bool, bool, Optional[str], bool, bool) -> None
    """Search tweets by QUERY string with optional advanced filters.

    QUERY is the search keywords (optional when using advanced filters).

    Advanced search examples:

    \b
      twitter-lyr search "python" --from elonmusk
      twitter-lyr search "AI" --lang en --since 2026-01-01
      twitter-lyr search "rust" --has links --min-likes 100
      twitter-lyr search --from bbc --exclude retweets
    """
    from .search import build_search_query

    try:
        composed_query = build_search_query(
            query,
            from_user=from_user,
            to_user=to_user,
            lang=lang,
            since=since,
            until=until,
            has=list(has) if has else None,
            exclude=list(exclude) if exclude else None,
            min_likes=min_likes,
            min_retweets=min_retweets,
        )
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc
    if not composed_query:
        raise click.UsageError(
            "Provide a QUERY or at least one advanced filter (e.g. --from, --lang)."
        )

    compact = ctx.obj.get("compact", False)
    config = load_config()

    def _run():
        rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml, compact=compact)
        client = _get_client(config, quiet=not rich_output)
        _fetch_and_display(
            ctx,
            lambda count: client.fetch_search(composed_query, count, product),
            f"'{composed_query}' ({product})",
            "🔍",
            max_count,
            as_json,
            as_yaml,
            as_toon,
            output_file,
            do_filter,
            config,
            compact=compact,
            full_text=full_text,
            hint="Use `twitter-lyr show <N>` for tweet details",
        )

    _run_guarded(_run)


@cli.command()
@click.argument("screen_name")
@click.option(
    "--max", "-n", "max_count", type=int, default=None, help="Max number of tweets to fetch."
)
@structured_output_options
@click.option(
    "--output", "-o", "output_file", type=str, default=None, help="Save tweets to JSON file."
)
@click.option("--filter", "do_filter", is_flag=True, help="Enable score-based filtering.")
@click.option("--full-text", is_flag=True, help="Show full tweet text in table output.")
@click.pass_context
def likes(
    ctx, screen_name, max_count, as_json, as_yaml, as_toon, output_file, do_filter, full_text
):
    # type: (Any, str, int, bool, bool, bool, Optional[str], bool, bool) -> None
    """Show tweets liked by a user. SCREEN_NAME is the @handle (without @).

    NOTE: Twitter/X made all likes private since June 2024. You can only view
    your own likes. Querying another user's likes will return empty results.
    """
    screen_name = screen_name.lstrip("@")
    compact = ctx.obj.get("compact", False)
    config = load_config()

    def _run():
        rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml, compact=compact)
        client = _get_client(config, quiet=not rich_output)
        if rich_output:
            console.print(f"👤 Fetching @{screen_name}'s profile...")
        profile = client.fetch_user(screen_name)

        # Warn if querying another user's likes (Twitter made likes private since June 2024)
        try:
            me = client.fetch_me()
            if me.screen_name.lower() != screen_name.lower():
                if rich_output:
                    console.print(
                        "\n[yellow]⚠️  Twitter/X made all likes private since June 2024. "
                        "You can only view your own likes. "
                        f"Querying @{screen_name}'s likes will likely return empty results.[/yellow]\n"
                    )
                else:
                    logger.warning(
                        "Twitter/X made likes private (June 2024). "
                        "Only your own likes are visible. @%s's likes will likely be empty.",
                        screen_name,
                    )
        except Exception:
            pass  # Don't block the command if whoami fails

        _fetch_and_display(
            ctx,
            lambda count: client.fetch_user_likes(profile.id, count),
            f"@{screen_name} likes",
            "❤️",
            max_count,
            as_json,
            as_yaml,
            as_toon,
            output_file,
            do_filter,
            config,
            compact=compact,
            full_text=full_text,
        )

    _run_guarded(_run)


@cli.command()
@click.argument("tweet_id")
@click.option("--max", "-n", "max_count", type=int, default=None, help="Max replies to fetch.")
@click.option("--full-text", is_flag=True, help="Show full reply text in table output.")
@structured_output_options
@click.pass_context
def tweet(ctx, tweet_id, max_count, full_text, as_json, as_yaml, as_toon):
    # type: (Any, str, int, bool, bool, bool, bool) -> None
    """View a tweet and its replies. TWEET_ID is the numeric tweet ID or full URL."""
    compact = ctx.obj.get("compact", False)
    tweet_id = _normalize_tweet_id(tweet_id)
    config = load_config()
    rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml, compact=compact)
    try:
        client = _get_client(config, quiet=not rich_output)
        if rich_output:
            console.print(f"🐦 Fetching tweet {tweet_id}...\n")
        start = time.time()
        tweets = client.fetch_tweet_detail(tweet_id, _resolve_configured_count(config, max_count))
        elapsed = time.time() - start
        if rich_output:
            console.print("✅ Fetched %d tweets in %.1fs\n" % (len(tweets), elapsed))
    except (TwitterError, RuntimeError) as exc:
        _exit_with_error(exc)

    _emit_tweet_detail(
        tweets,
        compact=compact,
        as_json=as_json,
        as_yaml=as_yaml,
        as_toon=as_toon,
        full_text=full_text,
    )


def _emit_tweet_detail(tweets, compact, as_json, as_yaml, as_toon, full_text):
    # type: (list, bool, bool, bool, bool, bool) -> None
    """Render tweet detail + replies in the requested output format."""
    if compact:
        click.echo(tweets_to_compact_json(tweets))
        return

    if emit_structured(tweets_to_data(tweets), as_json=as_json, as_yaml=as_yaml):
        return

    if tweets:
        print_tweet_detail(tweets[0], console)
        if len(tweets) > 1:
            console.print("\n💬 Replies:")
            print_tweet_table(
                tweets[1:],
                console,
                title="💬 Replies — %d" % (len(tweets) - 1),
                full_text=full_text,
            )
    console.print()


def _print_show_hint(hint=None):
    # type: (Optional[str]) -> None
    """Print a contextual hint about related commands."""
    msg = hint or "Use `twitter-lyr show <N>` to view tweet #N from this list."
    console.print(f"[dim]💡 {msg}[/dim]")


@cli.command()
@click.argument("index", type=click.IntRange(1))
@click.option("--max", "-n", "max_count", type=int, default=None, help="Max replies to fetch.")
@click.option("--full-text", is_flag=True, help="Show full reply text in table output.")
@click.option(
    "--output",
    "-o",
    "output_file",
    type=str,
    default=None,
    help="Save tweet detail as JSON to file.",
)
@structured_output_options
@click.pass_context
def show(ctx, index, max_count, full_text, output_file, as_json, as_yaml, as_toon):
    # type: (Any, int, Optional[int], bool, Optional[str], bool, bool, bool) -> None
    """View tweet #INDEX from the last feed/search results."""
    compact = ctx.obj.get("compact", False)

    tweet_id, cache_size = resolve_cached_tweet(index)
    if tweet_id is None:
        if cache_size == 0:
            raise click.UsageError(
                "No cached results found. Run `twitter-lyr feed`, `twitter-lyr search`, "
                "`twitter-lyr bookmarks`, or another list command first."
            )
        raise click.UsageError(
            "Index %d is out of range (cache has %d tweets)." % (index, cache_size)
        )

    config = load_config()
    rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml, compact=compact)
    try:
        client = _get_client(config, quiet=not rich_output)
        if rich_output:
            console.print("🐦 Fetching tweet #%d (id: %s)...\n" % (index, tweet_id))
        start = time.time()
        tweets = client.fetch_tweet_detail(tweet_id, _resolve_configured_count(config, max_count))
        elapsed = time.time() - start
        if rich_output:
            console.print("✅ Fetched %d tweets in %.1fs\n" % (len(tweets), elapsed))
    except (TwitterError, RuntimeError) as exc:
        _exit_with_error(exc)

    if output_file:
        Path(output_file).write_text(tweets_to_json(tweets), encoding="utf-8")
        if rich_output:
            console.print(f"💾 Saved to {output_file}\n")

    _emit_tweet_detail(
        tweets,
        compact=compact,
        as_json=as_json,
        as_yaml=as_yaml,
        as_toon=as_toon,
        full_text=full_text,
    )


@cli.command()
@click.argument("tweet_id")
@structured_output_options
@click.option("--markdown", "-m", "as_markdown", is_flag=True, help="Output article as Markdown.")
@click.option(
    "--output", "-o", "output_file", type=str, default=None, help="Save article Markdown to file."
)
@click.pass_context
def article(ctx, tweet_id, as_json, as_yaml, as_toon, as_markdown, output_file):
    # type: (Any, str, bool, bool, bool, bool, Optional[str]) -> None
    """Fetch a Twitter Article. TWEET_ID is the numeric tweet ID or full URL."""
    # Compact is an explicit opt-in (`-c`); article rendering has no compact
    # form, so reject it when the user explicitly requests it.
    compact = ctx.obj.get("compact", False)
    if compact:
        raise click.UsageError(
            "`twitter-lyr article` does not support --compact. Use --markdown or --output."
        )
    if as_markdown and (as_json or as_yaml):
        raise click.UsageError("Use only one of --markdown, --json, or --yaml.")

    tweet_id = _normalize_tweet_id(tweet_id)
    config = load_config()
    mode = _structured_mode(as_json=as_json, as_yaml=as_yaml)
    rich_output = (mode is None) and not as_markdown
    try:
        client = _get_client(config, quiet=not rich_output)
        if rich_output:
            console.print(f"📰 Fetching article {tweet_id}...\n")
        start = time.time()
        article_tweet = client.fetch_article(tweet_id)
        elapsed = time.time() - start
        if rich_output:
            console.print(f"✅ Fetched article in {elapsed:.1f}s\n")
    except (TwitterError, RuntimeError) as exc:
        _exit_with_error(exc)

    article_data = tweet_to_dict(article_tweet)
    markdown = article_to_markdown(article_tweet)
    if output_file:
        if as_markdown or mode is None:
            rendered_output = markdown
        elif mode == "json":
            rendered_output = json.dumps(article_data, ensure_ascii=False, indent=2)
        else:
            rendered_output = yaml.safe_dump(
                article_data,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            )
        Path(output_file).write_text(rendered_output, encoding="utf-8")
        if rich_output:
            console.print(f"💾 Saved article output to {output_file}\n")

    if as_markdown:
        click.echo(markdown, nl=False)
        return
    if emit_structured(article_data, as_json=as_json, as_yaml=as_yaml):
        return

    print_article(article_tweet, console)
    console.print()


@cli.command(name="list-timeline")
@click.argument("list_id")
@click.option("--max", "-n", "max_count", type=int, default=None, help="Max tweets to fetch.")
@click.option(
    "--cursor",
    type=str,
    default=None,
    help="Pagination cursor for continuing a previous list request.",
)
@structured_output_options
@click.option("--filter", "do_filter", is_flag=True, help="Enable score-based filtering.")
@click.option("--full-text", is_flag=True, help="Show full tweet text in table output.")
@click.pass_context
def list_timeline(ctx, list_id, max_count, cursor, as_json, as_yaml, as_toon, do_filter, full_text):
    # type: (Any, str, int, Optional[str], bool, bool, bool, bool, bool) -> None
    """Fetch tweets from a Twitter List. LIST_ID is the numeric list ID."""
    compact = ctx.obj.get("compact", False)
    config = load_config()
    rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml, compact=compact)

    def _run():
        client = _get_client(config)
        try:
            fetch_count = _resolve_configured_count(config, max_count)
            if rich_output:
                console.print("📋 Fetching list %s (%d tweets)...\n" % (list_id, fetch_count))
            start = time.time()
            tweets, next_cursor = client.fetch_list_timeline(
                list_id,
                fetch_count,
                cursor=cursor,
                return_cursor=True,
            )
            elapsed = time.time() - start
            if rich_output:
                console.print("✅ Fetched %d list %s in %.1fs\n" % (len(tweets), list_id, elapsed))
        except (TwitterError, RuntimeError) as exc:
            _exit_with_error(exc)

        filtered = _apply_filter(tweets, do_filter, config, rich_output=rich_output)

        if compact:
            if ctx.obj.get("output_format") == "toon":
                from .serialization import tweet_to_compact_dict

                emit_toon([tweet_to_compact_dict(t) for t in filtered])
            else:
                click.echo(tweets_to_compact_json(filtered, ctx.obj.get("fields")))
            return

        save_tweet_cache(filtered)

        if _emit_timeline_structured(
            filtered, next_cursor, as_json=as_json, as_yaml=as_yaml, as_toon=as_toon
        ):
            return

        print_tweet_table(
            filtered,
            console,
            title="📋 list %s — %d tweets" % (list_id, len(filtered)),
            full_text=full_text,
        )
        _print_show_hint()
        console.print()

    _run_guarded(_run)


def _fetch_and_display_users(
    screen_name: str,
    fetch_fn_name: str,
    label: str,
    max_count: int | None,
    as_json: bool,
    as_yaml: bool,
) -> None:
    """Shared fetch-and-display logic for followers/following commands."""
    screen_name = screen_name.lstrip("@")
    config = load_config()
    try:
        rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml)
        client = _get_client(config, quiet=not rich_output)
        if rich_output:
            console.print(f"👤 Fetching @{screen_name}'s profile...")
        profile = client.fetch_user(screen_name)
        fetch_count = _resolve_configured_count(config, max_count)
        if rich_output:
            console.print("👥 Fetching %s (%d)...\n" % (label, fetch_count))
        start = time.time()
        users = getattr(client, fetch_fn_name)(profile.id, fetch_count)
        elapsed = time.time() - start
        if rich_output:
            console.print("✅ Fetched %d %s in %.1fs\n" % (len(users), label, elapsed))
    except (TwitterError, RuntimeError) as exc:
        _exit_with_error(exc)

    if emit_structured(users_to_data(users), as_json=as_json, as_yaml=as_yaml):
        return

    print_user_table(users, console, title="👥 @%s %s — %d" % (screen_name, label, len(users)))
    console.print()


@cli.command()
@click.argument("screen_name")
@click.option("--max", "-n", "max_count", type=int, default=None, help="Max users to fetch.")
@structured_output_options
def followers(screen_name, max_count, as_json, as_yaml, as_toon):
    # type: (str, int, bool, bool, bool) -> None
    """List followers of a user. SCREEN_NAME is the @handle (without @)."""
    _fetch_and_display_users(
        screen_name, "fetch_followers", "followers", max_count, as_json, as_yaml
    )


@cli.command()
@click.argument("screen_name")
@click.option("--max", "-n", "max_count", type=int, default=None, help="Max users to fetch.")
@structured_output_options
def following(screen_name, max_count, as_json, as_yaml, as_toon):
    # type: (str, int, bool, bool, bool) -> None
    """List accounts a user is following. SCREEN_NAME is the @handle (without @)."""
    _fetch_and_display_users(
        screen_name, "fetch_following", "following", max_count, as_json, as_yaml
    )


# ── Write commands ──────────────────────────────────────────────────────

_MAX_IMAGES = 4  # Twitter allows up to 4 images per tweet
_MAX_VIDEOS = 1  # Twitter allows 1 video per tweet


def _upload_media(client, image_paths, video_paths, rich_output=True):
    # type: (TwitterClient, tuple, tuple, bool) -> list
    """Upload images and/or videos and return list of media_id strings."""
    media_ids = []

    # Upload images
    if image_paths:
        if len(image_paths) > _MAX_IMAGES:
            raise click.UsageError(
                "Too many images: max %d, got %d" % (_MAX_IMAGES, len(image_paths))
            )
        for i, path in enumerate(image_paths, 1):
            if rich_output:
                console.print("📤 Uploading image %d/%d: %s" % (i, len(image_paths), path))
            media_ids.append(client.upload_media(path))

    # Upload videos
    if video_paths:
        if len(video_paths) > _MAX_VIDEOS:
            raise click.UsageError(
                "Too many videos: max %d, got %d" % (_MAX_VIDEOS, len(video_paths))
            )
        for i, path in enumerate(video_paths, 1):
            if rich_output:
                console.print("📤 Uploading video %d/%d: %s" % (i, len(video_paths), path))
            media_ids.append(client.upload_media(path))

    return media_ids


def _write_action(emoji, action_desc, client_method, tweet_id, as_json=False, as_yaml=False):
    # type: (str, str, str, str, bool, bool) -> None
    """Generic write action helper to reduce CLI command boilerplate.

    Emits structured JSON/YAML when piped or when OUTPUT env is set.
    """
    action_name = action_desc.lower().replace(" ", "_")

    def operation(client: TwitterClient) -> WritePayload:
        getattr(client, client_method)(tweet_id)
        return {"success": True, "action": action_name, "id": tweet_id}

    _run_write_command(
        as_json=as_json,
        as_yaml=as_yaml,
        operation=operation,
        progress_lines=[f"{emoji} {action_desc} {tweet_id}..."],
        success_lines=["[green]✅ Done.[/green]"],
        error_details={"action": action_name, "id": tweet_id},
    )


@cli.command()
@click.argument("text")
@click.option("--reply-to", "-r", default=None, help="Reply to this tweet ID.")
@click.option(
    "--image",
    "-i",
    "images",
    multiple=True,
    type=click.Path(exists=True),
    help="Attach image (up to 4). Repeatable.",
)
@click.option(
    "--video",
    "-v",
    "videos",
    multiple=True,
    type=click.Path(exists=True),
    help="Attach video (1). Repeatable.",
)
@structured_output_options
def post(text, reply_to, images, videos, as_json, as_yaml, as_toon):
    # type: (str, Optional[str], tuple, tuple, bool, bool, bool) -> None
    """Post a new tweet. TEXT is the tweet content.

    Attach images with --image / -i (up to 4):

    \b
      twitter-lyr post "Hello!" --image photo.jpg
      twitter-lyr post "Gallery" -i a.png -i b.png -i c.jpg

    Attach a video with --video / -v (only 1 video allowed):

    \b
      twitter-lyr post "Video tweet" --video clip.mp4
    """
    normalized_reply_to = _normalize_tweet_id(reply_to) if reply_to else None
    action = f"Replying to {normalized_reply_to}" if normalized_reply_to else "Posting tweet"
    rich_output = not _structured_mode(as_json=as_json, as_yaml=as_yaml)

    def operation(client: TwitterClient) -> WritePayload:
        media_ids = _upload_media(client, images, videos, rich_output=rich_output)
        tweet_id = client.create_tweet(
            text, reply_to_id=normalized_reply_to, media_ids=media_ids or None
        )
        return {
            "success": True,
            "action": "post",
            "id": tweet_id,
            "url": f"https://x.com/i/status/{tweet_id}",
        }

    payload = _run_write_command(
        as_json=as_json,
        as_yaml=as_yaml,
        operation=operation,
        progress_lines=[f"✏️  {action}..."],
        success_lines=["[green]✅ Tweet posted![/green]"],
        error_details={"action": "post", "replyTo": normalized_reply_to},
    )
    if payload and not _structured_mode(as_json=as_json, as_yaml=as_yaml):
        console.print("🔗 {}".format(payload["url"]))


@cli.command(name="reply")
@click.argument("tweet_id")
@click.argument("text")
@click.option(
    "--image",
    "-i",
    "images",
    multiple=True,
    type=click.Path(exists=True),
    help="Attach image (up to 4). Repeatable.",
)
@click.option(
    "--video",
    "-v",
    "videos",
    multiple=True,
    type=click.Path(exists=True),
    help="Attach video (1). Repeatable.",
)
@structured_output_options
def reply_tweet(tweet_id, text, images, videos, as_json, as_yaml, as_toon):
    # type: (str, str, tuple, tuple, bool, bool, bool) -> None
    """Reply to a tweet. TWEET_ID is the tweet to reply to, TEXT is the reply content."""
    tweet_id = _normalize_tweet_id(tweet_id)
    rich_output = not _structured_mode(as_json=as_json, as_yaml=as_yaml)

    def operation(client: TwitterClient) -> WritePayload:
        media_ids = _upload_media(client, images, videos, rich_output=rich_output)
        new_id = client.create_tweet(text, reply_to_id=tweet_id, media_ids=media_ids or None)
        return {
            "success": True,
            "action": "reply",
            "id": new_id,
            "replyTo": tweet_id,
            "url": f"https://x.com/i/status/{new_id}",
        }

    payload = _run_write_command(
        as_json=as_json,
        as_yaml=as_yaml,
        operation=operation,
        progress_lines=[f"💬 Replying to {tweet_id}..."],
        success_lines=["[green]✅ Reply posted![/green]"],
        error_details={"action": "reply", "replyTo": tweet_id},
    )
    if payload and not _structured_mode(as_json=as_json, as_yaml=as_yaml):
        console.print("🔗 {}".format(payload["url"]))


@cli.command(name="quote")
@click.argument("tweet_id")
@click.argument("text")
@click.option(
    "--image",
    "-i",
    "images",
    multiple=True,
    type=click.Path(exists=True),
    help="Attach image (up to 4). Repeatable.",
)
@click.option(
    "--video",
    "-v",
    "videos",
    multiple=True,
    type=click.Path(exists=True),
    help="Attach video (1). Repeatable.",
)
@structured_output_options
def quote_tweet(tweet_id, text, images, videos, as_json, as_yaml, as_toon):
    # type: (str, str, tuple, tuple, bool, bool, bool) -> None
    """Quote-tweet a tweet. TWEET_ID is the tweet to quote, TEXT is the commentary."""
    tweet_id = _normalize_tweet_id(tweet_id)
    rich_output = not _structured_mode(as_json=as_json, as_yaml=as_yaml)

    def operation(client: TwitterClient) -> WritePayload:
        media_ids = _upload_media(client, images, videos, rich_output=rich_output)
        new_id = client.quote_tweet(tweet_id, text, media_ids=media_ids or None)
        return {
            "success": True,
            "action": "quote",
            "id": new_id,
            "quotedId": tweet_id,
            "url": f"https://x.com/i/status/{new_id}",
        }

    payload = _run_write_command(
        as_json=as_json,
        as_yaml=as_yaml,
        operation=operation,
        progress_lines=[f"🔄 Quoting tweet {tweet_id}..."],
        success_lines=["[green]✅ Quote tweet posted![/green]"],
        error_details={"action": "quote", "quotedId": tweet_id},
    )
    if payload and not _structured_mode(as_json=as_json, as_yaml=as_yaml):
        console.print("🔗 {}".format(payload["url"]))


@cli.command(name="status")
@structured_output_options
def status(as_json, as_yaml, as_toon):
    # type: (bool, bool, bool) -> None
    """Check whether the current Twitter/X session is authenticated."""
    config = load_config()
    try:
        rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml)
        client = _get_client(config, quiet=not rich_output)
        profile = client.fetch_me()
    except (TwitterError, RuntimeError) as exc:
        payload = error_payload(_error_code_from_exc(exc), str(exc))
        if emit_structured(payload, as_json=as_json, as_yaml=as_yaml):
            sys.exit(1)
        _exit_with_error(exc)
        return

    payload = success_payload({"authenticated": True, "user": _agent_user_profile(profile)})
    if emit_structured(payload, as_json=as_json, as_yaml=as_yaml):
        return

    console.print("[green]✅ Authenticated.[/green]")
    console.print(f"👤 @{profile.screen_name}")


@cli.group(name="media", invoke_without_command=True)
@click.pass_context
def media(ctx):
    # type: (Any) -> None
    """Media management commands."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@media.command(name="status")
@click.argument("media_id")
@structured_output_options
def media_status(media_id, as_json, as_yaml, as_toon):
    # type: (str, bool, bool, bool) -> None
    """Check media upload processing status."""
    config = load_config()
    try:
        rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml)
        client = _get_client(config, quiet=not rich_output)
        if rich_output:
            console.print(f"📤 Checking media status for {media_id}...")
        status = client.check_media_status(media_id)
    except (TwitterError, RuntimeError) as exc:
        if emit_structured(
            error_payload(_error_code_from_exc(exc), str(exc)), as_json=as_json, as_yaml=as_yaml
        ):
            raise SystemExit(1) from None
        _exit_with_error(exc)

    if emit_structured(success_payload(status), as_json=as_json, as_yaml=as_yaml):
        return

    if rich_output:
        state = status.get("state", "unknown")
        progress = status.get("progress_percent", 0)
        check_after = status.get("check_after_secs", 5)
        error = status.get("error")

        if state == "succeeded":
            console.print("[green]✅ Media processing complete[/green]")
        elif state == "failed":
            console.print("[red]❌ Media processing failed[/red]")
            if error:
                console.print(
                    "   Error: {} (code {})".format(error.get("message", "Unknown"), error.get("code", "N/A"))
                )
        elif state == "in_progress":
            console.print("[yellow]⏳ Media processing: %d%%[/yellow]" % progress)
            console.print("   Check again in %ds" % check_after)
        else:
            console.print("[dim]❓ State: %s (%d%%)[/dim]" % (state, progress))
        console.print()


@cli.group(name="auth", invoke_without_command=True)
@click.pass_context
def auth(ctx):
    # type: (Any) -> None
    """Authentication management commands."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@auth.command(name="status")
@structured_output_options
def auth_status(as_json, as_yaml, as_toon):
    # type: (bool, bool, bool) -> None
    """Check authentication status and show current user."""
    config = load_config()
    try:
        rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml, as_toon=as_toon)
        client = _get_client(config, quiet=not rich_output)
        if rich_output:
            console.print("🔐 Checking authentication status...")
        profile = client.fetch_me()
    except (TwitterError, RuntimeError) as exc:
        if emit_structured(
            error_payload(_error_code_from_exc(exc), str(exc)), as_json=as_json, as_yaml=as_yaml, as_toon=as_toon
        ):
            raise SystemExit(1) from None
        _exit_with_error(exc)

    payload = success_payload({"authenticated": True, "user": _agent_user_profile(profile)})
    if emit_structured(payload, as_json=as_json, as_yaml=as_yaml, as_toon=as_toon):
        return

    console.print("[green]✅ Authenticated.[/green]")
    console.print(f"👤 @{profile.screen_name}")


@auth.command(name="clear")
@click.confirmation_option(prompt="Are you sure you want to clear stored authentication?")
@structured_output_options
def auth_clear(as_json, as_yaml, as_toon):
    # type: (bool, bool, bool) -> None
    """Clear stored authentication (cookies)."""
    # Clear environment variables if set
    import os

    cleared = []
    if os.environ.get("TWITTER_AUTH_TOKEN"):
        os.environ.pop("TWITTER_AUTH_TOKEN", None)
        cleared.append("TWITTER_AUTH_TOKEN")
    if os.environ.get("TWITTER_CT0"):
        os.environ.pop("TWITTER_CT0", None)
        cleared.append("TWITTER_CT0")

    payload = success_payload({"cleared": True, "variables": cleared})
    if emit_structured(payload, as_json=as_json, as_yaml=as_yaml, as_toon=as_toon):
        return

    if cleared:
        console.print("[green]✅ Cleared environment variables: {}[/green]".format(", ".join(cleared)))
    else:
        console.print("[yellow]No environment variables to clear.[/yellow]")
    console.print(
        "[dim]Note: Browser cookies are not affected. Log out from x.com in your browser to fully clear.[/dim]"
    )


@auth.command(name="login")
@click.option(
    "--oauth1",
    "auth_type",
    flag_value="oauth1",
    help="Use OAuth 1.0a (user context, requires consumer key/secret).",
)
@click.option(
    "--oauth2",
    "auth_type",
    flag_value="oauth2",
    default=True,
    help="Use OAuth 2.0 PKCE (user context, recommended).",
)
@click.option(
    "--app-only",
    "auth_type",
    flag_value="app_only",
    help="Use App-Only (client credentials, read-only).",
)
@click.option(
    "--scope",
    default="tweet.read tweet.write users.read offline.access",
    help="OAuth2 scope (for oauth2 type).",
)
@structured_output_options
def auth_login(auth_type, scope, as_json, as_yaml, as_toon):
    # type: (str, str, bool, bool, bool) -> None
    """Authenticate with Twitter using OAuth.

    Three authentication types:
      --oauth2      OAuth 2.0 PKCE (recommended, supports refresh tokens)
      --oauth1      OAuth 1.0a (legacy, requires consumer key/secret)
      --app-only    App-Only client credentials (read-only public data)

    Configure credentials via environment variables:
      OAuth 1.0a:  TWITTER_OAUTH1_CONSUMER_KEY, TWITTER_OAUTH1_CONSUMER_SECRET
      OAuth 2.0:   TWITTER_OAUTH2_CLIENT_ID, TWITTER_OAUTH2_CLIENT_SECRET
      Redirect URI: TWITTER_REDIRECT_URI (default: http://localhost:8080/callback)
    """
    import os

    from .oauth import create_oauth_manager

    oauth1_key = os.environ.get("TWITTER_OAUTH1_CONSUMER_KEY", "")
    oauth1_secret = os.environ.get("TWITTER_OAUTH1_CONSUMER_SECRET", "")
    oauth2_id = os.environ.get("TWITTER_OAUTH2_CLIENT_ID", "")
    oauth2_secret = os.environ.get("TWITTER_OAUTH2_CLIENT_SECRET", "")
    redirect_uri = os.environ.get("TWITTER_REDIRECT_URI", "http://localhost:8080/callback")

    manager = create_oauth_manager(
        oauth1_consumer_key=oauth1_key,
        oauth1_consumer_secret=oauth1_secret,
        oauth2_client_id=oauth2_id,
        oauth2_client_secret=oauth2_secret,
        redirect_uri=redirect_uri,
    )

    try:
        payload = {}  # type: dict[str, Any]
        if auth_type == "oauth2":
            if not oauth2_id or not oauth2_secret:
                raise click.UsageError(
                    "OAuth 2.0 requires TWITTER_OAUTH2_CLIENT_ID and TWITTER_OAUTH2_CLIENT_SECRET"
                )
            oauth2_tokens = manager.oauth2_run_flow(scope)
            payload = {
                "auth_type": "oauth2",
                "access_token": oauth2_tokens.access_token,
                "refresh_token": oauth2_tokens.refresh_token,
                "expires_in": oauth2_tokens.expires_in,
                "scope": oauth2_tokens.scope,
            }

        elif auth_type == "oauth1":
            if not oauth1_key or not oauth1_secret:
                raise click.UsageError(
                    "OAuth 1.0a requires TWITTER_OAUTH1_CONSUMER_KEY and TWITTER_OAUTH1_CONSUMER_SECRET"
                )
            # OAuth 1.0a flow
            oauth1_tokens = manager.oauth1_run_flow()  # type: OAuth1Tokens
            payload = {
                "auth_type": "oauth1",
                "access_token": oauth1_tokens.oauth_token,
                "access_token_secret": oauth1_tokens.oauth_token_secret,
            }

        elif auth_type == "app_only":
            if not oauth2_id or not oauth2_secret:
                raise click.UsageError(
                    "App-Only requires TWITTER_OAUTH2_CLIENT_ID and TWITTER_OAUTH2_CLIENT_SECRET"
                )
            app_only_tokens = manager.app_only_run_flow()  # type: AppOnlyToken
            payload = {
                "auth_type": "app_only",
                "access_token": app_only_tokens.access_token,
                "expires_in": app_only_tokens.expires_in,
            }

        else:
            raise click.UsageError(f"Unknown auth type: {auth_type}")

    except Exception as exc:
        if emit_structured(
            error_payload("auth_failed", str(exc)), as_json=as_json, as_yaml=as_yaml
        ):
            raise SystemExit(1) from None
        _exit_with_error(exc)

    if emit_structured(success_payload(payload), as_json=as_json, as_yaml=as_yaml):
        return

    console.print("[green]✅ Authentication successful![/green]")
    console.print("[dim]Tokens displayed above. Store them securely.[/dim]")


@auth.command(name="refresh")
@click.argument("refresh_token")
@structured_output_options
def auth_refresh(refresh_token, as_json, as_yaml, as_toon):
    # type: (str, bool, bool, bool) -> None
    """Refresh OAuth 2.0 access token using refresh token."""
    import os

    from .oauth import create_oauth_manager

    oauth2_id = os.environ.get("TWITTER_OAUTH2_CLIENT_ID", "")
    oauth2_secret = os.environ.get("TWITTER_OAUTH2_CLIENT_SECRET", "")

    if not oauth2_id or not oauth2_secret:
        raise click.UsageError(
            "OAuth 2.0 requires TWITTER_OAUTH2_CLIENT_ID and TWITTER_OAUTH2_CLIENT_SECRET"
        )

    manager = create_oauth_manager(
        oauth2_client_id=oauth2_id,
        oauth2_client_secret=oauth2_secret,
    )

    try:
        tokens = manager.oauth2_refresh_token(refresh_token)
        payload = {
            "access_token": tokens.access_token,
            "refresh_token": tokens.refresh_token,
            "expires_in": tokens.expires_in,
            "scope": tokens.scope,
        }
    except Exception as exc:
        if emit_structured(
            error_payload("refresh_failed", str(exc)), as_json=as_json, as_yaml=as_yaml
        ):
            raise SystemExit(1) from None
        _exit_with_error(exc)

    if emit_structured(success_payload(payload), as_json=as_json, as_yaml=as_yaml):
        return

    console.print("[green]✅ Token refreshed![/green]")


@cli.command(name="whoami")
@structured_output_options
def whoami(as_json, as_yaml, as_toon):
    # type: (bool, bool, bool) -> None
    """Show the currently authenticated user's profile."""
    config = load_config()
    try:
        rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml)
        client = _get_client(config, quiet=not rich_output)
        if rich_output:
            console.print("👤 Fetching current user...")
        profile = client.fetch_me()
    except (TwitterError, RuntimeError) as exc:
        if emit_structured(
            error_payload(_error_code_from_exc(exc), str(exc)), as_json=as_json, as_yaml=as_yaml
        ):
            raise SystemExit(1) from None
        _exit_with_error(exc)

    if not emit_structured(
        success_payload({"user": _agent_user_profile(profile)}), as_json=as_json, as_yaml=as_yaml
    ):
        console.print()
        print_user_profile(profile, console)


@cli.command(name="follow")
@click.argument("screen_name")
@structured_output_options
def follow_user(screen_name, as_json, as_yaml, as_toon):
    # type: (str, bool, bool, bool) -> None
    """Follow a user. SCREEN_NAME is the @handle (without @)."""
    screen_name = screen_name.lstrip("@")

    def operation(client: TwitterClient) -> WritePayload:
        user_id = client.resolve_user_id(screen_name)
        client.follow_user(user_id)
        return {"success": True, "action": "follow", "screenName": screen_name, "userId": user_id}

    _run_write_command(
        as_json=as_json,
        as_yaml=as_yaml,
        operation=operation,
        progress_lines=[f"👤 Looking up @{screen_name}...", f"➕ Following @{screen_name}..."],
        success_lines=[f"[green]✅ Now following @{screen_name}[/green]"],
        error_details={"action": "follow", "screenName": screen_name},
    )


@cli.command(name="unfollow")
@click.argument("screen_name")
@structured_output_options
def unfollow_user(screen_name, as_json, as_yaml, as_toon):
    # type: (str, bool, bool, bool) -> None
    """Unfollow a user. SCREEN_NAME is the @handle (without @)."""
    screen_name = screen_name.lstrip("@")

    def operation(client: TwitterClient) -> WritePayload:
        user_id = client.resolve_user_id(screen_name)
        client.unfollow_user(user_id)
        return {"success": True, "action": "unfollow", "screenName": screen_name, "userId": user_id}

    _run_write_command(
        as_json=as_json,
        as_yaml=as_yaml,
        operation=operation,
        progress_lines=[
            f"👤 Looking up @{screen_name}...",
            f"➖ Unfollowing @{screen_name}...",
        ],
        success_lines=[f"[green]✅ Unfollowed @{screen_name}[/green]"],
        error_details={"action": "unfollow", "screenName": screen_name},
    )


@cli.command(name="delete")
@click.argument("tweet_id")
@click.confirmation_option(prompt="Are you sure you want to delete this tweet?")
@structured_output_options
def delete_tweet(tweet_id, as_json, as_yaml, as_toon):
    # type: (str, bool, bool, bool) -> None
    """Delete a tweet. TWEET_ID is the numeric tweet ID."""
    _write_action("🗑️", "Deleting tweet", "delete_tweet", tweet_id, as_json=as_json, as_yaml=as_yaml)


@cli.command()
@click.argument("tweet_id")
@structured_output_options
def like(tweet_id, as_json, as_yaml, as_toon):
    # type: (str, bool, bool, bool) -> None
    """Like a tweet. TWEET_ID is the numeric tweet ID."""
    _write_action("❤️", "Liking tweet", "like_tweet", tweet_id, as_json=as_json, as_yaml=as_yaml)


@cli.command()
@click.argument("tweet_id")
@structured_output_options
def unlike(tweet_id, as_json, as_yaml, as_toon):
    # type: (str, bool, bool, bool) -> None
    """Unlike a tweet. TWEET_ID is the numeric tweet ID."""
    _write_action(
        "💔", "Unliking tweet", "unlike_tweet", tweet_id, as_json=as_json, as_yaml=as_yaml
    )


@cli.command()
@click.argument("tweet_id")
@structured_output_options
def retweet(tweet_id, as_json, as_yaml, as_toon):
    # type: (str, bool, bool, bool) -> None
    """Retweet a tweet. TWEET_ID is the numeric tweet ID."""
    _write_action("🔄", "Retweeting", "retweet", tweet_id, as_json=as_json, as_yaml=as_yaml)


@cli.command()
@click.argument("tweet_id")
@structured_output_options
def unretweet(tweet_id, as_json, as_yaml, as_toon):
    # type: (str, bool, bool, bool) -> None
    """Undo a retweet. TWEET_ID is the numeric tweet ID."""
    _write_action("🔄", "Undoing retweet", "unretweet", tweet_id, as_json=as_json, as_yaml=as_yaml)


@cli.command()
@click.argument("tweet_id")
@structured_output_options
def favorite(tweet_id, as_json, as_yaml, as_toon):
    # type: (str, bool, bool, bool) -> None
    """Bookmark (favorite) a tweet. TWEET_ID is the numeric tweet ID."""
    _write_action(
        "🔖", "Bookmarking tweet", "bookmark_tweet", tweet_id, as_json=as_json, as_yaml=as_yaml
    )


@cli.command()
@click.argument("tweet_id")
@structured_output_options
def bookmark(tweet_id, as_json, as_yaml, as_toon):
    # type: (str, bool, bool, bool) -> None
    """Bookmark a tweet. TWEET_ID is the numeric tweet ID."""
    _write_action(
        "🔖", "Bookmarking tweet", "bookmark_tweet", tweet_id, as_json=as_json, as_yaml=as_yaml
    )


@cli.command()
@click.argument("tweet_id")
@structured_output_options
def unfavorite(tweet_id, as_json, as_yaml, as_toon):
    # type: (str, bool, bool, bool) -> None
    """Remove a tweet from bookmarks (unfavorite). TWEET_ID is the numeric tweet ID."""
    _write_action(
        "🔖", "Removing bookmark", "unbookmark_tweet", tweet_id, as_json=as_json, as_yaml=as_yaml
    )


@cli.command()
@click.argument("tweet_id")
@structured_output_options
def unbookmark(tweet_id, as_json, as_yaml, as_toon):
    # type: (str, bool, bool, bool) -> None
    """Remove a tweet from bookmarks. TWEET_ID is the numeric tweet ID."""
    _write_action(
        "🔖", "Removing bookmark", "unbookmark_tweet", tweet_id, as_json=as_json, as_yaml=as_yaml
    )


@cli.command(name="session-install")
@click.option(
    "--shell", type=click.Choice(["bash", "zsh", "fish"]), help="Shell to install hooks for."
)
@click.option(
    "--scope", type=click.Choice(["user", "project"]), default="user", help="Installation scope."
)
@structured_output_options
@click.pass_context
def session_install(ctx, shell, scope, as_json, as_yaml, as_toon):
    """Install shell hooks for agent session integration.

    Adds a hook to run `twitter-lyr` on shell startup, providing live
    timeline context to AI agents at session start.
    """
    hooks = {
        "bash": "~/.bashrc",
        "zsh": "~/.zshrc",
        "fish": "~/.config/fish/config.fish",
    }
    hook_file = Path(hooks[shell or "bash"]).expanduser()
    hook_line = "twitter-lyr --format yaml --compact 2>/dev/null || true"

    if hook_file.exists():
        content = hook_file.read_text()
        if hook_line in content:
            payload = {
                "ok": True,
                "schema_version": "1",
                "data": {"status": "already_installed", "file": str(hook_file)},
            }
        else:
            hook_file.write_text(content + f"\n{hook_line}\n")
            payload = {
                "ok": True,
                "schema_version": "1",
                "data": {"status": "installed", "file": str(hook_file)},
            }
    else:
        hook_file.parent.mkdir(parents=True, exist_ok=True)
        hook_file.write_text(f"{hook_line}\n")
        payload = {
            "ok": True,
            "schema_version": "1",
            "data": {"status": "installed", "file": str(hook_file)},
        }

    emit_structured(payload, as_json=as_json, as_yaml=as_yaml, as_toon=as_toon)


@cli.command(name="help", context_settings={"ignore_unknown_options": True})
@click.argument("command", required=False)
@click.pass_context
def help_cmd(ctx, command):
    """Show contextual help with flag inheritance."""
    if command is None:
        click.echo(ctx.parent.get_help())
        return

    # Get the subcommand from the parent group
    parent_group = ctx.parent.command
    if command in parent_group.commands:
        subcmd = parent_group.commands[command]
        # Show command help with inherited flags
        click.echo(subcmd.get_help(ctx))
        # Show contextual suggestions
        inherited = {"--max", "--compact", "--filter", "--format", "--fields", "--full-text"}
        click.echo("\n[dim]Inherited flags:[/dim]")
        for flag in sorted(inherited):
            click.echo(f"  {flag}")
    else:
        click.echo(f"Unknown command: {command}")
        ctx.exit(1)


if __name__ == "__main__":
    cli()
# ── New write commands: Block, Mute, DM, Poll, List, Community ──────────────


@cli.command(name="block")
@click.argument("screen_name")
@structured_output_options
def block_user(screen_name, as_json, as_yaml, as_toon):
    # type: (str, bool, bool, bool) -> None
    """Block a user. SCREEN_NAME is the @handle (without @)."""
    screen_name = screen_name.lstrip("@")

    def operation(client: TwitterClient) -> WritePayload:
        user_id = client.resolve_user_id(screen_name)
        client.block_user(user_id)
        return {"success": True, "action": "block", "screenName": screen_name, "userId": user_id}

    _run_write_command(
        as_json=as_json,
        as_yaml=as_yaml,
        operation=operation,
        progress_lines=[f"👤 Looking up @{screen_name}...", f"🚫 Blocking @{screen_name}..."],
        success_lines=[f"[green]✅ Blocked @{screen_name}[/green]"],
        error_details={"action": "block", "screenName": screen_name},
    )


@cli.command(name="unblock")
@click.argument("screen_name")
@structured_output_options
def unblock_user(screen_name, as_json, as_yaml, as_toon):
    # type: (str, bool, bool, bool) -> None
    """Unblock a user. SCREEN_NAME is the @handle (without @)."""
    screen_name = screen_name.lstrip("@")

    def operation(client: TwitterClient) -> WritePayload:
        user_id = client.resolve_user_id(screen_name)
        client.unblock_user(user_id)
        return {"success": True, "action": "unblock", "screenName": screen_name, "userId": user_id}

    _run_write_command(
        as_json=as_json,
        as_yaml=as_yaml,
        operation=operation,
        progress_lines=[f"👤 Looking up @{screen_name}...", f"🔓 Unblocking @{screen_name}..."],
        success_lines=[f"[green]✅ Unblocked @{screen_name}[/green]"],
        error_details={"action": "unblock", "screenName": screen_name},
    )


@cli.command(name="mute")
@click.argument("screen_name")
@structured_output_options
def mute_user(screen_name, as_json, as_yaml, as_toon):
    # type: (str, bool, bool, bool) -> None
    """Mute a user. SCREEN_NAME is the @handle (without @)."""
    screen_name = screen_name.lstrip("@")

    def operation(client: TwitterClient) -> WritePayload:
        user_id = client.resolve_user_id(screen_name)
        client.mute_user(user_id)
        return {"success": True, "action": "mute", "screenName": screen_name, "userId": user_id}

    _run_write_command(
        as_json=as_json,
        as_yaml=as_yaml,
        operation=operation,
        progress_lines=[f"👤 Looking up @{screen_name}...", f"🔇 Muting @{screen_name}..."],
        success_lines=[f"[green]✅ Muted @{screen_name}[/green]"],
        error_details={"action": "mute", "screenName": screen_name},
    )


@cli.command(name="unmute")
@click.argument("screen_name")
@structured_output_options
def unmute_user(screen_name, as_json, as_yaml, as_toon):
    # type: (str, bool, bool, bool) -> None
    """Unmute a user. SCREEN_NAME is the @handle (without @)."""
    screen_name = screen_name.lstrip("@")

    def operation(client: TwitterClient) -> WritePayload:
        user_id = client.resolve_user_id(screen_name)
        client.unmute_user(user_id)
        return {"success": True, "action": "unmute", "screenName": screen_name, "userId": user_id}

    _run_write_command(
        as_json=as_json,
        as_yaml=as_yaml,
        operation=operation,
        progress_lines=[f"👤 Looking up @{screen_name}...", f"🔊 Unmuting @{screen_name}..."],
        success_lines=[f"[green]✅ Unmuted @{screen_name}[/green]"],
        error_details={"action": "unmute", "screenName": screen_name},
    )


# ── DM commands ──────────────────────────────────────────────────────────────


@cli.group(name="dm", invoke_without_command=True)
@click.pass_context
def dm(ctx):
    """Direct message commands."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@dm.command(name="conversations")
@click.option(
    "--max", "-n", "max_count", type=int, default=None, help="Max conversations to fetch."
)
@structured_output_options
@click.pass_context
def dm_conversations(ctx, max_count, as_json, as_yaml, as_toon):
    """List DM conversations."""
    compact = ctx.obj.get("compact", False)
    config = load_config()
    rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml, compact=compact)
    try:
        client = _get_client(config, quiet=not rich_output)
        if rich_output:
            console.print("💬 Fetching DM conversations...")
        conversations = client.fetch_dm_conversations(max_count or 50)
    except (TwitterError, RuntimeError) as exc:
        _exit_with_error(exc)

    from .serialization import dm_conversations_to_data

    data = dm_conversations_to_data(conversations)

    if compact:
        click.echo(json.dumps(data, ensure_ascii=False, indent=2))
        return

    if emit_structured(success_payload(data), as_json=as_json, as_yaml=as_yaml):
        return

    if rich_output:
        from rich.table import Table

        table = Table(title="💬 DM Conversations — %d" % len(conversations))
        table.add_column("ID", style="dim")
        table.add_column("Participants", style="bold")
        table.add_column("Last Message", style="dim")
        for conv in conversations:
            participants_list = conv.get("participants", [])
            participants = ", ".join("@" + u.get("screen_name", "") for u in participants_list[:3])
            if len(participants_list) > 3:
                participants += " +%d more" % (len(participants_list) - 3)
            last_msg_obj = conv.get("last_message")
            last_msg = last_msg_obj.get("text", "")[:50] if last_msg_obj else ""
            table.add_row(conv.get("id", ""), participants, last_msg)
        console.print(table)
        console.print()


@dm.command(name="messages")
@click.argument("conversation_id")
@click.option("--max", "-n", "max_count", type=int, default=50, help="Max messages to fetch.")
@structured_output_options
@click.pass_context
def dm_messages(ctx, conversation_id, max_count, as_json, as_yaml, as_toon):
    """Fetch messages from a DM conversation."""
    compact = ctx.obj.get("compact", False)
    config = load_config()
    rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml, compact=compact)
    try:
        client = _get_client(config, quiet=not rich_output)
        if rich_output:
            console.print(f"💬 Fetching messages from conversation {conversation_id}...")
        messages = client.fetch_dm_messages(conversation_id, max_count)
    except (TwitterError, RuntimeError) as exc:
        _exit_with_error(exc)

    from .serialization import dm_messages_to_data

    data = dm_messages_to_data(messages)

    if compact:
        click.echo(json.dumps(data, ensure_ascii=False, indent=2))
        return

    if emit_structured(success_payload(data), as_json=as_json, as_yaml=as_yaml):
        return

    if rich_output:
        from rich.table import Table

        table = Table(title="💬 Messages — %d" % len(messages))
        table.add_column("Time", style="dim")
        table.add_column("Sender", style="bold")
        table.add_column("Message", style="white")
        for msg in messages:
            table.add_row(
                msg.get("created_at", "")[:19] if msg.get("created_at") else "",
                "@" + msg.get("sender_screen_name", ""),
                msg.get("text", ""),
            )
        console.print(table)
        console.print()


@dm.command(name="create")
@click.argument("participants", nargs=-1, required=True)
@structured_output_options
def dm_create(participants, as_json, as_yaml, as_toon):
    """Create a new DM conversation with participants. PARTICIPANTS are @handles (without @)."""
    screen_names = [p.lstrip("@") for p in participants]

    def operation(client: TwitterClient) -> WritePayload:
        conv_id = client.create_dm_conversation(screen_names)
        return {
            "success": True,
            "action": "create_dm",
            "conversationId": conv_id,
            "participants": screen_names,
        }

    _run_write_command(
        as_json=as_json,
        as_yaml=as_yaml,
        operation=operation,
        progress_lines=[
            "💬 Creating DM conversation with {}...".format(", ".join("@" + s for s in screen_names))
        ],
        success_lines=["[green]✅ DM conversation created![/green]"],
        error_details={"action": "create_dm", "participants": screen_names},
    )


@dm.command(name="send")
@click.argument("conversation_id")
@click.argument("text")
@structured_output_options
def dm_send(conversation_id, text, as_json, as_yaml, as_toon):
    """Send a DM to a conversation. CONVERSATION_ID is the DM conversation ID."""

    def operation(client: TwitterClient) -> WritePayload:
        msg_id = client.send_dm(conversation_id, text)
        return {
            "success": True,
            "action": "send_dm",
            "messageId": msg_id,
            "conversationId": conversation_id,
        }

    _run_write_command(
        as_json=as_json,
        as_yaml=as_yaml,
        operation=operation,
        progress_lines=[f"💬 Sending DM to conversation {conversation_id}..."],
        success_lines=["[green]✅ DM sent![/green]"],
        error_details={"action": "send_dm", "conversationId": conversation_id},
    )


@dm.command(name="mark-read")
@click.argument("conversation_id")
@structured_output_options
def dm_mark_read(conversation_id, as_json, as_yaml, as_toon):
    """Mark a DM conversation as read."""

    def operation(client: TwitterClient) -> WritePayload:
        client.mark_dm_conversation_read(conversation_id)
        return {"success": True, "action": "mark_dm_read", "conversationId": conversation_id}

    _run_write_command(
        as_json=as_json,
        as_yaml=as_yaml,
        operation=operation,
        progress_lines=[f"💬 Marking conversation {conversation_id} as read..."],
        success_lines=["[green]✅ Conversation marked as read![/green]"],
        error_details={"action": "mark_dm_read", "conversationId": conversation_id},
    )


@dm.command(name="typing")
@click.argument("conversation_id")
@structured_output_options
def dm_typing(conversation_id, as_json, as_yaml, as_toon):
    """Send typing indicator in a DM conversation."""

    def operation(client: TwitterClient) -> WritePayload:
        client.send_dm_typing_indicator(conversation_id)
        return {"success": True, "action": "dm_typing", "conversationId": conversation_id}

    _run_write_command(
        as_json=as_json,
        as_yaml=as_yaml,
        operation=operation,
        progress_lines=[f"⌨️  Sending typing indicator to conversation {conversation_id}..."],
        success_lines=["[green]✅ Typing indicator sent![/green]"],
        error_details={"action": "dm_typing", "conversationId": conversation_id},
    )


@dm.command(name="rotate-keys")
@click.argument("conversation_id")
@structured_output_options
def dm_rotate_keys(conversation_id, as_json, as_yaml, as_toon):
    """Rotate encryption keys for a DM conversation."""

    def operation(client: TwitterClient) -> WritePayload:
        client.rotate_dm_encryption_keys(conversation_id)
        return {"success": True, "action": "dm_rotate_keys", "conversationId": conversation_id}

    _run_write_command(
        as_json=as_json,
        as_yaml=as_yaml,
        operation=operation,
        progress_lines=[f"🔐 Rotating encryption keys for conversation {conversation_id}..."],
        success_lines=["[green]✅ Encryption keys rotated![/green]"],
        error_details={"action": "dm_rotate_keys", "conversationId": conversation_id},
    )


# ── Poll commands ────────────────────────────────────────────────────────────


@cli.group(name="poll", invoke_without_command=True)
@click.pass_context
def poll(ctx):
    """Poll commands."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@poll.command(name="create")
@click.argument("question")
@click.option(
    "--option",
    "-o",
    "options",
    multiple=True,
    required=True,
    help="Poll option. Repeat for each option (2-4).",
)
@click.option(
    "--duration",
    "-d",
    type=int,
    default=1440,
    help="Poll duration in minutes (default: 1440 = 24h).",
)
@structured_output_options
def poll_create(question, options, duration, as_json, as_yaml, as_toon):
    """Create a poll tweet. QUESTION is the poll question."""
    if len(options) < 2 or len(options) > 4:
        raise click.UsageError("Polls must have 2-4 options")

    def operation(client: TwitterClient) -> WritePayload:
        tweet_id = client.create_poll(question, list(options), duration)
        return {
            "success": True,
            "action": "create_poll",
            "id": tweet_id,
            "url": f"https://x.com/i/status/{tweet_id}",
        }

    _run_write_command(
        as_json=as_json,
        as_yaml=as_yaml,
        operation=operation,
        progress_lines=[f"📊 Creating poll: {question[:50]}..."],
        success_lines=["[green]✅ Poll created![/green]"],
        error_details={"action": "create_poll", "options": len(options)},
    )


@poll.command(name="vote")
@click.argument("tweet_id")
@click.option("--choice", "-c", type=int, required=True, help="Option index to vote for (0-based).")
@structured_output_options
def poll_vote(tweet_id, choice, as_json, as_yaml, as_toon):
    """Vote on a poll. TWEET_ID is the poll tweet ID."""
    tweet_id = _normalize_tweet_id(tweet_id)

    def operation(client: TwitterClient) -> WritePayload:
        client.vote_poll(tweet_id, choice)
        return {"success": True, "action": "vote_poll", "tweetId": tweet_id, "choice": choice}

    _run_write_command(
        as_json=as_json,
        as_yaml=as_yaml,
        operation=operation,
        progress_lines=["📊 Voting on poll %s (choice %d)..." % (tweet_id, choice)],
        success_lines=["[green]✅ Vote cast![/green]"],
        error_details={"action": "vote_poll", "tweetId": tweet_id, "choice": choice},
    )


# ── List management commands ─────────────────────────────────────────────────


class ListGroup(click.Group):
    """Custom group that handles backward compatibility for `twitter-lyr list <id>`."""

    def get_command(self, ctx, cmd_name):
        # First try to get a subcommand
        cmd = super().get_command(ctx, cmd_name)
        if cmd:
            return cmd
        # If cmd_name is numeric, treat as list ID and return the timeline command
        if cmd_name.isdigit():
            from .cli import list_timeline_sub

            return list_timeline_sub
        return None

    def resolve_command(self, ctx, args):
        # Try normal resolution first
        try:
            return super().resolve_command(ctx, args)
        except Exception as exc:
            # If first arg is numeric, treat as list ID - invoke the subcommand directly
            if args and args[0].isdigit() and exc.__class__.__name__ == "NoSuchCommand":
                from .cli import list_timeline_sub

                # Invoke the subcommand with the numeric ID as the list_id argument
                ctx.invoke(list_timeline_sub, list_id=args[0])
                ctx.exit(0)
            raise


@cli.group(name="list", cls=ListGroup, invoke_without_command=True)
@click.pass_context
def list_cmd(ctx):
    """Twitter List commands. Use `twitter-lyr list <id>` to view a list timeline."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@list_cmd.command(name="list-timeline")
@click.argument("list_id")
@click.option("--max", "-n", "max_count", type=int, default=None, help="Max tweets to fetch.")
@click.option(
    "--cursor",
    type=str,
    default=None,
    help="Pagination cursor for continuing a previous list request.",
)
@structured_output_options
@click.option("--filter", "do_filter", is_flag=True, help="Enable score-based filtering.")
@click.option("--full-text", is_flag=True, help="Show full tweet text in table output.")
@click.pass_context
def list_timeline_sub(
    ctx, list_id, max_count, cursor, as_json, as_yaml, as_toon, do_filter, full_text
):
    # type: (Any, str, Optional[int], Optional[str], bool, bool, bool, bool, bool) -> None
    """Fetch tweets from a Twitter List. LIST_ID is the numeric list ID."""
    compact = ctx.obj.get("compact", False)
    config = load_config()
    rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml, compact=compact)

    tweets = []  # type: list[Tweet]
    next_cursor = None  # type: Optional[str]

    def _run():
        nonlocal tweets, next_cursor
        client = _get_client(config)
        try:
            fetch_count = _resolve_configured_count(config, max_count)
            if rich_output:
                console.print("📋 Fetching list %s (%d tweets)...\n" % (list_id, fetch_count))
            start = time.time()
            tweets, next_cursor = client.fetch_list_timeline(
                list_id,
                fetch_count,
                cursor=cursor,
                return_cursor=True,
            )
            elapsed = time.time() - start
            if rich_output:
                console.print("✅ Fetched %d list %s in %.1fs\n" % (len(tweets), list_id, elapsed))
        except (TwitterError, RuntimeError) as exc:
            _exit_with_error(exc)

    _run_guarded(_run)

    filtered = _apply_filter(tweets, do_filter, config, rich_output=rich_output)

    if compact:
        # Explicit --json/--yaml/--toon flags win over the global --format
        # default ("toon") so `--json` in compact mode still emits JSON.
        output_format = ctx.obj.get("output_format", "toon")
        fmt = output_format if not (as_json or as_yaml or as_toon) else (
            "json" if as_json else "yaml" if as_yaml else "toon"
        )
        if fmt == "toon":
            from .serialization import tweet_to_compact_dict

            emit_toon([tweet_to_compact_dict(t) for t in filtered])
        else:
            click.echo(tweets_to_compact_json(filtered, ctx.obj.get("fields")))
        return

    save_tweet_cache(filtered)

    if _emit_timeline_structured(
        filtered, next_cursor, as_json=as_json, as_yaml=as_yaml, as_toon=as_toon
    ):
        return

    print_tweet_table(
        filtered,
        console,
        title="📋 list %s — %d tweets" % (list_id, len(filtered)),
        full_text=full_text,
    )
    _print_show_hint()
    console.print()


@list_cmd.command(name="create")
@click.argument("name")
@click.option("--description", "-d", default="", help="List description.")
@click.option("--private/--public", default=False, help="Make list private (default: public).")
@structured_output_options
def list_create(name, description, private, as_json, as_yaml, as_toon):
    """Create a new Twitter List."""

    def operation(client: TwitterClient) -> WritePayload:
        list_id = client.create_list(name, description, private)
        return {
            "success": True,
            "action": "create_list",
            "listId": list_id,
            "name": name,
            "private": private,
        }

    _run_write_command(
        as_json=as_json,
        as_yaml=as_yaml,
        operation=operation,
        progress_lines=[f"📋 Creating list: {name}..."],
        success_lines=["[green]✅ List created![/green]"],
        error_details={"action": "create_list", "name": name},
    )


@list_cmd.command(name="update")
@click.argument("list_id")
@click.option("--name", default=None, help="New list name.")
@click.option("--description", "-d", default=None, help="New list description.")
@click.option("--private/--public", default=None, help="Change privacy.")
@structured_output_options
def list_update(list_id, name, description, private, as_json, as_yaml, as_toon):
    """Update an existing Twitter List."""
    if name is None and description is None and private is None:
        raise click.UsageError(
            "At least one of --name, --description, or --private/--public is required"
        )

    def operation(client: TwitterClient) -> WritePayload:
        client.update_list(list_id, name=name, description=description, private=private)
        return {"success": True, "action": "update_list", "listId": list_id}

    _run_write_command(
        as_json=as_json,
        as_yaml=as_yaml,
        operation=operation,
        progress_lines=[f"📋 Updating list {list_id}..."],
        success_lines=["[green]✅ List updated![/green]"],
        error_details={"action": "update_list", "listId": list_id},
    )


@list_cmd.command(name="delete")
@click.argument("list_id")
@click.confirmation_option(prompt="Are you sure you want to delete this list?")
@structured_output_options
def list_delete(list_id, as_json, as_yaml, as_toon):
    """Delete a Twitter List."""

    def operation(client: TwitterClient) -> WritePayload:
        client.delete_list(list_id)
        return {"success": True, "action": "delete_list", "listId": list_id}

    _run_write_command(
        as_json=as_json,
        as_yaml=as_yaml,
        operation=operation,
        progress_lines=[f"🗑️  Deleting list {list_id}..."],
        success_lines=["[green]✅ List deleted![/green]"],
        error_details={"action": "delete_list", "listId": list_id},
    )


@list_cmd.command(name="add-member")
@click.argument("list_id")
@click.argument("screen_name")
@structured_output_options
def list_add_member(list_id, screen_name, as_json, as_yaml, as_toon):
    """Add a member to a Twitter List."""
    screen_name = screen_name.lstrip("@")

    def operation(client: TwitterClient) -> WritePayload:
        user_id = client.resolve_user_id(screen_name)
        client.add_list_member(list_id, user_id)
        return {
            "success": True,
            "action": "add_list_member",
            "listId": list_id,
            "screenName": screen_name,
            "userId": user_id,
        }

    _run_write_command(
        as_json=as_json,
        as_yaml=as_yaml,
        operation=operation,
        progress_lines=[f"👤 Looking up @{screen_name}...", f"📋 Adding to list {list_id}..."],
        success_lines=[f"[green]✅ Added @{screen_name} to list {list_id}[/green]"],
        error_details={"action": "add_list_member", "listId": list_id, "screenName": screen_name},
    )


@list_cmd.command(name="remove-member")
@click.argument("list_id")
@click.argument("screen_name")
@structured_output_options
def list_remove_member(list_id, screen_name, as_json, as_yaml, as_toon):
    """Remove a member from a Twitter List."""
    screen_name = screen_name.lstrip("@")

    def operation(client: TwitterClient) -> WritePayload:
        user_id = client.resolve_user_id(screen_name)
        client.remove_list_member(list_id, user_id)
        return {
            "success": True,
            "action": "remove_list_member",
            "listId": list_id,
            "screenName": screen_name,
            "userId": user_id,
        }

    _run_write_command(
        as_json=as_json,
        as_yaml=as_yaml,
        operation=operation,
        progress_lines=[
            f"👤 Looking up @{screen_name}...",
            f"📋 Removing from list {list_id}...",
        ],
        success_lines=[f"[green]✅ Removed @{screen_name} from list {list_id}[/green]"],
        error_details={
            "action": "remove_list_member",
            "listId": list_id,
            "screenName": screen_name,
        },
    )


@list_cmd.command(name="members")
@click.argument("list_id")
@click.option("--max", "-n", "max_count", type=int, default=None, help="Max members to fetch.")
@structured_output_options
@click.pass_context
def list_members(ctx, list_id, max_count, as_json, as_yaml, as_toon):
    """List members of a Twitter List."""
    compact = ctx.obj.get("compact", False)
    config = load_config()
    rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml, compact=compact)
    try:
        client = _get_client(config, quiet=not rich_output)
        if rich_output:
            console.print(f"📋 Fetching members of list {list_id}...")
        members = client.fetch_list_members(list_id, max_count)
    except (TwitterError, RuntimeError) as exc:
        _exit_with_error(exc)

    if emit_structured(users_to_data(members), as_json=as_json, as_yaml=as_yaml):
        return

    if rich_output:
        print_user_table(
            members, console, title="📋 List %s Members — %d" % (list_id, len(members))
        )
        console.print()


@list_cmd.command(name="subscriptions")
@click.option("--max", "-n", "max_count", type=int, default=None, help="Max lists to fetch.")
@structured_output_options
@click.pass_context
def list_subscriptions(ctx, max_count, as_json, as_yaml, as_toon):
    """List Twitter Lists the authenticated user subscribes to."""
    compact = ctx.obj.get("compact", False)
    config = load_config()
    rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml, compact=compact)
    try:
        client = _get_client(config, quiet=not rich_output)
        if rich_output:
            console.print("📋 Fetching list subscriptions...")
        lists = client.fetch_list_subscriptions(max_count)
    except (TwitterError, RuntimeError) as exc:
        _exit_with_error(exc)

    from .serialization import lists_to_data

    data = lists_to_data(lists)

    if compact:
        click.echo(json.dumps(data, ensure_ascii=False, indent=2))
        return

    if emit_structured(success_payload(data), as_json=as_json, as_yaml=as_yaml):
        return

    if rich_output:
        from rich.table import Table

        table = Table(title="📋 Subscribed Lists — %d" % len(lists))
        table.add_column("ID", style="dim")
        table.add_column("Name", style="bold")
        table.add_column("Members", justify="right")
        table.add_column("Private", justify="center")
        for lst in lists:
            table.add_row(
                lst.get("id", ""),
                lst.get("name", ""),
                str(lst.get("member_count", 0)),
                "yes" if lst.get("private", False) else "no",
            )
        console.print(table)
        console.print()


# ── Notifications ────────────────────────────────────────────────────────────


@cli.command()
@click.option(
    "--max", "-n", "max_count", type=int, default=None, help="Max notifications to fetch."
)
@click.option(
    "--type",
    "notif_type",
    type=click.Choice(["all", "mentions", "likes", "retweets", "follows", "quotes"]),
    default="all",
    help="Filter by notification type.",
)
@structured_output_options
@click.pass_context
def notifications(ctx, max_count, notif_type, as_json, as_yaml, as_toon):
    """Fetch notifications for the authenticated user."""
    compact = ctx.obj.get("compact", False)
    config = load_config()
    rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml, compact=compact)
    try:
        client = _get_client(config, quiet=not rich_output)
        if rich_output:
            console.print("🔔 Fetching notifications...")
        notifications = client.fetch_notifications(max_count, notif_type)
    except (TwitterError, RuntimeError) as exc:
        _exit_with_error(exc)

    if emit_structured(notifications, as_json=as_json, as_yaml=as_yaml):
        return

    if rich_output:
        from rich.table import Table

        table = Table(title="🔔 Notifications — %d" % len(notifications))
        table.add_column("Type", style="bold")
        table.add_column("From", style="cyan")
        table.add_column("Message", style="white")
        table.add_column("Time", style="dim")
        for notif in notifications:
            table.add_row(
                notif.get("type", ""),
                "@" + notif.get("from_user", ""),
                notif.get("text", "")[:60],
                notif.get("created_at", "")[:19],
            )
        console.print(table)
        console.print()


# ── Community commands ───────────────────────────────────────────────────────


@cli.group(name="community", invoke_without_command=True)
@click.pass_context
def community(ctx):
    """Community commands."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@community.command(name="join")
@click.argument("community_id")
@structured_output_options
def community_join(community_id, as_json, as_yaml, as_toon):
    """Join a Community."""

    def operation(client: TwitterClient) -> WritePayload:
        client.join_community(community_id)
        return {"success": True, "action": "join_community", "communityId": community_id}

    _run_write_command(
        as_json=as_json,
        as_yaml=as_yaml,
        operation=operation,
        progress_lines=[f"🏘️  Joining community {community_id}..."],
        success_lines=["[green]✅ Joined community![/green]"],
        error_details={"action": "join_community", "communityId": community_id},
    )


@community.command(name="leave")
@click.argument("community_id")
@structured_output_options
def community_leave(community_id, as_json, as_yaml, as_toon):
    """Leave a Community."""

    def operation(client: TwitterClient) -> WritePayload:
        client.leave_community(community_id)
        return {"success": True, "action": "leave_community", "communityId": community_id}

    _run_write_command(
        as_json=as_json,
        as_yaml=as_yaml,
        operation=operation,
        progress_lines=[f"🏘️  Leaving community {community_id}..."],
        success_lines=["[green]✅ Left community![/green]"],
        error_details={"action": "leave_community", "communityId": community_id},
    )


@community.command(name="tweets")
@click.argument("community_id")
@click.option("--max", "-n", "max_count", type=int, default=None, help="Max tweets to fetch.")
@structured_output_options
@click.pass_context
def community_tweets(ctx, community_id, max_count, as_json, as_yaml, as_toon):
    """Fetch tweets from a Community."""
    compact = ctx.obj.get("compact", False)
    config = load_config()
    rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml, compact=compact)
    try:
        client = _get_client(config, quiet=not rich_output)
        if rich_output:
            console.print(f"🏘️  Fetching tweets from community {community_id}...")
        tweets = client.fetch_community_tweets(community_id, max_count)
    except (TwitterError, RuntimeError) as exc:
        _exit_with_error(exc)

    filtered = _apply_filter(tweets, False, config, rich_output=rich_output)

    if compact:
        click.echo(tweets_to_compact_json(filtered, ctx.obj.get("fields")))
        return

    if emit_structured(
        tweets_to_data(filtered, ctx.obj.get("fields")), as_json=as_json, as_yaml=as_yaml
    ):
        return

    save_tweet_cache(filtered)
    print_tweet_table(
        filtered, console, title="🏘️ Community %s Tweets — %d" % (community_id, len(filtered))
    )
    console.print()
