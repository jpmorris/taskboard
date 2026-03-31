# taskboard

sdffA CLI dashboard to track tasks — manual to-dos, AI agent sessions,
long-running commands, and remote jobs.

Task data is stored in `~/.taskboard/tasks.json`.

## Installation

Symlink the script into your PATH:

```bash
ln -sf ~/code/taskboard/taskboard.py ~/.local/bin/taskboard
chmod +x ~/code/taskboard/taskboard.py
```

Ensure `~/.local/bin` is in your PATH.

## Live Dashboard

```bash
taskboard watch
```

Interactive auto-refreshing display with keyboard controls:

- **`a`** — add a new to-do task
- **`d`** — mark a task done by ID
- **`r`** — remove a task by ID
- **`m`** — reorder (move a task before another ID, or 0 for end)
- **`h`** — hide window temporarily (i3 scratchpad)
- **`q`** — quit

Tasks display in the order you set — top is highest priority. IDs renumber
automatically after moves/removes.

## Task Statuses

| Status  | Icon | Color  | Meaning                         |
| ------- | ---- | ------ | ------------------------------- |
| TODO    | ○    | cyan   | Manual to-do item               |
| RUNNING | ●    | yellow | Active process or agent session |
| DONE    | ✓    | green  | Completed                       |
| FAILED  | ✗    | red    | Command exited with error       |

## Manual To-Dos

Add from the dashboard (`a` key) or CLI:

```bash
taskboard add "refactor auth module"
taskboard add "review PR" -c /path/to/project
```

Manual tasks get status `TODO` (not `RUNNING`). Remove them when finished.

## Claude Code Integration

Auto-tracks agent sessions. When you submit a prompt, the task shows as RUNNING.
When Claude finishes responding, it flips to DONE. Each session gets its own
line — multiple agents in the same directory show separately.

### Setup

Add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [{ "type": "command", "command": "taskboard hook-start" }]
      }
    ],
    "Stop": [
      {
        "hooks": [{ "type": "command", "command": "taskboard hook-stop" }]
      }
    ]
  }
}
```

Shows as tool `claude` in the dashboard.

## VS Code Copilot Integration

Same hooks work for VS Code Copilot agent mode. Shows as tool `vscode` in the
dashboard (detected via `VSCODE_CWD` environment variable).

### Setup

1. The hooks in `~/.claude/settings.json` (above) are shared with VS Code
2. In VS Code settings, enable:
   - **Chat: Use Hooks** — checked
   - **Chat: Use Claude Hooks** — checked (required for Claude-format hook
     files)
3. Reload VS Code (`Ctrl+Shift+P` → "Developer: Reload Window")

Verify hooks are loaded: type `/hooks` in the Copilot chat.

### WSL (Windows)

If running Claude Code in WSL, put the hooks in the WSL-side
`~/.claude/settings.json`. For VS Code on Windows with WSL remote, the extension
host runs in WSL so the same config works.

## Copilot CLI Integration

Auto-tracks Copilot CLI agent sessions. Shows as tool `copilot` in the
dashboard.

### Setup

Create `.github/hooks/taskboard.json` in your project repository:

```json
{
  "version": 1,
  "hooks": {
    "userPromptSubmitted": [
      {
        "type": "command",
        "bash": "taskboard hook-copilot-start"
      }
    ],
    "sessionEnd": [
      {
        "type": "command",
        "bash": "taskboard hook-copilot-stop"
      }
    ]
  }
}
```

Unlike Claude hooks (which are global), Copilot CLI hooks are per-project
(loaded from `.github/hooks/*.json` in the repository).

Since Copilot CLI doesn't include a session ID in hook payloads, tasks are
matched by working directory — one task per active `cwd`. If a session ends with
an error, it shows as `failed`.

### Adding taskboard tracking to a new repo

Run this one-liner from the repo root to create the hook file:

```bash
mkdir -p .github/hooks && cp ~/workspace/taskboard/.github/hooks/taskboard.json .github/hooks/
```

Or manually create `.github/hooks/taskboard.json` with the JSON above. Then
start a fresh Copilot CLI session from that directory — hooks are loaded at
session start based on cwd, so **you must `cd` into the repo before launching**
(not `/resume`).

## Remote Agent Tracking (Reverse Tunnel)

Track AI agent sessions running on a remote server (e.g. SageMaker) on your
local dashboard. No taskboard installation needed on the remote — just `curl`.

### How it works

1. Your SSH connection script adds reverse port forwards (`-R`)
2. Local machine runs `socat` listeners that pipe data to
   `taskboard hook-start/stop`
3. Remote `~/.claude/settings.json` uses `curl` to POST hook data through the
   tunnel
4. Tasks appear in your local `tasks.json` — no syncing, no polling

### Local machine setup

Install socat:

```bash
# Arch/Manjaro
sudo pacman -S socat
# Ubuntu/Debian/WSL
sudo apt install socat
```

Start listeners (your connection script should start these when the tunnel is
up, kill them on disconnect):

```bash
socat TCP-LISTEN:9998,reuseaddr,fork EXEC:"taskboard hook-start" &
socat TCP-LISTEN:9999,reuseaddr,fork EXEC:"taskboard hook-stop" &
```

### SSH tunnel setup

Add reverse port forwards to your SSH connection. The exact command depends on
your tunnel setup, but the key flags are:

```bash
ssh ... -R 9998:localhost:9998 -R 9999:localhost:9999 remote-host
```

For SSM-based connections, add the `-R` flags to whichever SSH command
establishes the tunnel.

### Remote server setup

On the remote server (SageMaker etc.), create `~/.claude/settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "curl -sf http://localhost:9998 -d @-"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "curl -sf http://localhost:9999 -d @-"
          }
        ]
      }
    ]
  }
}
```

Also enable in VS Code settings on the remote:

- **Chat: Use Hooks** — checked
- **Chat: Use Claude Hooks** — checked

That's it. When an agent fires on the remote, `curl` POSTs the hook JSON through
the reverse tunnel to your local socat, which pipes it to
`taskboard hook-start/stop`. Tasks appear on your local dashboard instantly.

If the tunnel is down, `curl -sf` fails silently — no impact on the agent.

## Wrapping Commands (`taskboard run`)

Track any command — local or remote. Adds a RUNNING task, executes the command,
marks DONE or FAILED on exit.

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

The local `ssh` process exits when the remote command finishes — no callbacks
needed.

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

Exit code is preserved, so you can chain:
`taskboard run "test" -- pytest && echo "passed"`.

Runs through the shell, so builtins, pipes, and redirects all work.

## CPU Monitoring (`taskboard monitor`)

Monitor a process's CPU usage and automatically toggle RUNNING/DONE based on a
threshold. Useful for tracking remote debug sessions or batch jobs.

```bash
taskboard monitor "description" -- command-that-outputs-cpu-percent
```

The command after `--` must output a single number (CPU %).

### Examples

```bash
taskboard monitor "debug session" -- bash -c 'ps -p $(pgrep -f my_script.py) -o %cpu --no-headers || echo 0'
taskboard monitor "remote training" -- ssh sagemaker 'ps -p $(pgrep -f train.py) -o %cpu --no-headers || echo 0'
```

### Options

```bash
--threshold 10   # CPU% to trigger running (default: 10)
--interval 5     # seconds between polls (default: 5)
-t tool          # custom tool name (default: monitor)
```

Cycles automatically: CPU above threshold → RUNNING, below → DONE. `Ctrl+C` to
stop.

## i3 Floating Dashboard

Auto-float a small always-visible taskboard window.

### Setup

Add to `~/.config/i3/config`:

```
for_window [title="^TASKBOARD$"] floating enable, sticky enable, resize set 600 300, move position 3240 0
exec --no-startup-id taskboard-launch
```

Create a Terminator profile in `~/.config/terminator/config`:

```ini
[profiles]
  [[taskboard]]
    font = SauceCodePro Nerd Font 5
    show_titlebar = False
    use_system_font = False
```

The `taskboard-launch` script opens Terminator with this profile.

## Windows Shortcut (Pin to Start)

You can't pin a `.bat` file directly to the Start menu, so use a two-file
approach:

### 1. Create `taskboard.bat`

Save this anywhere (e.g. `C:\Users\<you>\taskboard.bat`):

```bat
wsl -e /bin/bash -c "~/.local/bin/taskboard watch"
```

Using `~` avoids hardcoding your username.

### 2. Create a shortcut to pin

1. Right-click `taskboard.bat` → **Create shortcut**
2. Edit shortcut properties:
   - **Target:** `C:\Windows\System32\cmd.exe /c "C:\Users\<you>\taskboard.bat"`
   - **Run:** Minimized (optional, hides the CMD flash)
3. To pin to Start: move the `.lnk` shortcut to:
   ```
   %APPDATA%\Microsoft\Windows\Start Menu\Programs\
   ```
   Then right-click it in Start and choose **Pin to Start**.

### Windows Terminal

```powershell
wt.exe -p "Taskboard" --size 80,15 --pos 1800,0 -F
```

`-F` is focus mode (no tabs/title bar). Set `"historySize": 0` in the Windows
Terminal profile to prevent scrollback ghosting.

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
