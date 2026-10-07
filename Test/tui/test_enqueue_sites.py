# 05.10.26
# ruff: noqa: E402

import sys
from pathlib import Path
from types import SimpleNamespace

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

import pytest

from VibraVid.cli.command import queue as queue_module
from VibraVid.cli.command.queue import _PROCESS_TAG, _load_queue, _queue_path, enqueue
from VibraVid.tui.screens import detail as detail_module
from VibraVid.tui.screens import queue as queue_screen
from VibraVid.tui.screens import search as search_module
from VibraVid.utils import config_manager

ITEM_KEYS = ["id", "argv", "status", "tag", "enqueued_at", "started_at", "finished_at", "returncode", "attempts"]


@pytest.fixture(autouse=True)
def _isolated_queue_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config_manager, "base_path", str(tmp_path))


def _items():
    return _load_queue(_queue_path(_PROCESS_TAG))["items"]


def _assert_pending_item(item, argv=None):
    assert list(item) == ITEM_KEYS
    assert len(item["id"]) == 8
    assert item["status"] == "pending"
    assert item["tag"] == _PROCESS_TAG
    assert item["enqueued_at"]
    assert item["started_at"] is None and item["finished_at"] is None
    assert item["returncode"] is None and item["attempts"] == 0
    if argv is not None:
        assert item["argv"] == argv


class _Notifier:
    def __init__(self):
        self.messages = []

    def notify(self, message, severity="information"):
        self.messages.append((message, severity))


def test_cli_enqueue_strips_the_queue_flags():
    enqueue(["--down", "http://x/m.m3u8", "--queue-add"], SimpleNamespace(down="http://x/m.m3u8"))

    (item,) = _items()
    _assert_pending_item(item, ["--down", "http://x/m.m3u8"])


def test_cli_enqueue_appends_and_keeps_existing_items():
    enqueue(["--down", "a"], SimpleNamespace(down="a"))
    enqueue(["--down", "b"], SimpleNamespace(down="b"))

    first, second = _items()
    assert first["argv"] == ["--down", "a"] and second["argv"] == ["--down", "b"]
    assert first["id"] != second["id"]


def test_queue_screen_enqueues_a_typed_command():
    screen = SimpleNamespace(app=_Notifier(), _refresh_queue=lambda: None)
    queue_screen.QueueScreen._on_enqueue_modal_dismissed(screen, '--down "http://x/a b.m3u8" --type hls')

    (item,) = _items()
    _assert_pending_item(item, ["--down", "http://x/a b.m3u8", "--type", "hls"])
    assert screen.app.messages[0][0].startswith("Enqueued job")


def test_queue_screen_ignores_empty_and_invalid_commands():
    screen = SimpleNamespace(app=_Notifier(), _refresh_queue=lambda: None)
    queue_screen.QueueScreen._on_enqueue_modal_dismissed(screen, "")
    queue_screen.QueueScreen._on_enqueue_modal_dismissed(screen, '--down "unterminated')

    assert _items() == []
    assert screen.app.messages[0][1] == "error"


def _search_screen(selected=(), raw=()):
    return SimpleNamespace(
        focused=None,
        app=_Notifier(),
        _selected_keys=set(selected),
        _raw=list(raw),
        _highlighted_payload=None,
        _populate_results_list=lambda: None,
    )


def test_search_quick_enqueue_queues_the_highlighted_item():
    result = SimpleNamespace(id="7", name="My Show")
    screen = _search_screen()
    screen._get_active_target = lambda: ("mysite", result, [])
    search_module.SearchScreen.action_quick_enqueue(screen)

    (item,) = _items()
    _assert_pending_item(item)
    argv = item["argv"]
    assert argv[argv.index("-i") + 1] == "mysite" and argv[argv.index("-s") + 1] == "My Show" and argv[argv.index("--item") + 1] == "1"


def test_search_batch_enqueue_queues_every_selected_item_in_one_write():
    a, b, c = (SimpleNamespace(id=str(i), name=f"Show {i}") for i in range(3))
    raw = [("siteA", a, []), ("siteB", b, []), ("siteC", c, [])]
    screen = _search_screen(selected={"siteA:0", "siteC:2"}, raw=raw)
    search_module.SearchScreen.action_batch_enqueue(screen)

    first, second = _items()
    _assert_pending_item(first)
    _assert_pending_item(second)
    assert first["argv"][first["argv"].index("-i") + 1] == "siteA"
    assert second["argv"][second["argv"].index("-i") + 1] == "siteC"
    assert screen._selected_keys == set()
    assert screen.app.messages[0] == (search_module.t("batch_enqueued_msg", count=2), "information")


def test_search_batch_enqueue_without_selection_writes_nothing():
    screen = _search_screen()
    search_module.SearchScreen.action_batch_enqueue(screen)

    assert _items() == []
    assert screen.app.messages[0][1] == "warning"


def _detail_screen(is_single=True, selections=None):
    return SimpleNamespace(
        app=_Notifier(),
        _item=SimpleNamespace(name="Great Series"),
        _site="mysite",
        _is_single=is_single,
        _episode_selections=selections or {},
        _seasons=[1, 2],
        _season_episode_count=lambda season: 10,
    )


def test_detail_queue_button_enqueues_the_single_item():
    detail_module.TitleDetailScreen._on_queue(_detail_screen(is_single=True))

    (item,) = _items()
    _assert_pending_item(item)
    argv = item["argv"]
    assert argv[argv.index("-i") + 1] == "mysite" and argv[argv.index("-s") + 1] == "Great Series"


def test_detail_queue_button_requires_an_episode_selection_for_series():
    screen = _detail_screen(is_single=False, selections={})
    detail_module.TitleDetailScreen._on_queue(screen)

    assert _items() == []
    assert screen.app.messages[0][1] == "warning"


def test_every_site_shares_the_module_level_process_tag():
    assert queue_module._PROCESS_TAG == _PROCESS_TAG
