# Hook System

Run custom scripts at points of the download lifecycle, configured under `HOOKS` in `config.toml`.

| Stage | Runs |
|-------|------|
| `pre_run` | before the main flow starts |
| `pre_download` | once per item, right before that movie/episode starts |
| `post_download` | after each individual download (GUI: once per item) |
| `post_run` | once when the whole execution ends |

```toml
# Replace the matching "pre_run = []" / "pre_download = []" / ... line of [HOOKS] with these tables
# (a key can't be declared twice in TOML).
[[HOOKS.pre_run]]
name = "prepare-env"
type = "python"
path = "scripts/prepare.py"
args = ["--clean"]
env = { MY_FLAG = "1" }
cwd = "~"
os = ["linux", "darwin"]
timeout = 60
enabled = true
continue_on_error = true

[[HOOKS.pre_download]]
name = "pre-download-env"
type = "python"
path = "/app/script.py"
args = ["{download_title}"]
timeout = 30

[[HOOKS.post_download]]
name = "post-download-env"
type = "python"
path = "/app/script.py"
args = ["{download_path}"]
env = { MY_FLAG = "1" }
cwd = "~"
os = ["linux"]
timeout = 60

[[HOOKS.post_run]]
name = "notify"
type = "bash"
command = "echo 'Download finished: {download_title}'"
allow_inline_template = true
```

## Options

| Key | Description |
|-----|-------------|
| `name` | Label |
| `type` | `python`, `bash`, `sh`, `shell`, `bat`, `cmd` |
| `path` | Script file (alternative to `command`) |
| `command` | Inline command (alternative to `path`); `args` are ignored |
| `allow_inline_template` | `true` expands `{download_title}`-style placeholders inside `command` (off by default: `command` runs verbatim, while `path`/`args`/`cwd` always expand) |
| `args` | Arguments passed to the script |
| `env` | Extra environment variables |
| `cwd` | Working directory (supports `~` and env vars) |
| `os` | Optional filter: any of `"windows"`, `"darwin"`, `"linux"` |
| `timeout` | Max seconds (the hook fails if exceeded) |
| `enabled` | Disable without removing |
| `continue_on_error` | `false` = stop execution when the hook fails |

## Types

- **Python**: current interpreter.
- **Bash / sh / shell**: `/bin/bash -c` on macOS/Linux.
- **Bat / cmd / shell**: `cmd /c` on Windows.
- **Inline**: `command` instead of `path` for one-liners.

## Placeholders

| Placeholder | Description |
|-------------|-------------|
| `{download_path}` | Absolute path of the downloaded file |
| `{download_dir}` | Its directory |
| `{download_filename}` | Its filename |
| `{download_id}` | Internal download id |
| `{download_title}` | Title |
| `{download_site}` | Source site |
| `{download_media_type}` | Media type |
| `{download_status}` | Final status |
| `{download_error}` | Error message, if any |
| `{download_success}` | `1` success, `0` failure |
| `{stage}` | Current hook stage |

The same values are exposed as environment variables with an `SC_` prefix (`SC_DOWNLOAD_PATH`, `SC_DOWNLOAD_SUCCESS`, `SC_HOOK_STAGE`, ...).
