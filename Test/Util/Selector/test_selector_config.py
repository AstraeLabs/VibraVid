# 25.03.26
# ruff: noqa: E402

import sys
from pathlib import Path


workspace_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(workspace_root))


from mock_streams import (
    create_video_streams_example1,
    create_video_streams_example2,
    create_audio_streams_example1,
    create_audio_streams_example2,
    create_audio_streams_example3,
    create_audio_streams_with_default,
    create_subtitle_streams_with_default,
)
from VibraVid.core.utils.selector import StreamSelector


def run_case(name, streams, video_filter, audio_filter, subtitle_filter, expected_behavior):
    """Run a test case with the given configuration."""
    print(f"\n{'=' * 70}")
    print(f"TEST: {name}")
    print(f"Filters: video={video_filter!r} | audio={audio_filter!r} | subtitle={subtitle_filter!r}")
    print(f"Expected: {expected_behavior}")
    print(f"{'=' * 70}")

    # Reset selected flags
    for s in streams:
        s.selected = False

    selector = StreamSelector(video_filter, audio_filter, subtitle_filter)
    sv, sa, ss = selector.apply(streams)

    print("\nResult N3u8dl select arguments:")
    print(f"  Video:    {sv!r}")
    print(f"  Audio:    {sa!r}")
    print(f"  Subtitle: {ss!r}")

    return sv, sa, ss


def run_audio_tests():
    """Test audio selection with new fallback strategy."""
    print("\n" + "=" * 80)
    print("AUDIO SELECTION TESTS - Configuration Validation")
    print("=" * 80)

    # Test 1: select_audio="ita" - Find Italian, DROP if not found
    print("\n" + "-" * 80)
    print("TEST GROUP 1: select_audio='ita' (Find Italian, DROP if not found)")
    print("-" * 80)

    streams = create_audio_streams_example1()
    run_case(
        "Audio Test 1.1: Italian available",
        streams,
        "best",
        "ita",
        "false",
        "Should select Italian audio with best bitrate",
    )

    streams = create_audio_streams_example2()
    run_case(
        "Audio Test 1.2: Italian NOT available - must DROP",
        streams,
        "best",
        "ita",
        "false",
        "Should DROP (no Italian found)",
    )

    # Test 2: select_audio="ita|best" - Find Italian best, NO fallback
    print("\n" + "-" * 80)
    print("TEST GROUP 2: select_audio='ita|best' (Find Italian best, DROP if not found)")
    print("-" * 80)

    streams = create_audio_streams_example3()
    run_case(
        "Audio Test 2.1: Multiple Italian tracks",
        streams,
        "best",
        "ita|best",
        "false",
        "Should select Italian with best bitrate (256kbps ac-3)",
    )

    streams = create_audio_streams_example2()
    run_case(
        "Audio Test 2.2: Italian NOT available - must DROP",
        streams,
        "best",
        "ita|best",
        "false",
        "Should DROP (no Italian found)",
    )

    # Test 3: select_audio="ita|best,AAC" - Find Italian AAC best, DROP if not found
    print("\n" + "-" * 80)
    print("TEST GROUP 3: select_audio='ita|best,MP4A' (Find Italian MP4A best, DROP if not found)")
    print("-" * 80)

    streams = create_audio_streams_example3()
    run_case(
        "Audio Test 3.1: Italian MP4A available",
        streams,
        "best",
        "ita|best,mp4a",
        "false",
        "Should select Italian MP4A (128kbps)",
    )

    streams = create_audio_streams_example1()
    run_case(
        "Audio Test 3.2: Italian MP4A available - with other languages",
        streams,
        "best",
        "ita|best,mp4a",
        "false",
        "Should DROP (request says ita|best,mp4a but should still find ita mp4a)",
    )

    streams = create_audio_streams_example2()
    run_case(
        "Audio Test 3.3: Italian MP4A NOT available - must DROP",
        streams,
        "best",
        "ita|best,mp4a",
        "false",
        "Should DROP (no Italian + MP4A found)",
    )

    # Test 4: select_audio="all" - All audio tracks
    print("\n" + "-" * 80)
    print("TEST GROUP 4: select_audio='all' (All audio)")
    print("-" * 80)

    streams = create_audio_streams_example1()
    run_case(
        "Audio Test 4.1: Select all audio", streams, "best", "all", "false", "Should select all available audio tracks"
    )


def run_video_tests():
    """Test video selection with fallback strategy."""
    print("\n" + "=" * 80)
    print("VIDEO SELECTION TESTS - Configuration Validation")
    print("=" * 80)

    # Test 1: select_video="1080" - Find 1080p, fallback to WORST if not found
    print("\n" + "-" * 80)
    print("TEST GROUP 1: select_video='1080' (Find 1080p, fallback to worst)")
    print("-" * 80)

    streams = create_video_streams_example1()
    run_case(
        "Video Test 1.1: 1080p available",
        streams,
        "1080",
        "best",
        "false",
        "Should select 1080p, best bitrate among resolutions",
    )

    streams = create_video_streams_example2()
    run_case(
        "Video Test 1.2: 1080p NOT available - must fallback to WORST (480p)",
        streams,
        "1080",
        "best",
        "false",
        "Should fallback to worst resolution (480p)",
    )

    # Test 2: select_video="1080|best" - Find 1080p, fallback to BEST if not found
    print("\n" + "-" * 80)
    print("TEST GROUP 2: select_video='1080|best' (Find 1080p, fallback to best)")
    print("-" * 80)

    streams = create_video_streams_example1()
    run_case("Video Test 2.1: 1080p available", streams, "1080|best", "best", "false", "Should select 1080p")

    streams = create_video_streams_example2()
    run_case(
        "Video Test 2.2: 1080p NOT available - must fallback to BEST (720p)",
        streams,
        "1080|best",
        "best",
        "false",
        "Should fallback to best available (720p)",
    )

    # Test 3: select_video="1080,H265" - Find 1080p H.265, fallback to WORST if not found
    print("\n" + "-" * 80)
    print("TEST GROUP 3: select_video='1080,H265' (Find 1080p H.265, fallback to worst)")
    print("-" * 80)

    streams = create_video_streams_example1()
    run_case(
        "Video Test 3.1: 1080p H.265 available",
        streams,
        "1080,h265",
        "best",
        "false",
        "Should select 1080p hvc1 (2000kbps)",
    )

    # Test 4: select_video="1080|best,H265" - Find 1080p H.265, fallback to best H.265
    print("\n" + "-" * 80)
    print("TEST GROUP 4: select_video='1080|best,H265' (Find 1080p H.265, fallback to best H.265)")
    print("-" * 80)

    streams = create_video_streams_example2()
    run_case(
        "Video Test 4.1: 1080p H.265 NOT available - fallback to best H.265 (720p)",
        streams,
        "1080|best,h265",
        "best",
        "false",
        "Should fallback to best H.265 available (720p hvc1)",
    )


def run_default_flag_tests():
    """Test audio/subtitle selection with default flag filter."""
    print("\n" + "=" * 80)
    print("DEFAULT FLAG SELECTION TESTS - Selecting streams marked as default")
    print("=" * 80)

    # Test 1: audio="default" - Select only audio streams with default=True
    print("\n" + "-" * 80)
    print("TEST GROUP 1: select_audio='default' (Select only default audio)")
    print("-" * 80)

    streams = create_audio_streams_with_default()
    run_case(
        "Audio Test 5.1: Select only default audio track",
        streams,
        "best",
        "default",
        "false",
        "Should select English audio (marked as default=True)",
    )

    # Test 2: audio="non-default" - Select only audio streams with default=False
    print("\n" + "-" * 80)
    print("TEST GROUP 2: select_audio='non-default' (Select only non-default audio)")
    print("-" * 80)

    streams = create_audio_streams_with_default()
    run_case(
        "Audio Test 5.2: Select non-default audio tracks (best)",
        streams,
        "best",
        "non-default",
        "false",
        "Should select Italian audio (best bitrate, default=False)",
    )

    # Test 3: subtitle="default" - Select only subtitle streams with default=True
    print("\n" + "-" * 80)
    print("TEST GROUP 3: select_subtitle='default' (Select only default subtitle)")
    print("-" * 80)

    streams = create_subtitle_streams_with_default()
    run_case(
        "Subtitle Test 6.1: Select only default subtitle",
        streams,
        "false",
        "false",
        "default",
        "Should select English subtitle (marked as default=True)",
    )

    # Test 4: subtitle="non-default" - Select non-default subtitle
    print("\n" + "-" * 80)
    print("TEST GROUP 4: select_subtitle='non-default' (Select only non-default subtitles)")
    print("-" * 80)

    streams = create_subtitle_streams_with_default()
    run_case(
        "Subtitle Test 6.2: Select non-default subtitles",
        streams,
        "false",
        "false",
        "non-default",
        "Should select English CC and Italian Forced (non-default)",
    )


if __name__ == "__main__":
    run_audio_tests()
    run_video_tests()
    run_default_flag_tests()

    print("\n" + "=" * 80)
    print("All configuration tests completed!")
    print("=" * 80)
