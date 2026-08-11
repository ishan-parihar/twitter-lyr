"""Shared structured output helpers for twitter-lyr."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from typing import Any

import click
import yaml

_OUTPUT_ENV = "OUTPUT"
_SCHEMA_VERSION = "1"


def ensure_utf8_streams() -> None:
    """Reconfigure stdout/stderr to use UTF-8 encoding on Windows.

    On Windows with ConPTY disabled (e.g. winpty fallback + PowerShell),
    the default encoding may be GBK/cp936 which cannot encode emoji.
    Calling reconfigure(encoding='utf-8') once at startup fixes ALL
    output paths — click.echo, rich Console, and plain print — without
    needing per-call wrappers.

    This is a no-op on Unix (already UTF-8) and safe to call multiple times.
    """
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass  # frozen or non-standard stream, skip


def default_structured_format(*, as_json: bool, as_yaml: bool, as_toon: bool = False) -> str | None:
    """Resolve explicit flags first, then env override, then TTY default."""
    if sum([as_json, as_yaml, as_toon]) > 1:
        raise click.UsageError("Use only one of --json, --yaml, or --toon.")
    if as_toon:
        return "toon"
    if as_yaml:
        return "yaml"
    if as_json:
        return "json"

    output_mode = os.getenv(_OUTPUT_ENV, "auto").strip().lower()
    if output_mode == "yaml":
        return "yaml"
    if output_mode == "json":
        return "json"
    if output_mode == "toon":
        return "toon"
    if output_mode == "rich":
        return None

    if not sys.stdout.isatty():
        # Non-TTY (piped) output defaults to YAML so downstream tools/agents
        # get a stable, universally parseable format; TOON is opt-in via
        # `--toon` or the explicit `--format toon` flag.
        return "yaml"
    return None


def use_rich_output(
    *, as_json: bool, as_yaml: bool, as_toon: bool = False, compact: bool = False
) -> bool:
    """Return True when human-readable rich output should be used."""
    if compact:
        return False
    return default_structured_format(as_json=as_json, as_yaml=as_yaml, as_toon=as_toon) is None


def structured_output_options(command: Callable) -> Callable:
    """Add --json/--yaml/--toon options to a Click command."""
    command = click.option(
        "--toon", "as_toon", is_flag=True, help="Output as TOON (token-efficient)."
    )(command)
    command = click.option("--yaml", "as_yaml", is_flag=True, help="Output as YAML.")(command)
    command = click.option("--json", "as_json", is_flag=True, help="Output as JSON.")(command)
    return command


def emit_structured(data: Any, *, as_json: bool, as_yaml: bool, as_toon: bool = False) -> bool:
    """Emit structured output and return True when used."""
    if as_json and as_yaml and as_toon:
        raise click.UsageError("Use only one of --json, --yaml, or --toon.")
    if as_toon:
        emit_toon(data)
        return True
    fmt = default_structured_format(as_json=as_json, as_yaml=as_yaml)
    if not fmt:
        return False
    payload = _normalize_success_payload(data)
    if fmt == "json":
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        click.echo(
            yaml.safe_dump(
                payload,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            )
        )
    return True


def success_payload(data: Any) -> dict[str, Any]:
    """Wrap structured success data in the shared agent schema."""
    return {
        "ok": True,
        "schema_version": _SCHEMA_VERSION,
        "data": data,
    }


def error_payload(code: str, message: str, *, details: Any | None = None) -> dict[str, Any]:
    """Wrap structured error data in the shared agent schema."""
    error = {
        "code": code,
        "message": message,
    }
    if details is not None:
        error["details"] = details
    return {
        "ok": False,
        "schema_version": _SCHEMA_VERSION,
        "error": error,
    }


def _normalize_success_payload(data: Any) -> Any:
    """Wrap plain structured data in the shared agent success schema."""
    if isinstance(data, dict) and data.get("schema_version") == _SCHEMA_VERSION and "ok" in data:
        return data
    return success_payload(data)


def _encode_toon(obj: Any, indent: int = 0) -> str:
    """Encode a Python object to TOON (Token-Oriented Object Notation) format.

    TOON is a compact, token-efficient format optimized for LLM consumption.
    Uses single-line notation where possible, minimal whitespace.
    """
    if obj is None:
        return "null"
    if isinstance(obj, bool):
        return "true" if obj else "false"
    if isinstance(obj, (int, float)):
        return str(obj)
    if isinstance(obj, str):
        # Escape special characters
        escaped = obj.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(obj, list):
        if not obj:
            return "[]"
        # Check if all items are simple (no nested structures)
        all_simple = all(not isinstance(item, (dict, list)) for item in obj)
        if all_simple and len(obj) <= 10:
            items = ", ".join(_encode_toon(item) for item in obj)
            return f"[{items}]"
        # Multi-line for complex arrays
        items = "\n".join("  " * (indent + 1) + _encode_toon(item) for item in obj)
        return f"[\n{items}\n{'  ' * indent}]"
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        # Single-line for simple dicts with few keys
        if len(obj) <= 5 and all(not isinstance(v, (dict, list)) for v in obj.values()):
            items = ", ".join(f"{k}: {_encode_toon(v)}" for k, v in obj.items())
            return f"{{ {items} }}"
        # Multi-line for complex dicts
        items = "\n".join(
            "  " * (indent + 1) + f"{k}: {_encode_toon(v, indent + 1)}" for k, v in obj.items()
        )
        return f"{{{items}\n{'  ' * indent}}}"
    # Fallback for other types
    return _encode_toon(str(obj))


def emit_toon(data: Any) -> None:
    """Emit data in TOON format to stdout."""
    click.echo(_encode_toon(_normalize_success_payload(data)))


def emit_empty_state(
    label: str, hint: str, *, as_json: bool = False, as_yaml: bool = False, as_toon: bool = False
) -> bool:
    """Emit a structured empty-state message when results are empty.

    Returns True when structured output was used, False for rich (human) output.
    """
    payload = success_payload({
        "items": [],
        "empty": True,
        "message": f"No {label} found.",
        "hint": hint,
    })
    if as_toon:
        emit_toon(payload)
        return True
    fmt = default_structured_format(as_json=as_json, as_yaml=as_yaml)
    if fmt == "json":
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return True
    if fmt == "yaml":
        click.echo(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, default_flow_style=False)
        )
        return True
    if fmt == "toon":
        emit_toon(payload)
        return True
    return False


def emit_error(
    code: str,
    message: str,
    *,
    as_json: bool | None = None,
    as_yaml: bool | None = None,
    as_toon: bool | None = None,
    details: Any | None = None,
) -> bool:
    """Emit a structured error when the active output mode is machine-readable."""
    if as_json is None or as_yaml is None:
        ctx = click.get_current_context(silent=True)
        params = ctx.params if ctx is not None else {}
        as_json = bool(params.get("as_json", False)) if as_json is None else as_json
        as_yaml = bool(params.get("as_yaml", False)) if as_yaml is None else as_yaml

    fmt = default_structured_format(as_json=bool(as_json), as_yaml=bool(as_yaml))
    if fmt is None:
        return False

    payload = error_payload(code, message, details=details)
    if fmt == "json":
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        click.echo(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, default_flow_style=False)
        )
    return True
