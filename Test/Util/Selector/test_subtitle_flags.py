# 21.11.26
# ruff: noqa: E402

import sys
from pathlib import Path


workspace_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(workspace_root))


from mock_streams import MockStream
from VibraVid.core.utils.selector import StreamSelector


def _pool():
    """ita and eng each have plain + forced + cc + sdh; fra has only a plain track."""
    streams = []
    for lang, region in (("ita", "it-IT"), ("eng", "en-US")):
        streams += [
            MockStream(type="subtitle", language=lang, resolved_language=region, id=f"{lang}-plain"),
            MockStream(type="subtitle", language=lang, resolved_language=region, forced=True, id=f"{lang}-forced"),
            MockStream(type="subtitle", language=lang, resolved_language=region, is_cc=True, id=f"{lang}-cc"),
            MockStream(type="subtitle", language=lang, resolved_language=region, is_sdh=True, id=f"{lang}-sdh"),
        ]
    streams.append(MockStream(type="subtitle", language="fra", resolved_language="fr-FR", id="fra-plain"))
    return streams


def _ids(streams):
    return {s.id for s in streams if s.type == "subtitle" and s.selected}


def _select(value, streams=None, strict=False):
    streams = _pool() if streams is None else streams
    selector = StreamSelector("false", "false", value, strict_no_match=strict)
    selector.apply(streams)
    return streams, selector


def test_only_forced_of_each_language():
    streams, _ = _select("ita_forced|eng_forced")
    assert _ids(streams) == {"ita-forced", "eng-forced"}


def test_only_cc():
    streams, _ = _select("eng_cc")
    assert _ids(streams) == {"eng-cc"}


def test_only_sdh():
    streams, _ = _select("ita_sdh|eng_sdh")
    assert _ids(streams) == {"ita-sdh", "eng-sdh"}


def test_mixed_flags_per_language():
    streams, _ = _select("ita_forced|eng_cc")
    assert _ids(streams) == {"ita-forced", "eng-cc"}


def test_hi_is_not_folded_into_cc():
    # a pool with a real "hi" track: eng_hi finds it, eng_cc keeps finding the cc one
    streams = _pool() + [MockStream(type="subtitle", language="eng", resolved_language="en-US", is_hi=True, id="eng-hi")]
    streams, _ = _select("eng_hi", streams)
    assert _ids(streams) == {"eng-hi"}

    streams = _pool() + [MockStream(type="subtitle", language="eng", resolved_language="en-US", is_hi=True, id="eng-hi")]
    streams, _ = _select("eng_cc", streams)
    assert _ids(streams) == {"eng-cc"}


def test_sdh_and_hi_are_interchangeable_only_when_matching():
    only_hi = [MockStream(type="subtitle", language="eng", resolved_language="en-US", is_hi=True, id="eng-hi")]
    streams, _ = _select("eng_sdh", only_hi)
    assert _ids(streams) == {"eng-hi"}

    only_sdh = [MockStream(type="subtitle", language="eng", resolved_language="en-US", is_sdh=True, id="eng-sdh")]
    streams, _ = _select("eng_hi", only_sdh)
    assert _ids(streams) == {"eng-sdh"}


def test_cc_request_never_falls_back_to_sdh_or_hi():
    streams = [
        MockStream(type="subtitle", language="eng", resolved_language="en-US", is_sdh=True, id="eng-sdh"),
        MockStream(type="subtitle", language="eng", resolved_language="en-US", is_hi=True, id="eng-hi"),
    ]
    streams, _ = _select("eng_cc", streams)
    assert _ids(streams) == set()


def test_exact_spelling_wins_over_the_interchangeable_one():
    streams, _ = _select("eng_sdh")
    assert _ids(streams) == {"eng-sdh"}
    streams = _pool() + [MockStream(type="subtitle", language="eng", resolved_language="en-US", is_hi=True, id="eng-hi")]
    streams, _ = _select("eng_hi", streams)
    assert _ids(streams) == {"eng-hi"}


def test_hindi_is_not_mistaken_for_the_hi_flag():
    streams = [MockStream(type="subtitle", language="hi", resolved_language="hi-IN", id="hin")]
    streams, _ = _select("hi", streams)
    assert _ids(streams) == {"hin"}


def test_missing_flag_has_no_fallback_to_plain_track():
    # fra only has a plain track: "fra_forced" must not hand it back
    streams, selector = _select("fra_forced")
    assert _ids(streams) == set()
    assert selector.no_match is False  # no_match is only raised in strict mode

    streams, selector = _select("fra_forced", strict=True)
    assert _ids(streams) == set()
    assert selector.no_match is True


def test_missing_flag_for_one_language_keeps_the_other():
    streams, _ = _select("fra_forced|ita_forced")
    assert _ids(streams) == {"ita-forced"}


def test_combined_flags_need_a_track_carrying_both():
    streams = _pool()
    streams.append(MockStream(type="subtitle", language="ita", resolved_language="it-IT", forced=True, is_sdh=True, id="ita-forced-sdh"))
    streams, _ = _select("ita_forced_sdh", streams)
    assert _ids(streams) == {"ita-forced-sdh"}


def test_combined_flags_with_no_such_track_selects_nothing():
    streams, selector = _select("ita_forced_sdh", strict=True)
    assert _ids(streams) == set()
    assert selector.no_match is True


def test_plain_language_keeps_every_variant():
    streams, _ = _select("ita")
    assert _ids(streams) == {"ita-plain", "ita-forced", "ita-cc", "ita-sdh"}


def test_iso_639_1_and_639_2_codes_are_equivalent_with_flags():
    for token in ("it_forced", "ita_forced", "it-IT_forced"):
        streams, _ = _select(token)
        assert _ids(streams) == {"ita-forced"}, token
