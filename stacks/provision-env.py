"""Create missing private stack env files from their templates. Never overwrites.

Usage: provision-env.py <stacks-dir> <lan-ip>
(media-server/.env is created by setup-stacks.sh, which knows the media paths and groups.)
"""
import json
import os
import secrets
import sys
import tempfile
from pathlib import Path


def write_env(template, target, updates):
    lines = []
    remaining = set(updates)
    for line in template.read_text().splitlines():
        key, _, _ = line.partition("=")
        if key in updates:
            value = json.dumps(updates[key].replace("$", "$$"), ensure_ascii=False)
            line = f"{key}={value}"
            remaining.discard(key)
        lines.append(line)
    if remaining:
        raise ValueError(f"Missing fields in {template.name}: {', '.join(sorted(remaining))}")
    with tempfile.NamedTemporaryFile(mode="w", dir=target.parent) as temp:
        temp.write("\n".join(lines) + "\n")
        temp.flush()
        try:
            os.link(temp.name, target)
        except FileExistsError:
            pass


def provision(root, lan_ip):
    router = root / "9router" / ".env"
    if not router.exists():
        write_env(router.with_name(".env.example"), router, {
            "LAN_IP": lan_ip,
            "DATA_DIR": str(Path.home() / "data" / "9router"),
            "INITIAL_PASSWORD": secrets.token_hex(24),
        })


if __name__ == "__main__":
    try:
        provision(Path(sys.argv[1]).resolve(), sys.argv[2])
    except (OSError, ValueError):
        sys.exit("Could not provision stack env files. Check the templates.")
