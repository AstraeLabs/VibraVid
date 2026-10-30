"""LiveEndWatcher: the recording stops only after several consecutive "not live" answers; errors and flickers do not count."""

import time

from VibraVid.services._base.live_event import LiveEndWatcher


def _watch(answers, misses=3, wait=3.0):
    """Feed *answers* (bool, or an Exception to raise) one per poll; return (ended, polls_done) once the watcher decides or *wait* passes."""
    state = {"i": 0}

    def is_live():
        i = state["i"]
        state["i"] += 1
        answer = answers[min(i, len(answers) - 1)]
        if isinstance(answer, Exception):
            raise answer
        return answer

    with LiveEndWatcher(is_live, interval=0.01, misses=misses) as watcher:
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline and not watcher.is_ended() and state["i"] < len(answers) + 20:
            time.sleep(0.01)
        return watcher.is_ended(), state["i"]


def test_stops_after_the_consecutive_misses():
    ended, polls = _watch([True, True, False, False, False])
    assert ended
    assert polls == 5


def test_a_flicker_back_to_live_resets_the_count():
    ended, _ = _watch([True, False, False, True, False, False, True, True, True])
    assert not ended


def test_errors_say_nothing_about_the_event():
    ended, _ = _watch([False, False, RuntimeError("epg down"), RuntimeError("epg down"), RuntimeError("epg down"), True])
    assert not ended


def test_errors_between_misses_do_not_reset_or_add():
    ended, polls = _watch([False, RuntimeError("x"), False, RuntimeError("x"), False])
    assert ended
    assert polls == 5


def test_never_stops_while_live():
    ended, _ = _watch([True] * 30)
    assert not ended
