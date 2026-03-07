# taskboard

A CLI dashboard to track tasks — manual to-dos, AI agent sessions, long-running commands, and remote jobs.

Task data is stored in `~/.taskboard/tasks.json`.

## Installation

Symlink the script into your PATH:

```bash
ln -sf ~/code/taskboard/taskboard.py ~/.local/bin/taskboard
```

## Live Dashboard

```bash
taskboard watch
```

Interactive auto-refreshing display with keyboard controls:

- **`a`** — add a new to-do task
- **`d`** — mark a task done by ID
- **`r`** — remove a task by ID
- **`m`** — reorder (move a task before another ID, or 0 for end)
- **`q`** — quit

Tasks display in the order you set — top is highest priority. IDs renumber automatically after moves/removes.

## Task Statuses

| Status | Icon | Color | Meaning |
|--------|------|-------|---------|
| TODO | ○ | cyan | Manual to-do item |
| RUNNING | ● | yellow | Active process or agent session |
| DONE | ✓ | green | Completed |
| FAILED | ✗ | red | Command exited with error |

## Manual To-Dos

Add from the dashboard (`a` key) or CLI:

```bash
taskboard add "refactor auth module"
taskboard add "review PR" -c /path/to/project
```

Manual tasks get status `TODO` (not `RUNNING`). Remove them when finished — there's no need to mark them done.

## Claude Code Integration

Auto-tracks agent sessions. When you submit a prompt, the task shows as RUNNING. When Claude finishes responding, it flips to DONE. One line per project directory — subsequent prompts update the same entry.

### Setup

Add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          { "type": "command", "command": "taskboard hook-start" }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          { "type": "command", "command": "taskboard hook-stop" }
        ]
      }
    ]
  }
}
```

Shows as tool `claude` in the dashboard.

## VS Code Copilot Integration

Same hooks work for VS Code Copilot agent mode. Shows as tool `vscode` in the dashboard (detected via `VSCODE_PID` environment variable).

### Setup

1. The hooks in `~/.claude/settings.json` (above) are shared with VS Code
2. In VS Code settings, enable:
   - **Chat: Use Hooks** — checked
   - **Chat: Use Claude Hooks** — checked (required for Claude-format hook files)
3. Reload VS Code (`Ctrl+Shift+P` → "Developer: Reload Window")

Verify hooks are loaded: type `/hooks` in the Copilot chat.

## Wrapping Commands (`taskboard run`)

Track any command — local or remote. Adds a RUNNING task, executes the command, marks DONE or FAILED on exit.

```bash
taskboard run "description" -- command args...
```

### Local commands

```bash
taskboard run "run tests" -- pytest -x
taskboard run "build project" -- make -j8
taskboard run "copy dataset" -- rsync -av /src /dst
```

### Remote commands via SSH

The local `ssh` process exits when the remote command finishes — no callbacks needed.

```bash
taskboard run "train model" -- ssh sagemaker "python train.py"
taskboard run "preprocess + train" -- ssh sagemaker "cd /data && python preprocess.py && python train.py"
```

For complex remote workflows, put them in a script:

```bash
taskboard run "full pipeline" -- ssh sagemaker "bash -s" < ./remote_pipeline.sh
```

### Options

```bash
taskboard run "deploy" -t deploy -- ./deploy.sh   # custom tool name
```

Exit code is preserved, so you can chain: `taskboard run "test" -- pytest && echo "passed"`.

Runs through the shell, so builtins, pipes, and redirects all work.

## CPU Monitoring (`taskboard monitor`)

Monitor a process's CPU usage and automatically toggle RUNNING/DONE based on a threshold. Useful for tracking remote debug sessions or batch jobs where you want to know when the process is actively working vs idle/paused.

```bash
taskboard monitor "description" -- command-that-outputs-cpu-percent
```

The command after `--` must output a single number (CPU %).

### Examples

```bash
# Monitor a local python process by PID
taskboard monitor "debug session" -- bash -c 'ps -p $(pgrep -f my_script.py) -o %cpu --no-headers || echo 0'

# Monitor a remote process on sagemaker
taskboard monitor "remote training" -- ssh sagemaker 'ps -p $(pgrep -f train.py) -o %cpu --no-headers || echo 0'

# Monitor by process name
taskboard monitor "python work" -- bash -c 'ps -C python3 -o %cpu --no-headers | head -1 || echo 0'
```

### Options

```bash
--threshold 10   # CPU% to trigger running (default: 10)
--interval 5     # seconds between polls (default: 5)
-t tool          # custom tool name (default: monitor)
```

Cycles automatically: CPU spikes above threshold → RUNNING, drops below → DONE. Press `Ctrl+C` to stop monitoring.

## Other Commands

```bash
taskboard list                  # show all tasks
taskboard done 3                # mark task #3 done
taskboard done -s session-id    # mark done by session
taskboard done -c /path         # mark done by cwd
taskboard rm 3                  # remove task #3
taskboard clear                 # remove completed/failed tasks
taskboard clear -a              # remove all tasks
taskboard link 3 session-id     # link task to a session
```
