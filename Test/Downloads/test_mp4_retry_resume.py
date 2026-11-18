# 07.10.26

import pytest

from VibraVid.core.downloader import mp4


class _FakeResponse:
    def __init__(self, status_code, headers, chunks, raise_after_index=None):
        self.status_code = status_code
        self.headers = headers
        self._chunks = chunks
        self._raise_after_index = raise_after_index

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=65536):
        for i, chunk in enumerate(self._chunks):
            yield chunk
            if self._raise_after_index is not None and i == self._raise_after_index:
                raise ConnectionError(
                    "curl: (92) HTTP/2 stream 1 was not closed cleanly: INTERNAL_ERROR (err 2)"
                )

    def close(self):
        pass


class _FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requests: list[dict] = []

    def get(self, url, headers=None, stream=True):
        self.requests.append({"url": url, "headers": headers})
        return self._responses.pop(0)


class _FakeBarManager:
    def handle_progress_line(self, parsed):
        pass


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(mp4.time, "sleep", lambda *_a, **_k: None)


@pytest.fixture
def downloader(tmp_path):
    d = mp4.MP4FileDownloader(
        url="http://example.test/video.mp4",
        path=str(tmp_path / "video.mp4"),
        check_content_type=False,
        close_tracking=False,
    )
    return d


def test_a_mid_stream_connection_error_resumes_instead_of_cancelling(monkeypatch, downloader):
    monkeypatch.setattr(mp4, "RETRY_COUNT", 2)

    first = _FakeResponse(200, {"content-length": "16"}, [b"AAAA", b"BBBB"], raise_after_index=1)
    second = _FakeResponse(206, {}, [b"CCCC", b"DDDD"])
    client = _FakeClient([first, second])

    downloader._stream_to_disk(client, {}, _FakeBarManager())

    assert downloader._downloaded == 16
    assert downloader._retryable_error is False
    assert downloader._interrupt.kill_download is False
    with open(downloader._temp_path, "rb") as fh:
        assert fh.read() == b"AAAABBBBCCCCDDDD"

    # The retry must resume from where it left off, not restart from byte 0.
    assert client.requests[0]["headers"] is None
    assert client.requests[1]["headers"] == {"Range": "bytes=8-"}


def test_retries_are_exhausted_after_retry_count_attempts(monkeypatch, downloader):
    monkeypatch.setattr(mp4, "RETRY_COUNT", 1)

    always_fails = lambda: _FakeResponse(206, {}, [b"X"], raise_after_index=0)
    client = _FakeClient(
        [
            _FakeResponse(200, {"content-length": "100"}, [b"A"], raise_after_index=0),
            always_fails(),
        ]
    )

    downloader._stream_to_disk(client, {}, _FakeBarManager())

    assert downloader._interrupt.kill_download is True
    assert len(client.requests) == 2  # 1 initial + 1 retry (RETRY_COUNT=1), then gives up


def test_a_server_that_ignores_the_range_request_restarts_from_scratch(monkeypatch, downloader):
    monkeypatch.setattr(mp4, "RETRY_COUNT", 2)

    first = _FakeResponse(200, {"content-length": "8"}, [b"AAAA"], raise_after_index=0)
    # Server doesn't support Range: responds 200 (full content) instead of 206.
    second = _FakeResponse(200, {"content-length": "8"}, [b"CCCCCCCC"])
    client = _FakeClient([first, second])

    downloader._stream_to_disk(client, {}, _FakeBarManager())

    assert downloader._interrupt.kill_download is False
    with open(downloader._temp_path, "rb") as fh:
        assert fh.read() == b"CCCCCCCC"


def test_a_deliberate_stop_is_never_retried(monkeypatch, downloader):
    monkeypatch.setattr(mp4, "RETRY_COUNT", 3)
    downloader._interrupt.force_quit = True

    client = _FakeClient([_FakeResponse(200, {"content-length": "4"}, [b"AAAA"])])

    downloader._stream_to_disk(client, {}, _FakeBarManager())

    assert len(client.requests) == 1  # no retry attempted
    assert downloader._retryable_error is False
