# 23.10.26

"""StreamingMuxFeeder: an ffmpeg that finished cleanly (``-t`` cap reached) while input was still being fed is a success, not a broken pipe."""

import sys

from VibraVid.core.muxing.streaming_mux import StreamingMuxFeeder

CHUNK = b"x" * (1 << 20)


def _feed_all(feeder, chunks=24):
    for _ in range(chunks):
        if not feeder.feed(CHUNK):
            break


def test_ffmpeg_exiting_cleanly_before_the_input_ends_is_ok():
    # Reads a little and exits 0, like ffmpeg stopping at ``-t`` while the download keeps producing chunks
    feeder = StreamingMuxFeeder([sys.executable, "-c", "import sys; sys.stdin.buffer.read(10)"])
    feeder.start()
    _feed_all(feeder)
    result = feeder.finish()

    assert result.ok and result.returncode == 0
    assert not feeder.failed


def test_ffmpeg_dying_with_an_error_is_still_a_failure():
    feeder = StreamingMuxFeeder([sys.executable, "-c", "import sys; sys.stdin.buffer.read(10); sys.exit(1)"])
    feeder.start()
    _feed_all(feeder)
    result = feeder.finish()

    assert not result.ok
    assert result.returncode == 1


def test_all_input_consumed_is_ok():
    feeder = StreamingMuxFeeder([sys.executable, "-c", "import sys; sys.stdin.buffer.read()"])
    feeder.start()
    _feed_all(feeder, chunks=4)
    assert feeder.finish().ok
