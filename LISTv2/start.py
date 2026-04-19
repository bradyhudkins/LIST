#!/usr/bin/env python3
"""
start.py — one-command launcher for LIST.

Works on Windows, macOS, and Linux.
Requires only Python 3.9+ (no extra tools needed).

What it does:
  1. Installs missing Python packages from requirements.txt
  2. Seeds the database (creates tables + admin user on first run)
  3. Starts the API server (uvicorn) in the background
  4. Starts the GUI (Dash) in the background
  5. Opens your browser automatically
  6. Waits — press Ctrl+C to shut everything down cleanly
"""

import os
import sys
import time
import subprocess
import webbrowser
import signal
import platform

# ── Resolve paths ─────────────────────────────────────────────────────────────

ROOT = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable  # same interpreter that's running this script

# ── Config (mirrors config.py defaults) ──────────────────────────────────────

API_PORT = int(os.getenv("LIST_API_PORT", "8000"))
GUI_PORT = int(os.getenv("LIST_GUI_PORT", "8050"))

# ── Helpers ───────────────────────────────────────────────────────────────────

def info(msg):
    print(f"  \033[96m→\033[0m  {msg}")

def ok(msg):
    print(f"  \033[92m✓\033[0m  {msg}")

def err(msg):
    print(f"  \033[91m✗\033[0m  {msg}", file=sys.stderr)

def step(msg):
    print(f"\n\033[1m{msg}\033[0m")


def run(cmd, **kwargs):
    """Run a command, raising on failure."""
    return subprocess.run(cmd, check=True, **kwargs)


def wait_for_port(port, timeout=20):
    """Block until something is listening on localhost:port."""
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.4)
    return False


# ── Step 1: Install dependencies ──────────────────────────────────────────────

def install_deps():
    step("1/4  Installing dependencies")
    req = os.path.join(ROOT, "requirements.txt")
    run(
        [PYTHON, "-m", "pip", "install", "-q", "-r", req, "--user"],
        cwd=ROOT,
    )
    ok("Dependencies installed")


# ── Step 2: Seed database ─────────────────────────────────────────────────────

def seed_db():
    step("2/4  Seeding database")
    run([PYTHON, "seed.py"], cwd=ROOT)


# ── Step 3 & 4: Launch processes ──────────────────────────────────────────────

def launch():
    step("3/4  Starting API server")
    api_url = os.getenv("LIST_API_URL", f"http://localhost:{API_PORT}")
    api_proc = subprocess.Popen(
        [
            PYTHON, "-m", "uvicorn",
            "api.main:app",
            "--host", "0.0.0.0",
            "--port", str(API_PORT),
            "--log-level", "warning",
        ],
        cwd=ROOT,
        # On Windows, CREATE_NEW_PROCESS_GROUP lets us send Ctrl+C cleanly
        **({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
           if platform.system() == "Windows" else {}),
    )

    info(f"Waiting for API on port {API_PORT} …")
    if not wait_for_port(API_PORT):
        err("API did not start in time. Check for errors above.")
        api_proc.terminate()
        sys.exit(1)
    ok(f"API running at http://localhost:{API_PORT}")

    step("4/4  Starting GUI")
    gui_env = os.environ.copy()
    gui_env["LIST_API_URL"] = api_url
    gui_env["LIST_GUI_PORT"] = str(GUI_PORT)
    gui_proc = subprocess.Popen(
        [PYTHON, os.path.join("gui", "app.py")],
        cwd=ROOT,
        env=gui_env,
        **({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
           if platform.system() == "Windows" else {}),
    )

    info(f"Waiting for GUI on port {GUI_PORT} …")
    if not wait_for_port(GUI_PORT):
        err("GUI did not start in time. Check for errors above.")
        api_proc.terminate()
        gui_proc.terminate()
        sys.exit(1)
    ok(f"GUI running at http://localhost:{GUI_PORT}")

    return api_proc, gui_proc


# ── Open browser ──────────────────────────────────────────────────────────────

def open_browser():
    url = f"http://localhost:{GUI_PORT}"
    print(f"\n  \033[1m\033[92mLIST is ready →  {url}\033[0m")
    print("  Default login:  admin  /  admin123")
    print("  Press Ctrl+C to stop.\n")
    time.sleep(0.5)
    webbrowser.open(url)


# ── Shutdown ──────────────────────────────────────────────────────────────────

def shutdown(procs):
    print("\n\nShutting down …")
    for p in procs:
        try:
            if platform.system() == "Windows":
                p.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                p.terminate()
            p.wait(timeout=5)
        except Exception:
            p.kill()
    ok("Stopped.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("\n\033[1m\033[96m LIST — Lightweight Investigation System for Ticketing\033[0m\n")

    try:
        install_deps()
        seed_db()
        api_proc, gui_proc = launch()
        open_browser()

        # Keep running until Ctrl+C
        while True:
            time.sleep(1)
            # Restart a process if it dies unexpectedly
            if api_proc.poll() is not None:
                err("API process exited unexpectedly.")
                break
            if gui_proc.poll() is not None:
                err("GUI process exited unexpectedly.")
                break

    except KeyboardInterrupt:
        pass
    except subprocess.CalledProcessError as e:
        err(f"Command failed: {e}")
        sys.exit(1)
    finally:
        try:
            shutdown([api_proc, gui_proc])
        except NameError:
            pass  # processes never started


if __name__ == "__main__":
    main()
