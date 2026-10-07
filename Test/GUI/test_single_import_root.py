# 05.10.26
# ruff: noqa: E402

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUI_DIR = ROOT / "GUI"


def test_gui_code_never_names_the_package_through_the_gui_root():
    offenders = []
    for path in GUI_DIR.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            module = node.module if isinstance(node, ast.ImportFrom) else None
            literal = node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
            for name in [module, literal, *names]:
                if name and name.startswith("GUI.searchapp"):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}: {name}")
    assert offenders == []


def test_loading_the_django_app_imports_every_module_once():
    probe = textwrap.dedent(
        f"""
        import os, sys
        sys.path[:0] = [{str(ROOT)!r}, {str(GUI_DIR)!r}]
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webgui.settings")
        import django
        django.setup()
        import searchapp.api as api
        import searchapp.api.base as api_base
        import searchapp.api_bot as bot
        import searchapp.arr.downloader_service  # noqa: F401  (imports searchapp.api lazily, by the same name)
        from searchapp.views import _shared
        print("DUPLICATES=" + ",".join(sorted(m for m in sys.modules if m.startswith("GUI.searchapp"))))
        print("SAME_REGISTRY=" + str(_shared.get_api is api.get_api and bot.get_api is api.get_api and _shared.Entries is api_base.Entries and bot.Entries is api_base.Entries))
        """
    )
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=180, encoding="utf-8", errors="replace")
    assert result.returncode == 0, result.stderr[-800:]
    assert "DUPLICATES=\n" in result.stdout + "\n"
    assert "SAME_REGISTRY=True" in result.stdout
