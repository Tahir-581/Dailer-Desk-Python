#!/usr/bin/env python3
"""
Write or update LiveKit credentials in .env.local.

Use this when you have credentials from a secret manager or another source
and want to populate .env.local without manually editing the file.

Example (env vars):
  LIVEKIT_KEY_INPUT=your_api_key LIVEKIT_SECRET_INPUT=your_secret \\
  LIVEKIT_URL_INPUT=wss://your-project.livekit.cloud \\
  python scripts/set_livekit_env.py

Example (arguments):
  python scripts/set_livekit_env.py --key YOUR_KEY --secret YOUR_SECRET --url wss://...

The script updates .env.local in the project root (parent of scripts/).
Existing LIVEKIT_API_KEY, LIVEKIT_API_SECRET, LIVEKIT_URL lines are updated;
other lines are left unchanged. If .env.local does not exist, it is created.
"""

import argparse
import os
import re
from pathlib import Path


def _project_root() -> Path:
    root = Path(__file__).resolve().parent.parent
    return root


def _env_local_path() -> Path:
    return _project_root() / ".env.local"


def _read_env_local() -> list[tuple[str, str]]:
    """Read .env.local. Returns list of (key, value_or_line).
    For key=value lines, key is the variable name and value_or_line is the value.
    For comments/blanks, key is '' and value_or_line is the full line to preserve.
    """
    path = _env_local_path()
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    result = []
    for line in lines:
        raw = line.rstrip()
        if not raw or raw.lstrip().startswith("#"):
            result.append(("", raw))
            continue
        m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)$", raw)
        if m:
            result.append((m.group(1).strip(), m.group(2).strip()))
        else:
            result.append(("", raw))
    return result


def _write_env_local(updates: dict[str, str]) -> None:
    """Update .env.local with the given key=value pairs. Preserves other keys and comments."""
    path = _env_local_path()
    livekit_keys = {"LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "LIVEKIT_URL"}
    existing = _read_env_local()
    keys_to_set = set(updates.keys())

    new_lines = []
    seen_keys = set()

    for key, value_or_line in existing:
        if key == "":
            new_lines.append(value_or_line)
            continue
        if key in livekit_keys and key in keys_to_set:
            new_lines.append(f"{key}={updates[key]}")
            seen_keys.add(key)
            continue
        seen_keys.add(key)
        new_lines.append(f"{key}={value_or_line}")

    for k in keys_to_set:
        if k not in seen_keys:
            if new_lines and new_lines[-1].strip() != "":
                new_lines.append("")
            new_lines.append(f"{k}={updates[k]}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Write or update LIVEKIT_API_KEY, LIVEKIT_API_SECRET, and optionally LIVEKIT_URL in .env.local."
    )
    parser.add_argument(
        "--key",
        default=os.getenv("LIVEKIT_KEY_INPUT", ""),
        help="LiveKit API key (default: LIVEKIT_KEY_INPUT env var)",
    )
    parser.add_argument(
        "--secret",
        default=os.getenv("LIVEKIT_SECRET_INPUT", ""),
        help="LiveKit API secret (default: LIVEKIT_SECRET_INPUT env var)",
    )
    parser.add_argument(
        "--url",
        default=os.getenv("LIVEKIT_URL_INPUT", ""),
        help="LiveKit URL, e.g. wss://your-project.livekit.cloud (default: LIVEKIT_URL_INPUT env var)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be written without modifying .env.local",
    )
    args = parser.parse_args()

    updates = {}
    if args.key:
        updates["LIVEKIT_API_KEY"] = args.key
    if args.secret:
        updates["LIVEKIT_API_SECRET"] = args.secret
    if args.url:
        updates["LIVEKIT_URL"] = args.url

    if not updates:
        parser.error("Provide at least one of --key, --secret, or --url (or set LIVEKIT_KEY_INPUT / LIVEKIT_SECRET_INPUT / LIVEKIT_URL_INPUT)")

    if args.dry_run:
        for k, v in updates.items():
            print(f"{k}={v}")
        print(f"(dry-run; would write to {_env_local_path()})")
        return

    _write_env_local(updates)
    print(f"Updated {_env_local_path()} with: {', '.join(updates.keys())}")


if __name__ == "__main__":
    main()
