# 18.10.26

import sys
from pathlib import Path

import pytest

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

from VibraVid.core.drm import widevine
from VibraVid.core.drm.manager import DRMManager
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.setup import device_install, system
from VibraVid.setup.device_install import parse_cdm_cli, resolve_cdm, validate_cdm_settings
from VibraVid.utils import config_manager

DL_WIDEVINE = {"type": "custom_api_1", "secret": "k", "device_name": "L3"}
REGISTRY = {"remote_widevine_1": DL_WIDEVINE, "remote_playready_1": {"type": "custom_api_1", "secret": "k", "device_name": "SL3"}}


class FakeSearcher:
    def search(self, _ext=None, filename=None):
        return f"/bin/{filename}" if filename in ("mine.wvd", "mine.prd") else None


@pytest.fixture
def setup_cdm(monkeypatch):
    login_data = config_manager._login_data  # the dict the config accessor reads, whatever the attribute points to later

    def _apply(service_cdm=None, global_cdm="search", cli=None):
        monkeypatch.setitem(login_data, "mysite", {"cdm": service_cdm})
        monkeypatch.setattr(config_manager, "_login_data", {"mysite": {"cdm": service_cdm}})  # the startup check lists sections: ignore the real login.toml
        monkeypatch.setitem(config_manager._config_data, "DRM", {"cdm": global_cdm})
        monkeypatch.setattr(device_install, "get_remote_cdm_registry", lambda: REGISTRY)
        monkeypatch.setattr(device_install, "DeviceSearcher", FakeSearcher)
        monkeypatch.setattr(system, "get_wvd_path", lambda: "/default/default.wvd")
        monkeypatch.setattr(system, "get_prd_path", lambda: "/default/default.prd")
        monkeypatch.setattr(context_tracker, "cdm_override", cli)

    return _apply


def test_origin_names_the_login_section_the_global_setting_or_search(setup_cdm):
    setup_cdm("mine.wvd", global_cdm="remote_playready_1")
    resolved = resolve_cdm("mysite")
    assert resolved.widevine_origin == "login.toml 'mysite'"
    assert resolved.playready_origin == "config.toml [DRM]"

    setup_cdm(None, global_cdm="search")
    resolved = resolve_cdm("mysite")
    assert resolved.widevine_origin == "search"


def test_origin_when_no_cdm_is_set_at_all(setup_cdm):
    setup_cdm(None, global_cdm=None)
    assert resolve_cdm("mysite").widevine_origin == "search"
    assert resolve_cdm(None).playready_origin == "search"


def test_origin_includes_the_quality_rule_that_matched(setup_cdm):
    setup_cdm([{"min_height": 1080, "cdm": "remote_widevine_1"}, {"cdm": "mine.wvd"}])
    assert resolve_cdm("mysite", 1080).widevine_origin == "login.toml 'mysite', min_height 1080"
    assert resolve_cdm("mysite", 480).widevine_origin == "login.toml 'mysite'"


def test_cli_override_beats_the_service_and_the_global_setting(setup_cdm):
    setup_cdm("mine.wvd", global_cdm="remote_playready_1", cli=["remote_widevine_1"])
    resolved = resolve_cdm("mysite")
    assert resolved.widevine_remote == DL_WIDEVINE and resolved.widevine_origin == "--cdm"
    assert resolved.playready_remote is not None and resolved.playready_origin == "config.toml [DRM]"  # the option only covers Widevine


def test_cli_override_with_a_quality_rule(setup_cdm):
    setup_cdm(cli=parse_cdm_cli(['[{"min_height": 1080, "cdm": "remote_widevine_1"}, {"cdm": "mine.wvd"}]']))
    assert resolve_cdm("mysite", 2160).widevine_remote == DL_WIDEVINE
    assert resolve_cdm("mysite", 720).wvd_path == "/bin/mine.wvd"


def test_parse_cdm_cli():
    assert parse_cdm_cli(None) is None and parse_cdm_cli([]) is None and parse_cdm_cli([" "]) is None
    assert parse_cdm_cli(["a.wvd", "b.prd"]) == ["a.wvd", "b.prd"]
    assert parse_cdm_cli(['["a.wvd", "b.prd"]', "c"]) == ["a.wvd", "b.prd", "c"]
    assert parse_cdm_cli(['{"max_height": 720, "cdm": "a.wvd"}']) == [{"max_height": 720, "cdm": "a.wvd"}]
    with pytest.raises(ValueError, match="invalid JSON"):
        parse_cdm_cli(["[oops"])


def test_a_clean_setup_has_no_problems(setup_cdm):
    setup_cdm(["mine.wvd", {"min_height": 1080, "cdm": "remote_widevine_1"}], global_cdm="search")
    assert validate_cdm_settings() == []


def test_missing_file_and_unknown_id_are_reported_with_their_origin(setup_cdm):
    setup_cdm(["absent.wvd", {"min_height": 1080, "cdm": "remote_widevine_9"}])
    problems = validate_cdm_settings()
    assert any("absent.wvd" in p and "'mysite' in login.toml" in p for p in problems)
    assert any("remote_widevine_9" in p and "known ids: remote_playready_1, remote_widevine_1" in p for p in problems)  # a rule not met at any height is still checked


def test_problems_in_the_global_setting_and_in_the_cli_option(setup_cdm):
    setup_cdm(None, global_cdm="gone.prd", cli=["nope"])
    problems = validate_cdm_settings(context_tracker.cdm_override)
    assert any("gone.prd" in p and "[DRM] in config.toml" in p for p in problems)
    assert any("'nope'" in p and "in --cdm" in p for p in problems)


def test_malformed_rules_and_values(setup_cdm):
    setup_cdm([{"min_height": "1080", "cdm": "mine.wvd"}, {"min_height": 720}, 5], global_cdm=7)
    problems = validate_cdm_settings()
    assert any("min_height must be a whole number" in p for p in problems)
    assert any("needs a 'cdm' entry" in p for p in problems)
    assert any("unexpected entry 5" in p for p in problems)
    assert any("must be a string or a list, not int" in p for p in problems)


def test_reserved_remote_ids_are_reported(setup_cdm, monkeypatch):
    setup_cdm()
    monkeypatch.setattr(device_install, "get_remote_cdm_registry", lambda: {"search": DL_WIDEVINE})
    assert any("reserved" in p for p in validate_cdm_settings())


def test_manager_keeps_the_origin_and_passes_it_to_the_key_function(monkeypatch, setup_cdm):
    monkeypatch.setattr(DRMManager, "_build_vaults", staticmethod(lambda: []))
    setup_cdm("mine.wvd")
    mgr = DRMManager.for_site("mysite")
    assert mgr.widevine_cdm_origin == "login.toml 'mysite'"
    _, kwargs = mgr.cdm_spec("widevine")
    assert kwargs["cdm_origin"] == "login.toml 'mysite'"
    mgr.select_cdm(480)
    assert mgr.widevine_cdm_origin == "login.toml 'mysite'"


def _record_prints(monkeypatch, module):
    printed = []
    monkeypatch.setattr(module.console, "print", lambda *args, **_: printed.append(str(args[0]).strip() if args else ""))
    return printed


class StopCdm:
    def open(self):
        raise RuntimeError("stop after the load line")


def test_remote_cdm_prints_one_load_line_with_the_reason(monkeypatch):
    printed = _record_prints(monkeypatch, widevine)
    monkeypatch.setattr(widevine, "build_remote_cdm", lambda *_: StopCdm())

    with pytest.raises(RuntimeError, match="stop after"):
        widevine._get_widevine_keys([], "https://license.test", None, {"device_name": "L3"}, cdm_origin="login.toml 'mysite'")

    assert [line for line in printed if "[+]" in line] == ["[green]\[+][/green] [cyan]remote L3 [white]| [dim]why: login.toml 'mysite'"]
    assert not any("chosen by" in line or "Using remote" in line for line in printed)


def test_local_device_prints_only_the_file_name_not_the_path(monkeypatch):
    from pywidevine.cdm import Cdm
    from pywidevine.device import Device

    printed = _record_prints(monkeypatch, widevine)
    monkeypatch.setattr(Device, "load", classmethod(lambda cls, path: object()))
    monkeypatch.setattr(Cdm, "from_device", classmethod(lambda cls, device: StopCdm()))

    with pytest.raises(RuntimeError, match="stop after"):
        widevine._get_widevine_keys([], "https://license.test", "C:/binary/wvd/phone_l3.wvd", None, cdm_origin="search")

    loads = [line for line in printed if "[+]" in line]
    assert loads == ["[green]\[+][/green] [cyan]phone_l3.wvd [white]| [dim]why: search"]
    assert "binary" not in loads[0] and "Security" not in loads[0]


def test_load_line_without_a_reason_and_with_square_brackets():
    import io

    from rich.console import Console

    from VibraVid.core.drm.system import cdm_load_line

    def render(line):
        out = io.StringIO()
        Console(file=out, width=200, force_terminal=False).print(line)
        return out.getvalue().strip()

    assert render(cdm_load_line("phone_l3.wvd")) == "[+] phone_l3.wvd"
    assert render(cdm_load_line("phone_l3.wvd", "config.toml [DRM]")) == "[+] phone_l3.wvd | why: config.toml [DRM]"
    assert render(cdm_load_line("phone_l3.wvd", "search", ("SysID", 22594))) == "[+] phone_l3.wvd | SysID: 22594 | why: search"
    assert render(cdm_load_line("phone_l3.wvd", None, ("SysID", 22594))) == "[+] phone_l3.wvd | SysID: 22594"


def test_widevine_line_keeps_the_system_id(monkeypatch):
    from pywidevine.cdm import Cdm
    from pywidevine.device import Device

    printed = _record_prints(monkeypatch, widevine)
    monkeypatch.setattr(Device, "load", classmethod(lambda cls, path: object()))
    monkeypatch.setattr(Cdm, "from_device", classmethod(lambda cls, device: StopCdm()))
    monkeypatch.setattr(widevine, "read_device_wvd", lambda path: {"system_id": 22594, "security_level": 3})

    with pytest.raises(RuntimeError, match="stop after"):
        widevine._get_widevine_keys([], "https://license.test", "C:/binary/wvd/phone_l3.wvd", None, cdm_origin="search")

    assert [line for line in printed if "[+]" in line] == ["[green]\[+][/green] [cyan]phone_l3.wvd [white]| [cyan]SysID: [red]22594 [white]| [dim]why: search"]


def test_playready_line_keeps_the_model(monkeypatch):
    from pyplayready.cdm import Cdm
    from pyplayready.device import Device

    from VibraVid.core.drm import playready

    printed = _record_prints(monkeypatch, playready)
    monkeypatch.setattr(Device, "load", classmethod(lambda cls, path: object()))
    monkeypatch.setattr(Cdm, "from_device", classmethod(lambda cls, device: StopCdm()))
    monkeypatch.setattr(playready, "read_device_prd", lambda path: {"manufacturer": "TPV", "model": "AOC", "model_number": "50U6185"})

    with pytest.raises(RuntimeError, match="stop after"):
        playready._get_playready_keys_local_cdm([], "https://license.test", "C:/binary/prd/tv_sl3000.prd", None, cdm_origin="login.toml 'mysite'")

    assert [line for line in printed if "[+]" in line] == ["[green]\[+][/green] [cyan]tv_sl3000.prd [white]| [cyan]Model: [red]TPV AOC 50U6185 [white]| [dim]why: login.toml 'mysite'"]


def test_unreadable_device_still_prints_the_file_name_and_the_reason(monkeypatch):
    from pyplayready.cdm import Cdm
    from pyplayready.device import Device

    from VibraVid.core.drm import playready

    printed = _record_prints(monkeypatch, playready)
    monkeypatch.setattr(Device, "load", classmethod(lambda cls, path: object()))
    monkeypatch.setattr(Cdm, "from_device", classmethod(lambda cls, device: StopCdm()))

    def broken(path):
        raise ValueError("not a prd")

    monkeypatch.setattr(playready, "read_device_prd", broken)
    with pytest.raises(RuntimeError, match="stop after"):
        playready._get_playready_keys_local_cdm([], "https://license.test", "C:/binary/prd/bad.prd", None, cdm_origin="search")

    assert [line for line in printed if "[+]" in line] == ["[green]\[+][/green] [cyan]bad.prd [white]| [dim]why: search"]
