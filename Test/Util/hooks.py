# Simple manual test for pre/post hooks execution

import os
import sys
import tempfile

from StreamingCommunity.Util.config_json import config_manager
from StreamingCommunity.run import execute_hooks


def main():
    # Prepare temp folder and python script
    with tempfile.TemporaryDirectory() as tmp:
        out_file = os.path.join(tmp, "hook_out.txt")
        script_path = os.path.join(tmp, "hook_script.py")

        with open(script_path, "w", encoding="utf-8") as f:
            f.write(
                "import os\n"
                "with open(os.environ.get('HOOK_OUT'), 'a', encoding='utf-8') as fp:\n"
                "    fp.write('ran\\n')\n"
            )

        original_hooks = (
            config_manager.config.get("HOOKS", {}).copy()
            if config_manager.config.get("HOOKS")
            else {}
        )

        try:
            # Configure hooks: run the python script pre and post
            config_manager.config.setdefault("HOOKS", {})
            config_manager.config["HOOKS"]["pre_run"] = [
                {
                    "name": "test-pre",
                    "type": "python",
                    "path": script_path,
                    "env": {"HOOK_OUT": out_file},
                    "enabled": True,
                    "continue_on_error": False,
                }
            ]
            config_manager.config["HOOKS"]["post_run"] = [
                {
                    "name": "test-post",
                    "type": "python",
                    "path": script_path,
                    "env": {"HOOK_OUT": out_file},
                    "enabled": True,
                    "continue_on_error": False,
                }
            ]

            # Execute and assert
            execute_hooks("pre_run")
            execute_hooks("post_run")

            with open(out_file, "r", encoding="utf-8") as fp:
                content = fp.read().strip()
            assert content.splitlines() == ["ran", "ran"], (
                f"Unexpected content: {content!r}"
            )

            print("OK: hooks executed (pre + post)")

        finally:
            # Restore original hooks configuration
            if original_hooks:
                config_manager.config["HOOKS"] = original_hooks
            else:
                config_manager.config.pop("HOOKS", None)


if __name__ == "__main__":
    sys.exit(main())