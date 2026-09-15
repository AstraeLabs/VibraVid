# 15.09.26
# ruff: noqa: E402

import argparse
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

import pytest

from VibraVid.cli import run


def _parser_with_dest(dest: str, default):
    parser = argparse.ArgumentParser(add_help=False)
    if default is False:
        parser.add_argument(f"--{dest.replace('_', '-')}", dest=dest, action="store_true")
    else:
        parser.add_argument(f"--{dest.replace('_', '-')}", dest=dest, default=default)
    return parser


@pytest.fixture
def patch_persisted(monkeypatch):
    def _patch(values: dict):
        monkeypatch.setattr(run, "resolve_persisted_site_options", lambda site: values)
    return _patch


def test_persisted_value_used_when_no_cli_flag_given(patch_persisted):
    patch_persisted({"optimize_audio": True, "show_bonus": True})
    parser = _parser_with_dest("optimize_audio", False)
    args = parser.parse_args([])  # flag not typed -> stays at its default (False)

    site_options = run.build_site_options(args, parser, ["optimize_audio"], "primevideo")

    assert site_options["optimize_audio"] is True


def test_explicit_cli_flag_overrides_persisted_value(patch_persisted):
    patch_persisted({"quality": "UHD"})
    parser = _parser_with_dest("quality", None)
    args = parser.parse_args(["--quality", "SD"])

    site_options = run.build_site_options(args, parser, ["quality"], "primevideo")

    assert site_options["quality"] == "SD"


def test_no_persisted_value_falls_back_to_cli_default(patch_persisted):
    patch_persisted({})
    parser = _parser_with_dest("optimize_audio", False)
    args = parser.parse_args([])

    site_options = run.build_site_options(args, parser, ["optimize_audio"], "primevideo")

    assert site_options["optimize_audio"] is False


def test_no_site_module_name_skips_persisted_lookup(monkeypatch):
    called = []
    monkeypatch.setattr(run, "resolve_persisted_site_options", lambda site: called.append(site) or {})
    parser = _parser_with_dest("optimize_audio", False)
    args = parser.parse_args([])

    run.build_site_options(args, parser, ["optimize_audio"], None)

    assert called == []
