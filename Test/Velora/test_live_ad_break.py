"""LiveAdBreak: a poll with no new content that sees ads is an ad break, not the end of the stream, until the break lasts too long."""

from VibraVid.core.velora._live_ad_break import LiveAdBreak


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_polls_without_ads_are_never_a_break():
    ad_break = LiveAdBreak(max_seconds=60, clock=_Clock())

    assert not ad_break.holds(False)
    assert not ad_break.is_started()


def test_ads_in_the_manifest_hold_the_recording_within_the_limit():
    clock = _Clock()
    ad_break = LiveAdBreak(max_seconds=60, clock=clock)

    assert ad_break.holds(True)
    clock.now += 59
    assert ad_break.holds(True)
    assert ad_break.is_started()


def test_a_break_that_lasts_too_long_is_the_end_of_the_stream():
    clock = _Clock()
    ad_break = LiveAdBreak(max_seconds=60, clock=clock)

    ad_break.holds(True)
    clock.now += 61

    assert not ad_break.holds(True)


def test_content_coming_back_resets_the_break_and_the_limit():
    clock = _Clock()
    ad_break = LiveAdBreak(max_seconds=60, clock=clock)
    ad_break.holds(True)
    clock.now += 50
    ad_break.reset()

    assert not ad_break.is_started()
    clock.now += 50
    assert ad_break.holds(True)


def test_ads_disappearing_without_content_ends_the_break():
    clock = _Clock()
    ad_break = LiveAdBreak(max_seconds=60, clock=clock)
    ad_break.holds(True)

    assert not ad_break.holds(False)
    assert not ad_break.is_started()


def test_elapsed_counts_from_the_first_ad_poll():
    clock = _Clock()
    ad_break = LiveAdBreak(max_seconds=600, clock=clock)

    assert ad_break.elapsed() == 0.0
    ad_break.holds(True)
    clock.now += 42

    assert ad_break.elapsed() == 42.0
    ad_break.reset()
    assert ad_break.elapsed() == 0.0
