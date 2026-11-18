# 05.10.26
# ruff: noqa: E402

import ast
import sys
from pathlib import Path

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

from VibraVid.cli import run as cli_run
from VibraVid.cli.command import equivalent_command


def _imported_modules(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name


def test_the_gui_does_not_import_the_cli_run_module():
    offenders = [
        str(path.relative_to(workspace_root))
        for path in (workspace_root / "GUI").rglob("*.py")
        if "VibraVid.cli.run" in set(_imported_modules(path))
    ]
    assert offenders == []


def test_the_equivalent_command_builder_is_shared_with_the_cli():
    assert cli_run.equivalent_command_builder is equivalent_command.equivalent_command_builder
    argv = equivalent_command.equivalent_command_builder.build_argv_from_params(site="mysite", search="a title", item="1")
    assert argv[argv.index("-i") + 1] == "mysite" and argv[argv.index("-s") + 1] == "a title"


def test_execute_hooks_is_the_same_function_for_the_cli_and_the_gui():
    from VibraVid.utils.hooks import execute_hooks

    assert cli_run.execute_hooks is execute_hooks
