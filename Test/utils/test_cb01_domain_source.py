import pytest

from VibraVid.services.cineblog01 import client


def _domain_reader(sections):
    def get_section(name):
        return dict(sections.get(name, {}))

    return get_section


def test_get_base_url_prefers_first_healthy_tracked_domain(monkeypatch):
    sections = {
        "Cineblog_1": {
            "full_url": "https://cineblog001.download/",
            "last_status": 200,
        },
        "Cineblog_2": {
            "full_url": "https://cineblog01.world/",
            "last_status": 200,
        },
        "Cineblog_3": {
            "full_url": "https://cineblog01.watch/",
            "last_status": 200,
        },
    }
    monkeypatch.setattr(
        client.config_manager.domain,
        "get_section",
        _domain_reader(sections),
    )

    assert client.get_base_url() == "https://cineblog001.download/"


def test_get_base_url_skips_unhealthy_primary(monkeypatch):
    sections = {
        "Cineblog_1": {
            "full_url": "https://cineblog001.download/",
            "last_status": 503,
        },
        "Cineblog_2": {
            "full_url": "https://cineblog01.world/",
            "last_status": 200,
        },
        "Cineblog_3": {
            "full_url": "https://cineblog01.watch/",
            "last_status": 200,
        },
    }
    monkeypatch.setattr(
        client.config_manager.domain,
        "get_section",
        _domain_reader(sections),
    )

    assert client.get_base_url() == "https://cineblog01.world/"


def test_get_base_url_uses_third_healthy_mirror(monkeypatch):
    sections = {
        "Cineblog_1": {
            "full_url": "https://cineblog001.download/",
            "last_status": 503,
        },
        "Cineblog_2": {
            "full_url": "https://cineblog01.world/",
            "last_status": -1,
        },
        "Cineblog_3": {
            "full_url": "https://cineblog01.watch/",
            "last_status": 200,
        },
    }
    monkeypatch.setattr(
        client.config_manager.domain,
        "get_section",
        _domain_reader(sections),
    )

    assert client.get_base_url() == "https://cineblog01.watch/"


def test_get_base_url_falls_back_to_first_present_domain(monkeypatch):
    sections = {
        "Cineblog_1": {
            "full_url": "https://cineblog001.download",
            "last_status": 503,
        },
        "Cineblog_2": {
            "full_url": "https://cineblog01.world/",
            "last_status": -1,
        },
    }
    monkeypatch.setattr(
        client.config_manager.domain,
        "get_section",
        _domain_reader(sections),
    )

    assert client.get_base_url() == "https://cineblog001.download/"


def test_get_base_url_fails_when_tracker_has_no_cb01_domains(monkeypatch):
    monkeypatch.setattr(
        client.config_manager.domain,
        "get_section",
        _domain_reader({}),
    )

    with pytest.raises(ValueError, match="No Cineblog01 domain is available"):
        client.get_base_url()
