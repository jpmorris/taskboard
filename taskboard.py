#!/usr/bin/env python3
"""taskboard - track AI tasks across VSCode windows and CLI sessions."""

import json
import os
import sys
import time
import select
import signal
import argparse
import subprocess
import fcntl
import termios
import tty
from datetime import datetime
from pathlib import Path

TASKBOARD_DIR = Path.home() / ".taskboard"
TASKS_FILE = TASKBOARD_DIR / "local.json"
# Legacy support: migrate tasks.json → local.json
_LEGACY_FILE = TASKBOARD_DIR / "tasks.json"


def _load_json(path):
    if not path.exists():
        return []
    with open(path) as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def load_tasks():
    """Load tasks from local.json (or legacy tasks.json)."""
    tasks = _load_json(TASKS_FILE)
    if not tasks and _LEGACY_FILE.exists():
        tasks = _load_json(_LEGACY_FILE)
        if tasks:
            save_tasks(tasks)
            _LEGACY_FILE.unlink()
    return tasks


def load_all_tasks():
    """Load local + all remote task files. Returns list of (source, tasks)."""
    result = [("local", load_tasks())]
    for f in sorted(TASKBOARD_DIR.glob("*.json")):
        if f.name in ("local.json", "tasks.json", "hidden.json", "hook_debug.json"):
            continue
        if f.suffix == ".json":
            source = f.stem  # e.g. "sagemaker" from sagemaker.json
            tasks = _load_json(f)
            result.append((source, tasks))
    return result


def save_tasks(tasks):
    TASKBOARD_DIR.mkdir(parents=True, exist_ok=True)
    tmp = TASKS_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        json.dump(tasks, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    tmp.rename(TASKS_FILE)


def next_id(tasks):
    if not tasks:
        return 1
    return max(t["id"] for t in tasks) + 1


def format_elapsed(created_at, completed_at=None):
    start = datetime.fromisoformat(created_at)
    end = datetime.fromisoformat(completed_at) if completed_at else datetime.now()
    delta = end - start
    mins = int(delta.total_seconds() // 60)
    secs = int(delta.total_seconds() % 60)
    if mins > 60:
        return f"{mins // 60}h{mins % 60:02d}m"
    return f"{mins}m{secs:02d}s"


def cmd_add(args):
    tasks = load_tasks()
    tool = args.tool or "manual"
    status = "todo" if tool == "manual" else "running"
    task = {
        "id": next_id(tasks),
        "description": args.description,
        "status": status,
        "tool": tool,
        "cwd": args.cwd or os.getcwd(),
        "session_id": args.session or None,
        "created_at": datetime.now().isoformat(),
        "completed_at": None,
    }
    tasks.append(task)
    save_tasks(tasks)
    print(f"#{task['id']} added: {task['description']}")


def cmd_done(args):
    tasks = load_tasks()

    if args.id:
        for t in tasks:
            if t["id"] == args.id and t["status"] == "running":
                t["status"] = "done"
                t["completed_at"] = datetime.now().isoformat()
                save_tasks(tasks)
                print(f"#{t['id']} done: {t['description']}")
                return
        print(f"No running task with ID {args.id}", file=sys.stderr)
        sys.exit(1)

    elif args.session:
        for t in reversed(tasks):
            if t.get("session_id") == args.session and t["status"] == "running":
                t["status"] = "done"
                t["completed_at"] = datetime.now().isoformat()
                save_tasks(tasks)
                print(f"#{t['id']} done: {t['description']}")
                return
        # No matching session - silently exit (hook may fire without a registered task)

    elif args.cwd:
        for t in reversed(tasks):
            if t.get("cwd") == args.cwd and t["status"] == "running":
                t["status"] = "done"
                t["completed_at"] = datetime.now().isoformat()
                save_tasks(tasks)
                print(f"#{t['id']} done: {t['description']}")
                return
        print(f"No running task for cwd {args.cwd}", file=sys.stderr)
        sys.exit(1)

    else:
        print("Provide a task ID, --session, or --cwd", file=sys.stderr)
        sys.exit(1)


def cmd_link(args):
    tasks = load_tasks()
    for t in tasks:
        if t["id"] == args.id:
            t["session_id"] = args.session
            save_tasks(tasks)
            print(f"#{t['id']} linked to session {args.session}")
            return
    print(f"No task with ID {args.id}", file=sys.stderr)
    sys.exit(1)


def cmd_list(args):
    all_groups = load_all_tasks()
    has_any = False
    for source, tasks in all_groups:
        if not tasks:
            continue
        has_any = True
        if source != "local":
            print(f"── {source} ──")
        for t in tasks:
            _render_task(t, source=None if source == "local" else source)
    if not has_any:
        print("No tasks.")


def _renumber(tasks):
    """Reassign sequential IDs based on current order."""
    for i, t in enumerate(tasks, 1):
        t["id"] = i
    return tasks


def _read_input(prompt_text, timeout=10):
    """Read a line of input with timeout, in normal terminal mode."""
    sys.stdout.write(prompt_text)
    sys.stdout.flush()
    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    if ready:
        return sys.stdin.readline().strip()
    return None


def _trunc(s, width):
    return s[:width-1] + "~" if len(s) > width else s


def _render_task(t, source=None):
    """Render a single task line."""
    if t["status"] == "running":
        color, icon = "\033[33m", "●"
    elif t["status"] == "todo":
        color, icon = "\033[36m", "○"
    elif t["status"] == "failed":
        color, icon = "\033[31m", "✗"
    else:
        color, icon = "\033[32m", "✓"
    reset = "\033[0m"
    tool = _trunc(t.get("tool", "?"), 7)
    elapsed = format_elapsed(t["created_at"], t.get("completed_at"))
    cwd_short = _trunc(os.path.basename(t.get("cwd", "")), 17)
    desc = _trunc(t["description"], 23)
    prefix = f"{color}{t['id']:>2}{icon}{reset}" if not source else f"{color} {icon}{reset}"
    print(
        f"{prefix}"
        f"[{tool:7}]{desc:23}"
        f" {elapsed:>7} {cwd_short}"
    )


def _render_watch(tasks, remote_groups=None, mode_msg=None):
    """Render the watch screen."""
    sys.stdout.write("\033[2J\033[H")
    now = datetime.now().strftime("%m/%d %H:%M")
    print(f"\033[1mTASKBOARD\033[0m {now}")
    print(f"{'─' * 50}")
    has_any = bool(tasks)
    if tasks:
        for t in tasks:
            _render_task(t)
    if remote_groups:
        for source, rtasks in remote_groups:
            if rtasks:
                has_any = True
                print(f"\033[2m{'─' * 3} {source} {'─' * (44 - len(source))}\033[0m")
                for t in rtasks:
                    _render_task(t, source=source)
    if not has_any:
        print("No tasks.")
    if mode_msg:
        print(mode_msg)
    else:
        print("[a]dd [d]one [r]m [m]ove [h]ide [q]uit")
    sys.stdout.write("\033[J")
    sys.stdout.flush()


def cmd_watch(args):
    old_settings = termios.tcgetattr(sys.stdin)
    # Switch to alternate screen buffer (no scrollback)
    sys.stdout.write("\033[?1049h\033[?25l")
    sys.stdout.flush()
    try:
        while True:
            all_groups = load_all_tasks()
            tasks = all_groups[0][1]  # local tasks
            remote_groups = all_groups[1:]  # remote sources
            _render_watch(tasks, remote_groups)

            # Set raw mode for single keypress detection
            tty.setcbreak(sys.stdin)
            try:
                ready, _, _ = select.select([sys.stdin], [], [], 2)
                if ready:
                    key = sys.stdin.read(1)
                else:
                    continue
            finally:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)

            if key == "q":
                break
            elif key == "h":
                tasks = load_tasks()
                _render_watch(tasks, "Hide for how many minutes? (default: 2)")
                answer = _read_input(" > ")
                hide_mins = int(answer) if answer and answer.isdigit() else 2
                # i3: use scratchpad. Otherwise: xdotool minimize.
                method = None
                wid = None
                try:
                    r = subprocess.run(["i3-msg", "move scratchpad"], capture_output=True, text=True)
                    if r.returncode == 0 and "true" in r.stdout:
                        method = "i3"
                except FileNotFoundError:
                    pass
                if not method:
                    try:
                        r = subprocess.run(["xdotool", "getactivewindow"], capture_output=True, text=True)
                        wid = r.stdout.strip()
                        if wid:
                            subprocess.run(["xdotool", "windowminimize", wid], capture_output=True)
                            method = "xdotool"
                    except FileNotFoundError:
                        pass
                if method:
                    time.sleep(hide_mins * 60)
                    if method == "i3":
                        subprocess.run(["i3-msg", "scratchpad show"], capture_output=True)
                    elif wid:
                        subprocess.run(["xdotool", "windowactivate", wid], capture_output=True)
            elif key == "a":
                tasks = load_tasks()
                _render_watch(tasks, "Add task: (enter to cancel)")
                desc = _read_input(" > ")
                if desc:
                    task = {
                        "id": next_id(tasks),
                        "description": desc,
                        "status": "todo",
                        "tool": "manual",
                        "cwd": os.getcwd(),
                        "session_id": None,
                        "created_at": datetime.now().isoformat(),
                        "completed_at": None,
                    }
                    tasks.append(task)
                    save_tasks(_renumber(tasks))
            elif key == "d":
                tasks = load_tasks()
                _render_watch(tasks, "Mark done which task ID? (enter to cancel)")
                answer = _read_input(" > ")
                if answer and answer.isdigit():
                    tid = int(answer)
                    for t in tasks:
                        if t["id"] == tid and t["status"] == "running":
                            t["status"] = "done"
                            t["completed_at"] = datetime.now().isoformat()
                            break
                    save_tasks(tasks)
            elif key == "r":
                tasks = load_tasks()
                _render_watch(tasks, "Remove which task ID? (enter to cancel)")
                answer = _read_input(" > ")
                if answer and answer.isdigit():
                    tid = int(answer)
                    new_tasks = [t for t in tasks if t["id"] != tid]
                    if len(new_tasks) < len(tasks):
                        save_tasks(_renumber(new_tasks))
            elif key == "m":
                tasks = load_tasks()
                _render_watch(tasks, "Move which task ID?")
                answer = _read_input(" > ")
                if answer and answer.isdigit():
                    tid = int(answer)
                    _render_watch(tasks, f"Place task #{tid} before which ID? (0 = end)")
                    answer2 = _read_input(" > ")
                    if answer2 and answer2.isdigit():
                        target = int(answer2)
                        # Find and remove the task to move
                        moving = None
                        remaining = []
                        for t in tasks:
                            if t["id"] == tid:
                                moving = t
                            else:
                                remaining.append(t)
                        if moving:
                            if target == 0:
                                remaining.append(moving)
                            else:
                                inserted = False
                                new_list = []
                                for t in remaining:
                                    if t["id"] == target:
                                        new_list.append(moving)
                                        inserted = True
                                    new_list.append(t)
                                if not inserted:
                                    new_list.append(moving)
                                remaining = new_list
                            save_tasks(_renumber(remaining))
    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
        # Restore main screen buffer and cursor
        sys.stdout.write("\033[?25h\033[?1049l")
        sys.stdout.flush()


def cmd_clear(args):
    tasks = load_tasks()
    before = len(tasks)
    if args.all:
        tasks = []
    else:
        tasks = [t for t in tasks if t["status"] == "running"]
    save_tasks(tasks)
    print(f"Cleared {before - len(tasks)} tasks.")


def cmd_rm(args):
    tasks = load_tasks()
    new_tasks = [t for t in tasks if t["id"] != args.id]
    if len(new_tasks) == len(tasks):
        print(f"No task with ID {args.id}", file=sys.stderr)
        sys.exit(1)
    save_tasks(_renumber(new_tasks))
    print(f"Removed #{args.id}.")


def cmd_monitor(args):
    """Monitor a remote process's CPU and toggle running/done based on threshold."""
    desc = args.description
    threshold = args.threshold
    interval = args.interval
    check_cmd = args.cmd

    tasks = load_tasks()
    task = {
        "id": next_id(tasks),
        "description": desc,
        "status": "done",
        "tool": args.tool or "monitor",
        "cwd": os.getcwd(),
        "session_id": None,
        "created_at": datetime.now().isoformat(),
        "completed_at": None,
    }
    tasks.append(task)
    save_tasks(tasks)
    print(f"#{task['id']} monitoring: {desc} (CPU threshold: {threshold}%, every {interval}s)")

    is_active = False
    try:
        while True:
            try:
                result = subprocess.run(
                    " ".join(check_cmd), shell=True,
                    capture_output=True, text=True, timeout=30
                )
                cpu = float(result.stdout.strip()) if result.stdout.strip() else 0.0
            except (ValueError, subprocess.TimeoutExpired):
                cpu = 0.0

            if cpu >= threshold and not is_active:
                # Process ramped up
                is_active = True
                tasks = load_tasks()
                for t in tasks:
                    if t["id"] == task["id"]:
                        t["status"] = "running"
                        t["created_at"] = datetime.now().isoformat()
                        t["completed_at"] = None
                        break
                save_tasks(tasks)
                print(f"#{task['id']} running (CPU: {cpu:.0f}%)")

            elif cpu < threshold and is_active:
                # Process calmed down
                is_active = False
                tasks = load_tasks()
                for t in tasks:
                    if t["id"] == task["id"]:
                        t["status"] = "done"
                        t["completed_at"] = datetime.now().isoformat()
                        break
                save_tasks(tasks)
                print(f"#{task['id']} idle (CPU: {cpu:.0f}%)")

            time.sleep(interval)
    except KeyboardInterrupt:
        # Clean up - mark done on exit
        tasks = load_tasks()
        for t in tasks:
            if t["id"] == task["id"]:
                t["status"] = "done"
                t["completed_at"] = datetime.now().isoformat()
                break
        save_tasks(tasks)
        print(f"\n#{task['id']} monitoring stopped.")


def cmd_run(args):
    """Wrap a command: add task, run it, mark done/failed on exit."""
    tasks = load_tasks()
    task = {
        "id": next_id(tasks),
        "description": args.description,
        "status": "running",
        "tool": args.tool or "shell",
        "cwd": os.getcwd(),
        "session_id": None,
        "created_at": datetime.now().isoformat(),
        "completed_at": None,
    }
    tasks.append(task)
    save_tasks(tasks)
    print(f"#{task['id']} started: {task['description']}")

    try:
        result = subprocess.run(" ".join(args.cmd), shell=True)
        returncode = result.returncode
    except KeyboardInterrupt:
        returncode = 130

    tasks = load_tasks()
    for t in tasks:
        if t["id"] == task["id"]:
            t["status"] = "done" if returncode == 0 else "failed"
            t["completed_at"] = datetime.now().isoformat()
            t["exit_code"] = returncode
            break
    save_tasks(tasks)
    status = "done" if returncode == 0 else f"failed (exit {returncode})"
    print(f"#{task['id']} {status}: {task['description']}")
    sys.exit(returncode)


def cmd_hook_debug(args):
    """Dump hook input and environment for debugging."""
    debug_file = TASKBOARD_DIR / "hook_debug.json"
    TASKBOARD_DIR.mkdir(parents=True, exist_ok=True)
    raw_stdin = ""
    try:
        if not sys.stdin.isatty():
            raw_stdin = sys.stdin.read()
    except Exception:
        pass
    try:
        input_data = json.loads(raw_stdin) if raw_stdin else {}
    except (json.JSONDecodeError, ValueError):
        input_data = {"_raw": raw_stdin}
    debug = {
        "input": input_data,
        "env": {k: v for k, v in os.environ.items()},
    }
    with open(debug_file, "w") as f:
        json.dump(debug, f, indent=2)
    print(f"Debug written to {debug_file}")


def cmd_hook_start(args):
    """Called by UserPromptSubmit hook - reads JSON from stdin."""
    if sys.stdin.isatty():
        return
    try:
        ready, _, _ = select.select([sys.stdin], [], [], 3)
        if not ready:
            return
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError, OSError):
        return

    session_id = input_data.get("session_id", input_data.get("sessionId", ""))
    cwd = input_data.get("cwd", "")
    prompt = input_data.get("prompt", "")

    # Truncate prompt for description
    desc = prompt[:60].replace("\n", " ").strip()
    if len(prompt) > 60:
        desc += "..."
    if not desc:
        desc = "claude session"

    tasks = load_tasks()

    tool = "vscode" if os.environ.get("VSCODE_PID") else "claude"

    # One line per session (reuse across prompts within same session)
    for t in tasks:
        if t.get("tool") in ("claude", "vscode") and t.get("session_id") == session_id:
            t["status"] = "running"
            t["description"] = desc
            t["tool"] = tool
            t["session_id"] = session_id
            t["created_at"] = datetime.now().isoformat()
            t["completed_at"] = None
            save_tasks(tasks)
            return

    task = {
        "id": next_id(tasks),
        "description": desc,
        "status": "running",
        "tool": tool,
        "cwd": cwd or os.getcwd(),
        "session_id": session_id,
        "created_at": datetime.now().isoformat(),
        "completed_at": None,
    }
    tasks.append(task)
    save_tasks(tasks)


def cmd_hook_stop(args):
    """Called by Stop hook - reads JSON from stdin."""
    if sys.stdin.isatty():
        return
    try:
        ready, _, _ = select.select([sys.stdin], [], [], 3)
        if not ready:
            return
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError, OSError):
        return

    session_id = input_data.get("session_id", input_data.get("sessionId", ""))
    cwd = input_data.get("cwd", "")

    tasks = load_tasks()

    # Try session match first
    for t in reversed(tasks):
        if t["status"] != "running":
            continue
        if t.get("session_id") and t["session_id"] == session_id:
            t["status"] = "done"
            t["completed_at"] = datetime.now().isoformat()
            save_tasks(tasks)
            return

    # Fall back to cwd match
    for t in reversed(tasks):
        if t["status"] != "running":
            continue
        if t.get("cwd") == cwd:
            t["status"] = "done"
            t["completed_at"] = datetime.now().isoformat()
            save_tasks(tasks)
            return


def cmd_hook(args):
    """Legacy hook handler - delegates to stop."""
    cmd_hook_stop(args)


def main():
    parser = argparse.ArgumentParser(
        prog="taskboard",
        description="Track AI tasks across VSCode windows and CLI sessions.",
    )
    sub = parser.add_subparsers(dest="command")

    p_add = sub.add_parser("add", help="Add a new running task")
    p_add.add_argument("description", help="What you're working on")
    p_add.add_argument("-t", "--tool", default=None, help="Tool name (claude, copilot)")
    p_add.add_argument("-c", "--cwd", default=None, help="Working directory (default: cwd)")
    p_add.add_argument("-s", "--session", default=None, help="Session ID")

    p_done = sub.add_parser("done", help="Mark a task done")
    p_done.add_argument("id", type=int, nargs="?", help="Task ID")
    p_done.add_argument("-s", "--session", default=None, help="Match by session ID")
    p_done.add_argument("-c", "--cwd", default=None, help="Match by cwd")

    p_link = sub.add_parser("link", help="Link a task to a session ID")
    p_link.add_argument("id", type=int, help="Task ID")
    p_link.add_argument("session", help="Session ID")

    sub.add_parser("list", help="List all tasks")
    sub.add_parser("watch", help="Live dashboard")

    p_clear = sub.add_parser("clear", help="Clear completed tasks")
    p_clear.add_argument("-a", "--all", action="store_true", help="Clear all tasks")

    p_rm = sub.add_parser("rm", help="Remove a specific task")
    p_rm.add_argument("id", type=int, help="Task ID to remove")

    sub.add_parser("hook", help="Stop hook handler (reads JSON from stdin)")
    sub.add_parser("hook-start", help="Start hook handler (reads JSON from stdin)")
    sub.add_parser("hook-stop", help="Stop hook handler (reads JSON from stdin)")
    sub.add_parser("hook-debug", help="Dump hook input and env to ~/.taskboard/hook_debug.json")

    p_run = sub.add_parser("run", help="Run a command and track it as a task")
    p_run.add_argument("description", help="Task description")
    p_run.add_argument("-t", "--tool", default=None, help="Tool name")
    p_run.add_argument("cmd", nargs=argparse.REMAINDER, help="Command to run (after --)")

    p_mon = sub.add_parser("monitor", help="Monitor a process CPU and toggle running/done")
    p_mon.add_argument("description", help="Task description")
    p_mon.add_argument("-t", "--tool", default=None, help="Tool name")
    p_mon.add_argument("--threshold", type=float, default=10.0, help="CPU%% threshold (default: 10)")
    p_mon.add_argument("--interval", type=int, default=5, help="Poll interval in seconds (default: 5)")
    p_mon.add_argument("cmd", nargs=argparse.REMAINDER, help="Command that outputs CPU%% (after --)")

    args = parser.parse_args()

    # Strip leading '--' from remainder args for 'run' and 'monitor' commands
    for cmd_name in ("run", "monitor"):
        if args.command == cmd_name and hasattr(args, "cmd"):
            if args.cmd and args.cmd[0] == "--":
                args.cmd = args.cmd[1:]
            if not args.cmd:
                print(f"Provide a command after -- (e.g., taskboard {cmd_name} \"desc\" -- command)", file=sys.stderr)
                sys.exit(1)

    commands = {
        "add": cmd_add,
        "done": cmd_done,
        "link": cmd_link,
        "list": cmd_list,
        "watch": cmd_watch,
        "clear": cmd_clear,
        "rm": cmd_rm,
        "hook": cmd_hook,
        "hook-start": cmd_hook_start,
        "hook-stop": cmd_hook_stop,
        "hook-debug": cmd_hook_debug,
        "run": cmd_run,
        "monitor": cmd_monitor,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
