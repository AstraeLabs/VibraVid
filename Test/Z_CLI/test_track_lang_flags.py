import pytest

from VibraVid.cli.run import normalize_lang_list, setup_argument_parser


@pytest.fixture(scope="module")
def parser():
    return setup_argument_parser({})[0]


@pytest.mark.parametrize("flag", ["-sa", "--audio", "--audio-langs"])
def test_audio_flag_aliases_share_dest(parser, flag):
    assert parser.parse_args([flag, "it|en"]).audio == "it|en"


@pytest.mark.parametrize("flag", ["-ss", "--subtitle", "--sub-langs"])
def test_subtitle_flag_aliases_share_dest(parser, flag):
    assert parser.parse_args([flag, "it|en"]).subtitle == "it|en"


@pytest.mark.parametrize(
    "spec, expected",
    [
        ("it,en", "it|en"),
        ("ita|eng", "ita|eng"),
        ("it eng", "it|eng"),
        ("ita_forced,en", "ita_forced|en"),
        ("l=ita:c=aac:f=best", "l=ita:c=aac:f=best"),
    ],
)
def test_normalize_lang_list(spec, expected):
    assert normalize_lang_list(spec) == expected
