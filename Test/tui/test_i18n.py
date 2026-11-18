import os
import pytest
from VibraVid.tui.i18n import t, set_language, get_language, detect_system_language

def test_i18n_default_and_fallback():
    set_language("en")
    assert get_language() == "en"
    assert t("seasons") == "Seasons"
    
    set_language("it")
    assert get_language() == "it"
    assert t("seasons") == "Stagioni"

def test_i18n_format_args():
    set_language("it")
    assert "5" in t("results_found", count=5)
    set_language("en")
    assert "5" in t("results_found", count=5)

def test_i18n_unknown_key():
    set_language("it")
    assert t("unknown_key_xyz") == "unknown_key_xyz"
    assert t("unknown_key_xyz", default="Default Value") == "Default Value"

def test_i18n_detect_system_language(monkeypatch):
    monkeypatch.setenv("LANG", "it_IT.UTF-8")
    assert detect_system_language() == "it"
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    assert detect_system_language() == "en"

def test_i18n_catalogs_keys_matching():
    from VibraVid.tui.i18n import CATALOGS
    it_keys = set(CATALOGS["it"].keys())
    en_keys = set(CATALOGS["en"].keys())
    assert it_keys == en_keys, f"Missing in IT: {en_keys - it_keys}, Missing in EN: {it_keys - en_keys}"

def test_i18n_new_gaps_keys():
    set_language("it")
    assert t("col_title") == "Titolo"
    assert t("col_status") == "Stato"
    assert t("hk_home") == "Torna alla schermata iniziale (Home)"
    assert t("sec_default") == "Impostazioni Predefinite"
    assert t("select_log_file") == "Seleziona File Log:"

    set_language("en")
    assert t("col_title") == "Title"
    assert t("col_status") == "Status"
    assert t("hk_home") == "Return to Home screen"
    assert t("sec_default") == "Default Settings"
    assert t("select_log_file") == "Select Log File:"

