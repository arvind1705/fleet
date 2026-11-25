import json
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote


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


def public_urls(lan_ip):
    """Browser-facing URLs: Caddy/mDNS names on a LAN host, direct ports on loopback."""
    if lan_ip in ("127.0.0.1", "localhost"):
        return {
            "langfuse": "http://localhost:3005",
            "minio": "http://localhost:9092",
            "minio_console": "http://localhost:9094",
            "minio_media": "http://localhost:9092",
        }
    return {
        "langfuse": "http://langfuse.local",
        "minio": "http://minio.local",
        "minio_console": "http://minio-console.local",
        "minio_media": "http://minio.local:9092",
    }


def provision(root, lan_ip):
    devdeps = root / "devdeps"
    langfuse = root / "langfuse" / "langfuse.env"
    cube = root / "cube" / ".env"
    router = root / "9router" / ".env"
    needs_langfuse = not langfuse.exists()
    needs_cube = not cube.exists()
    urls = public_urls(lan_ip)

    if not (devdeps / ".env").exists():
        write_env(devdeps / ".env.example", devdeps / ".env", {
            "LAN_IP": lan_ip,
            "POSTGRES_PASSWORD": secrets.token_hex(24),
            "CLICKHOUSE_PASSWORD": secrets.token_hex(24),
            "MINIO_ROOT_PASSWORD": secrets.token_hex(24),
            "MINIO_SERVER_URL": urls["minio"],
            "MINIO_BROWSER_REDIRECT_URL": urls["minio_console"],
        })

    if needs_langfuse or needs_cube:
        result = subprocess.run(
            ["docker", "compose", "config", "--environment"], cwd=devdeps,
            check=True, capture_output=True, text=True,
        )
        existing = dict(line.split("=", 1) for line in result.stdout.split("\n") if "=" in line)
        postgres_user = existing.get("POSTGRES_USER") or "kg_mcp"
        postgres_db = existing.get("POSTGRES_DB") or "kg_mcp_db"
        postgres_password = existing["POSTGRES_PASSWORD"]
        if not postgres_password:
            raise ValueError("Missing Postgres password")

    if needs_langfuse:
        clickhouse_user = existing.get("CLICKHOUSE_USER") or "clickhouse"
        minio_user = existing.get("MINIO_ROOT_USER") or "minio"
        clickhouse_password = existing["CLICKHOUSE_PASSWORD"]
        minio_password = existing["MINIO_ROOT_PASSWORD"]
        if not clickhouse_password or not minio_password:
            raise ValueError("Missing ClickHouse or MinIO password")
        pg_login = ":".join(quote(value, safe="") for value in (postgres_user, postgres_password))
        ch_login = ":".join(quote(value, safe="") for value in (clickhouse_user, clickhouse_password))
        write_env(langfuse.with_suffix(".env.example"), langfuse, {
            "NEXTAUTH_URL": urls["langfuse"],
            "LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT": urls["minio_media"],
            "NEXTAUTH_SECRET": secrets.token_urlsafe(32),
            "SALT": secrets.token_urlsafe(32),
            "ENCRYPTION_KEY": secrets.token_hex(32),
            "DATABASE_URL": f"postgresql://{pg_login}@postgres:5432/langfuse",
            "CLICKHOUSE_MIGRATION_URL": f"clickhouse://{ch_login}@langfuse-clickhouse:9000/default",
            "CLICKHOUSE_URL": f"http://{ch_login}@langfuse-clickhouse:8123",
            "CLICKHOUSE_USER": clickhouse_user,
            "CLICKHOUSE_PASSWORD": clickhouse_password,
            "LANGFUSE_S3_EVENT_UPLOAD_ACCESS_KEY_ID": minio_user,
            "LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY": minio_password,
            "LANGFUSE_S3_MEDIA_UPLOAD_ACCESS_KEY_ID": minio_user,
            "LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY": minio_password,
            "LANGFUSE_INIT_PROJECT_PUBLIC_KEY": f"pk-lf-{secrets.token_hex(16)}",
            "LANGFUSE_INIT_PROJECT_SECRET_KEY": f"sk-lf-{secrets.token_hex(32)}",
            "LANGFUSE_INIT_USER_PASSWORD": secrets.token_hex(24),
        })

    if not router.exists():
        write_env(router.with_name(".env.example"), router, {
            "LAN_IP": lan_ip,
            "DATA_DIR": str(Path.home() / "data" / "9router"),
            "INITIAL_PASSWORD": secrets.token_hex(24),
        })

    # The langfuse compose file reads LAN_IP for its published port; langfuse.env is only
    # passed to the containers, so the bind address needs its own compose .env.
    langfuse_compose_env = root / "langfuse" / ".env"
    if not langfuse_compose_env.exists():
        langfuse_compose_env.write_text(f"LAN_IP={lan_ip}\n")

    if needs_cube:
        write_env(root / "cube.env.example", cube, {
            "LAN_IP": lan_ip,
            "CUBEJS_API_SECRET": secrets.token_hex(32),
            "CUBEJS_SQL_PASSWORD": secrets.token_hex(24),
            "CUBEJS_DB_NAME": postgres_db,
            "CUBEJS_DB_USER": postgres_user,
            "CUBEJS_DB_PASS": postgres_password,
        })


if __name__ == "__main__":
    try:
        provision(Path(sys.argv[1]).resolve(), sys.argv[2])
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError):
        sys.exit("Could not provision stack env files. Check the templates and devdeps credentials.")
