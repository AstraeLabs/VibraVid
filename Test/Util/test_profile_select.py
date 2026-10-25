# 25.10.26

import pytest

from VibraVid.core.ui.tracker import context_tracker
from VibraVid.services._base import profile_select
from VibraVid.services._base.profile_select import AccountProfile, ProfileSelector
from VibraVid.utils.config import LoginConfigError

PROFILES = [AccountProfile("g1", "Adults"), AccountProfile("g2", "Kids"), AccountProfile("g3", "2")]


@pytest.fixture
def ask(monkeypatch):
    """Set what login.toml / --user-profile say, return a selector for 'svc'."""
    def _ask(login=None, cli=None):
        monkeypatch.setattr(profile_select.config_manager.login, "get", lambda site, key, default=None: login if (site, key) == ("svc", "profile") else default)
        monkeypatch.setattr(context_tracker, "site_options", {"user_profile": cli}, raising=False)
        return ProfileSelector("svc")
    return _ask


@pytest.mark.parametrize("login", [None, "", "  "])
def test_nothing_requested_keeps_the_credentials_profile(ask, login):
    selector = ask(login=login)
    assert selector.wanted == ""
    assert selector.pick(PROFILES) is None


@pytest.mark.parametrize("value, expected", [("1", "Adults"), (2, "Kids"), (" 2 ", "Kids")])
def test_number_is_the_position_in_the_list(ask, value, expected):
    assert ask(login=value).pick(PROFILES).name == expected


@pytest.mark.parametrize("value", ["Kids", "kids", " KIDS "])
def test_name_ignores_case_and_spaces(ask, value):
    assert ask(login=value).pick(PROFILES).id == "g2"


def test_number_wins_over_a_profile_named_like_a_number(ask):
    assert ask(login="2").pick(PROFILES).name == "Kids"


def test_digit_name_is_used_when_the_number_is_out_of_range(ask):
    assert ask(login="3").pick(PROFILES).id == "g3"
    assert ask(login="3").pick(PROFILES[:2] + [AccountProfile("g9", "3")]).id == "g9"


@pytest.mark.parametrize("value", ["0", "4", "Nobody"])
def test_unknown_profile_lists_the_choices(ask, value):
    with pytest.raises(LoginConfigError, match=r"1 Adults, 2 Kids, 3 2"):
        ask(login=value).pick(PROFILES)


def test_cli_wins_over_login_toml(ask):
    assert ask(login="1", cli="Kids").pick(PROFILES).name == "Kids"


def test_listing_marks_the_chosen_profile():
    assert ProfileSelector.listing(PROFILES, PROFILES[1]) == "1 Adults, 2 Kids (selected), 3 2"


class FakeClient:
    def __init__(self, profiles):
        self.profiles = profiles
        self.switched = None

    def list_profiles(self):
        return self.profiles

    def switch_profile(self, profile):
        self.switched = profile


def test_select_switches_only_when_a_profile_is_requested(ask):
    client = FakeClient(PROFILES)
    assert ask(login="").select(client) is None
    assert client.switched is None

    assert ask(login="Kids").select(client).id == "g2"
    assert client.switched.id == "g2"


def test_select_fails_when_the_service_returns_no_profiles(ask):
    with pytest.raises(RuntimeError, match="could not read"):
        ask(login="1").select(FakeClient([]))
