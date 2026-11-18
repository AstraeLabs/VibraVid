# 17.10.26

from VibraVid.services.raiplay import scrapper


class _Response:
    status_code = 200

    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class _Client:
    def __init__(self, data):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url):
        return _Response(self._data)


def _season_names(monkeypatch, blocks):
    monkeypatch.setattr(scrapper, "create_client", lambda **kw: _Client({"program_info": {"title": "T"}, "blocks": blocks}))
    info = scrapper.GetSerieInfo("/programmi/t.json")
    info.collect_info_title()
    return [(s.number, s.name) for s in info.seasons_manager.seasons]


def _block(name, *sets):
    return {
        "type": "RaiPlay Multimedia Block",
        "name": name,
        "id": f"block-{name}",
        "sets": [{"name": s, "id": f"set-{s}", "episode_size": {"number": 4}} for s in sets],
    }


def test_single_season_program_with_a_plain_episodes_block_is_detected(monkeypatch):
    blocks = [_block("Episodi", "Episodi"), _block("Extra", "Extra")]
    assert _season_names(monkeypatch, blocks) == [(1, "Episodi")]


def test_numbered_seasons_win_over_plain_sets(monkeypatch):
    blocks = [_block("Stagione 1", "Episodi"), _block("Speciali", "Speciali"), _block("Stagione 2", "Episodi")]
    assert _season_names(monkeypatch, blocks) == [(1, "Stagione 1"), (2, "Stagione 2")]


def test_clips_and_extras_are_still_skipped(monkeypatch):
    blocks = [_block("Clip", "Clip"), _block("Extra", "Extra")]
    assert _season_names(monkeypatch, blocks) == []
