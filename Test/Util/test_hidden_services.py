# 21.10.26

"""``hidden_services``: services switched off by the user are not loaded at all; the persisted indices never depend on the list."""

import logging

import pytest

from VibraVid.services._base import site_loader


class _NoDiskCache:
    """Keeps the real `.cache` manifest out of these tests (it prunes the entries of folders it did not see)."""

    @staticmethod
    def load(service, name):
        return {}

    @staticmethod
    def save(service, name, data, ttl=None):
        pass


@pytest.fixture
def services(tmp_path, monkeypatch):
    """Four services in a fake `services` folder, loaded as the "default" source with out-of-order, gapped indices."""
    for name, indice in {"alpha": 7, "bravo": 3, "charlie": 12, "delta": 5}.items():
        folder = tmp_path / name
        folder.mkdir()
        (folder / "__init__.py").write_text(f'indice = {indice}\n_useFor = "Serie"\n', encoding="utf-8")

    monkeypatch.setattr(site_loader, "__file__", str(tmp_path / "_base" / "site_loader.py"))  # default source -> tmp_path
    monkeypatch.setattr(site_loader, "imp_sources", ["default"])
    monkeypatch.setattr(site_loader, "get_is_binary_installation", lambda: False)
    monkeypatch.setattr(site_loader, "disk_cache", _NoDiskCache)
    monkeypatch.setattr(site_loader, "_warned_unknown_hidden", set())
    for var in site_loader._HIDDEN_SITES_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    _set_hidden(monkeypatch, [])
    return tmp_path


def _set_hidden(monkeypatch, names):
    real_get_list = site_loader.config_manager.config.get_list

    def get_list(section, key, *args, **kwargs):
        if (section, key) == ("DEFAULT", "hidden_services"):
            return list(names)
        return real_get_list(section, key, *args, **kwargs)

    monkeypatch.setattr(site_loader.config_manager.config, "get_list", get_list)


def _indices(loaded):
    return {lazy.module_name: lazy.indice for lazy in loaded.values()}


def _file_indices(folder):
    out = {}
    for init in folder.glob("*/__init__.py"):
        line = next(ln for ln in init.read_text(encoding="utf-8").splitlines() if ln.startswith("indice"))
        out[init.parent.name] = int(line.split("=")[1])
    return out


def test_nothing_hidden_loads_everything_in_index_order(services):
    assert _indices(site_loader.load_search_functions()) == {"bravo": 0, "delta": 1, "alpha": 2, "charlie": 3}


def test_hidden_services_are_not_loaded_and_the_others_have_no_gaps(services, monkeypatch):
    _set_hidden(monkeypatch, ["Delta", " charlie "])

    loaded = site_loader.load_search_functions()

    assert set(loaded) == {"bravo_search", "alpha_search"}
    assert _indices(loaded) == {"bravo": 0, "alpha": 1}


def test_the_list_never_rewrites_the_indices_stored_in_the_service_files(services, monkeypatch):
    site_loader.load_search_functions()
    stored = _file_indices(services)
    assert stored == {"bravo": 0, "delta": 1, "alpha": 2, "charlie": 3}  # numbered over every service found

    _set_hidden(monkeypatch, ["bravo", "delta"])
    site_loader.load_search_functions()

    assert _file_indices(services) == stored


@pytest.mark.parametrize("var", ["VIBRAVID_HIDDEN_SITES", "VIBRAVID_DISABLED_SITES"])
def test_env_vars_add_to_the_config_list(services, monkeypatch, var):
    _set_hidden(monkeypatch, ["alpha"])
    monkeypatch.setenv(var, "bravo, delta")

    assert site_loader.hidden_services() == {"alpha", "bravo", "delta"}
    assert set(site_loader.load_search_functions()) == {"charlie_search"}


def test_an_unknown_name_is_reported_once(services, monkeypatch, caplog):
    _set_hidden(monkeypatch, ["alpha", "nove"])
    caplog.set_level(logging.WARNING, logger=site_loader.logger.name)

    site_loader.load_search_functions()
    site_loader.load_search_functions()

    warnings = [r.getMessage() for r in caplog.records if "unknown service" in r.getMessage()]
    assert len(warnings) == 1 and "nove" in warnings[0]
