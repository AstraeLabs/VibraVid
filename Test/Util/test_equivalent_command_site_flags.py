# 23.10.26

"""The logged equivalent command prints each site option with its real flag, not the flag derived from its dest."""

import argparse
from types import SimpleNamespace

from VibraVid.cli.command.equivalent_command import EquivalentCommandBuilder


def _build(site_options):
    parser = argparse.ArgumentParser()
    parser.add_argument("--range", dest="video_range", default="SDR")
    parser.add_argument("--id", "-id", dest="direct_id", default=None)
    parser.add_argument("--vcodec", dest="vcodec", default="HEVC")
    context = SimpleNamespace(cli_site="netflix", site_options=site_options)
    builder = EquivalentCommandBuilder(excluded_dests=set())
    return builder.build(parser.parse_args([]), parser, context, site_option_dests=list(site_options))


def test_a_site_option_whose_dest_differs_from_its_flag_uses_the_flag():
    cmd = _build({"video_range": "SDR,HDR10", "direct_id": "80057281"})

    assert "--range SDR,HDR10" in cmd and "-id 80057281" in cmd
    assert "--video-range" not in cmd and "--direct-id" not in cmd


def test_a_site_option_whose_dest_matches_its_flag_is_unchanged():
    assert "--vcodec AV1,HEVC" in _build({"vcodec": "AV1,HEVC"})


def test_a_dest_missing_from_the_parser_falls_back_to_the_dashed_dest():
    assert "--some-option 1" in _build({"some_option": "1"})


def test_a_dest_the_context_tracker_already_prints_is_not_repeated_from_the_site_options():
    parser = argparse.ArgumentParser()
    context = SimpleNamespace(cli_site="netflix", cli_season_selection="1", cli_episode_selection="2", site_options={"season": "1", "episode": "2", "vcodec": "AVC"})
    builder = EquivalentCommandBuilder(excluded_dests={"season", "episode"})

    cmd = builder.build(parser.parse_args([]), parser, context, site_option_dests=["season", "episode", "vcodec"])

    assert cmd.count("--season") == 1 and cmd.count("--episode") == 1 and "--vcodec AVC" in cmd
