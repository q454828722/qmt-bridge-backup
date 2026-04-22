#!/usr/bin/env python3
"""Forward WSL localhost:18888 to the Windows-hosted QMT Bridge service."""

from __future__ import annotations

import os
import select
import socket
import subprocess
import sys
import threading
from contextlib import suppress


def default_gateway() -> str:
    route = subprocess.run(
        ["ip", "route"],
        check=False,
        capture_output=True,
        text=True,
    )
    for line in route.stdout.splitlines():
        parts = line.split()
        if parts[:1] == ["default"] and "via" in parts:
            return parts[parts.index("via") + 1]
    return "172.23.16.1"


LISTEN_HOST = os.environ.get("QMT_BRIDGE_WSL_LISTEN_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("QMT_BRIDGE_WSL_LISTEN_PORT", "18888"))
TARGET_HOST = os.environ.get("QMT_BRIDGE_WINDOWS_HOST", default_gateway())
TARGET_PORT = int(os.environ.get("QMT_BRIDGE_PORT", "18888"))


def relay(left: socket.socket, right: socket.socket) -> None:
    try:
        while True:
            readable, _, _ = select.select([left, right], [], [])
            for sock in readable:
                data = sock.recv(65536)
                if not data:
                    return
                (right if sock is left else left).sendall(data)
    except Exception:
        return
    finally:
        with suppress(Exception):
            left.close()
        with suppress(Exception):
            right.close()


def main() -> int:
    listen_addr = (LISTEN_HOST, LISTEN_PORT)
    target_addr = (TARGET_HOST, TARGET_PORT)

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(listen_addr)
    listener.listen(100)
    print(
        f"forward {listen_addr[0]}:{listen_addr[1]} -> {target_addr[0]}:{target_addr[1]}",
        flush=True,
    )

    while True:
        client, _ = listener.accept()
        try:
            upstream = socket.create_connection(target_addr, timeout=5)
        except Exception as exc:
            print(f"upstream connect failed: {exc}", file=sys.stderr, flush=True)
            with suppress(Exception):
                client.close()
            continue
        threading.Thread(target=relay, args=(client, upstream), daemon=True).start()


if __name__ == "__main__":
    raise SystemExit(main())
