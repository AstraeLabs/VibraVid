# Hook System

Execute custom scripts at specific points in the download lifecycle. Hooks are configured in `config.json` under the `HOOKS` key.

**Available stages:**
- `pre_run` — runs before the main flow starts
- `pre_download` — runs once per individual item, right before that movie/episode starts downloading
- `post_download` — runs after each individual download completes (in the GUI, once per item)
- `post_run` — runs once when the overall execution ends

```json
{
  "HOOKS": {
    "pre_run": [
      {
        "name": "prepare-env",
        "type": "python",
        "path": "scripts/prepare.py",
        "args": ["--clean"],
        "env": { "MY_FLAG": "1" },
        "cwd": "~",
        "os": ["linux", "darwin"],
        "timeout": 60,
        "enabled": true,
        "continue_on_error": true
      }
    ],
    "pre_download": [
      {
        "name": "pre-download-env",
        "type": "python",
        "path": "/app/script.py",
        "args": ["{download_title}"],
        "timeout": 30,
        "enabled": true,
        "continue_on_error": true
      }
    ],
    "post_download": [
      {
        "name": "post-download-env",
        "type": "python",
        "path": "/app/script.py",
        "args": ["{download_path}"],
        "env": { "MY_FLAG": "1" },
        "cwd": "~",
        "os": ["linux"],
        "timeout": 60,
        "enabled": true,
        "continue_on_error": true
      }
    ],
    "post_run": [
      {
        "name": "notify",
        "type": "bash",
        "command": "echo 'Download finished: {download_title}'",
        "allow_inline_template": true
      }
    ]
  }
}
```

## Hook Options

| Key | Description |
|-----|-------------|
| `name` | Descriptive label for the hook |
| `type` | Script type: `python`, `bash`, `sh`, `shell`, `bat`, `cmd` |
| `path` | Path to script file (alternative to `command`) |
| `command` | Inline command to execute (alternative to `path`). Note: `args` are ignored when using `command` |
| `allow_inline_template` | If `true`, expand `{download_title}`-style placeholders inside `command` itself (off by default — `command` is otherwise run verbatim, unlike `path`/`args`/`cwd` which always expand placeholders) |
| `args` | List of arguments passed to the script |
| `env` | Additional environment variables as key-value pairs |
| `cwd` | Working directory for execution (supports `~` and env vars) |
| `os` | Optional OS filter: `["windows"]`, `["darwin"]`, `["linux"]`, or any combination |
| `timeout` | Maximum execution time in seconds (hook fails if exceeded) |
| `enabled` | Enable or disable the hook without removing it |
| `continue_on_error` | If `false`, stops execution when the hook fails |

## Hook Types

- **Python:** runs with the current Python interpreter
- **Bash / sh / shell:** executed via `/bin/bash -c` on macOS/Linux
- **Bat / cmd / shell:** executed via `cmd /c` on Windows
- **Inline commands:** use `command` instead of `path` for simple one-liners

## Context Placeholders

| Placeholder | Description |
|-------------|-------------|
| `{download_path}` | Absolute path of the downloaded file |
| `{download_dir}` | Directory containing the downloaded file |
| `{download_filename}` | Filename of the downloaded file |
| `{download_id}` | Internal download identifier |
| `{download_title}` | Download title |
| `{download_site}` | Source site name |
| `{download_media_type}` | Media type |
| `{download_status}` | Final download status |
| `{download_error}` | Error message, if any |
| `{download_success}` | `1` on success, `0` on failure |
| `{stage}` | Current hook stage |

The same values are also exposed as environment variables with the `SC_` prefix (e.g. `SC_DOWNLOAD_PATH`, `SC_DOWNLOAD_SUCCESS`, `SC_HOOK_STAGE`).
