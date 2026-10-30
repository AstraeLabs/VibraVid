from VibraVid.core.velora.util._tfdt_rebase import FragmentContinuity


def _feed(steps, first_key=100, first_tfdt=0):
    """Observe fragments whose tfdt advances by each of *steps*, with consecutive segment numbers."""
    continuity = FragmentContinuity()
    key, tfdt = first_key, first_tfdt
    continuity.observe(key, tfdt)
    for step in steps:
        key, tfdt = key + 1, tfdt + step
        continuity.observe(key, tfdt)
    return continuity


def test_regular_sequence_is_contiguous():
    assert _feed([1152] * 50).is_contiguous()


def test_a_single_fragment_is_contiguous():
    assert _feed([]).is_contiguous()


def test_small_step_differences_are_not_a_hole():
    # 1.92 s next to 2.0 s steps (and a rounding wobble) are the normal state of a live track
    assert _feed([1152, 1200, 1152, 1152, 1200, 1152]).is_contiguous()


def test_skipped_segment_number_is_a_hole():
    continuity = FragmentContinuity()
    continuity.observe(10, 0)
    continuity.observe(11, 1152)
    continuity.observe(13, 3456)
    assert not continuity.is_contiguous()


def test_time_jump_with_consecutive_numbers_is_a_hole():
    assert not _feed([1152, 1152, 2304, 1152]).is_contiguous()


def test_tfdt_that_does_not_advance_is_a_hole():
    assert not _feed([1152, 0, 1152]).is_contiguous()
    assert not _feed([1152, -1152]).is_contiguous()


def test_missing_tfdt_is_a_hole():
    continuity = FragmentContinuity()
    continuity.observe(1, 0)
    continuity.observe(2, None)
    assert not continuity.is_contiguous()


def test_a_hole_is_never_forgotten():
    continuity = _feed([1152, 2304])
    for index in range(20):
        continuity.observe(200 + index, 100000 + index * 1152)
    assert not continuity.is_contiguous()


def test_first_step_already_double_cannot_be_told_from_the_usual_one():
    """Known limit: with no reference step yet, a double first step is taken as the normal one."""
    assert _feed([2304, 2304, 2304]).is_contiguous()
