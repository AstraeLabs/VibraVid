# 15.09.26

import os
import tempfile

from VibraVid.core.muxing.helper.chapters import sort_chapters, write_ffmetadata_chapters
from VibraVid.core.velora._decrypt_pipeline import DecryptPipelineMixin


class _FakeResult:
    def __init__(self, ok, error=None, stderr_tail=""):
        self.ok = ok
        self.error = error
        self.stderr_tail = stderr_tail


class _FakeFeeder:
    def __init__(self, ok):
        self._ok = ok
        self.aborted = False

    def abort(self):
        self.aborted = True

    def finish(self):
        return _FakeResult(self._ok, error=None if self._ok else "boom")


def _make_mixin(output_path, chapter_file, chapter_pending=True):
    obj = object.__new__(DecryptPipelineMixin)
    obj._streaming_mux_output_path = output_path
    obj._streaming_mux_chapter_pending = chapter_pending
    obj._streaming_mux_chapter_file = chapter_file
    obj.streaming_mux_result = None
    obj.streaming_mux_chapters_injected = False
    return obj


def test_write_ffmetadata_chapters_orders_and_formats():
    chapters = sort_chapters([{"name": "Credits", "seconds": 2572}, {"name": "Intro", "seconds": 0}])
    path = write_ffmetadata_chapters(chapters)
    try:
        content = open(path, encoding="utf-8").read()
    finally:
        os.unlink(path)
    assert "START=0" in content
    assert "START=2572000" in content
    assert content.index("title=Intro") < content.index("title=Credits")


def test_finish_streaming_mux_success_marks_chapters_injected_and_cleans_up():
    chapter_file = tempfile.NamedTemporaryFile(delete=False, suffix=".ffmeta")
    chapter_file.close()
    out_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mkv")
    out_file.write(b"x")
    out_file.close()

    obj = _make_mixin(out_file.name, chapter_file.name)
    try:
        obj._finish_streaming_mux(_FakeFeeder(True), True)
        assert obj.streaming_mux_result == out_file.name
        assert obj.streaming_mux_chapters_injected is True
        assert not os.path.exists(chapter_file.name)
    finally:
        os.unlink(out_file.name)


def test_finish_streaming_mux_fallback_keeps_chapters_injected_false():
    """If the fast path aborts, the normal mux still needs its own chapter injection --
    streaming_mux_chapters_injected must not be left True from the aborted attempt."""
    chapter_file = tempfile.NamedTemporaryFile(delete=False, suffix=".ffmeta")
    chapter_file.close()

    obj = _make_mixin("/nonexistent/output.mkv", chapter_file.name)
    obj._finish_streaming_mux(_FakeFeeder(True), False)  # live_merge_ok=False -> abort

    assert obj.streaming_mux_result is None
    assert obj.streaming_mux_chapters_injected is False
    assert not os.path.exists(chapter_file.name)


def test_finish_streaming_mux_ffmpeg_failure_keeps_chapters_injected_false():
    chapter_file = tempfile.NamedTemporaryFile(delete=False, suffix=".ffmeta")
    chapter_file.close()

    obj = _make_mixin("/nonexistent/output.mkv", chapter_file.name)
    obj._finish_streaming_mux(_FakeFeeder(False), True)  # ffmpeg itself failed

    assert obj.streaming_mux_result is None
    assert obj.streaming_mux_chapters_injected is False
    assert not os.path.exists(chapter_file.name)


def test_finish_streaming_mux_without_pending_chapters_stays_false():
    out_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mkv")
    out_file.write(b"x")
    out_file.close()

    obj = _make_mixin(out_file.name, chapter_file=None, chapter_pending=False)
    try:
        obj._finish_streaming_mux(_FakeFeeder(True), True)
        assert obj.streaming_mux_result == out_file.name
        assert obj.streaming_mux_chapters_injected is False
    finally:
        os.unlink(out_file.name)
