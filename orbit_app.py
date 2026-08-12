"""Orbit's small local lifecycle entry point (source and bundled runtime)."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from orbit_config import app_support_dir, logs_dir


HOST = "127.0.0.1"
PORT = int(os.getenv("ORBIT_PORT", "8000"))
BASE_URL = f"http://localhost:{PORT}"


def runtime_command(*arguments: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, *arguments]
    return [sys.executable, str(Path(__file__).resolve()), *arguments]


def pid_path() -> Path:
    return app_support_dir() / "orbit.pid"


def lock_path() -> Path:
    return app_support_dir() / "service.lock"


def log_path() -> Path:
    return logs_dir() / "orbit.log"


def health(timeout: float = 1.0) -> dict | None:
    try:
        with urllib.request.urlopen(f"{BASE_URL}/health", timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
            return body if body.get("service") == "orbit" else None
    except (OSError, ValueError, urllib.error.URLError):
        return None


def port_is_in_use() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(0.4)
        return connection.connect_ex((HOST, PORT)) == 0


def wait_for_orbit(seconds: float) -> dict | None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        current = health(timeout=0.3)
        if current:
            return current
        time.sleep(0.1)
    return None


def show_error(message: str) -> None:
    if sys.platform == "darwin":
        subprocess.run(
            ["/usr/bin/osascript", "-e", f'display alert "Orbit could not start" message {json.dumps(message)}'],
            capture_output=True,
            timeout=15,
        )
    else:
        print(message, file=sys.stderr)


def open_status() -> None:
    webbrowser.open(f"{BASE_URL}/orbit")


def start(open_browser: bool = True) -> int:
    existing = health()
    if existing:
        if open_browser:
            open_status()
        return 0
    if port_is_in_use():
        # A nearly-complete first launch can own the port before /health is
        # ready. Give it a moment so a double-click remains harmless.
        if wait_for_orbit(3):
            if open_browser:
                open_status()
            return 0
        message = (
            f"Port {PORT} is already being used by another application. "
            "Quit that application or change its port, then open Orbit again."
        )
        show_error(message)
        return 2

    app_support_dir().mkdir(parents=True, exist_ok=True)
    logs_dir().mkdir(parents=True, exist_ok=True)
    with log_path().open("a", encoding="utf-8") as output:
        process = subprocess.Popen(
            runtime_command("serve"),
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if health(timeout=0.5):
            if open_browser:
                open_status()
            return 0
        time.sleep(0.2)
    message = f"Orbit did not start. Details are in {log_path()}."
    show_error(message)
    return 1


def serve() -> int:
    app_support_dir().mkdir(parents=True, exist_ok=True)
    lock_file = lock_path().open("a+")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0
    pid_path().write_text(f"{os.getpid()}\n", encoding="utf-8")
    os.chmod(pid_path(), 0o600)
    os.environ["ORBIT_PRODUCT_MODE"] = "1"
    try:
        import uvicorn
        import server

        uvicorn.run(server.app, host=HOST, port=PORT, log_level="info")
        return 0
    finally:
        try:
            if pid_path().read_text(encoding="utf-8").strip() == str(os.getpid()):
                pid_path().unlink(missing_ok=True)
        except OSError:
            pass
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()


def stop() -> int:
    if not health():
        pid_path().unlink(missing_ok=True)
        return 0
    try:
        pid = int(pid_path().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        show_error(f"Orbit is running, but its process record is missing. See {log_path()}.")
        return 1
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not health(timeout=0.3):
            return 0
        time.sleep(0.2)
    show_error(f"Orbit did not stop cleanly. See {log_path()}.")
    return 1


def status() -> int:
    current = health()
    print(json.dumps(current or {"status": "stopped", "service": "orbit"}, indent=2))
    return 0 if current else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Start and manage the local Orbit service.")
    subparsers = parser.add_subparsers(dest="command")
    start_parser = subparsers.add_parser("start")
    start_parser.add_argument("--no-open", action="store_true")
    subparsers.add_parser("serve")
    subparsers.add_parser("stop")
    subparsers.add_parser("status")
    args = parser.parse_args()
    if args.command in (None, "start"):
        return start(getattr(args, "no_open", False) is False)
    if args.command == "serve":
        return serve()
    if args.command == "stop":
        return stop()
    return status()


if __name__ == "__main__":
    raise SystemExit(main())
