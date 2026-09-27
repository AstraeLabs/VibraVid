# 12.06.26

import logging
import os
import time
from collections.abc import Callable

import httpx

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


class _ProgressReader:
    def __init__(self, path: str, on_progress: Callable[[int, int], None] | None = None):
        self._fh = open(path, "rb")
        self._total = os.path.getsize(path)
        self._on_progress = on_progress
        self._done = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self._fh.read(size)
        if not chunk:
            return b""
        self._done += len(chunk)
        if self._on_progress:
            self._on_progress(self._done, self._total)
        return chunk

    def __len__(self) -> int:
        return self._total

    def __iter__(self):
        return self

    def __next__(self) -> bytes:
        chunk = self.read(1 << 20)
        if not chunk:
            raise StopIteration
        return chunk

    def fileno(self):
        import io

        raise io.UnsupportedOperation("_ProgressReader has no fileno")

    def close(self):
        self._fh.close()


def new_upload_client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(connect=30.0, read=300.0, write=60.0, pool=30.0))


def upload_file(
    upload_client: httpx.Client,
    file_path: str,
    endpoint: str,
    filename: str | None = None,
    on_progress: Callable[[int, int | None], None] | None = None,
) -> dict:
    name = filename or os.path.basename(file_path)
    reader = _ProgressReader(file_path, on_progress=on_progress)
    try:
        r = upload_client.post(
            endpoint,
            params={"filename": name},
            content=reader,
            headers={"Content-Type": "application/octet-stream", "Content-Length": str(reader._total)},
        )
    finally:
        reader.close()
    r.raise_for_status()
    data = r.json()
    if not data.get("success"):
        raise RuntimeError(f"storage upload failed: {data}")
    return data


class IncompleteDownloadError(IOError):
    pass


def download_file(
    session,
    direct_url: str,
    dest_path: str,
    total: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    chunk_size: int = 1 << 20,
    retries: int = 15,
    backoff: int = 4,
    incomplete_retries: int = 2,
    incomplete_backoff: int = 2,
) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(dest_path)) or ".", exist_ok=True)
    headers = {"User-Agent": _UA}

    last_exc = None
    incomplete_attempts = 0
    for attempt in range(1, retries + 1):
        try:
            with session.get(direct_url, stream=True, timeout=300, headers=headers) as r:
                r.raise_for_status()
                dl_total = total
                resp_content_length = r.headers.get("Content-Length")
                if dl_total is None:
                    try:
                        dl_total = int(resp_content_length or 0) or None
                    except (TypeError, ValueError):
                        dl_total = None
                written = 0
                tmp_path = dest_path + ".part"
                with open(tmp_path, "wb") as out:
                    for chunk in r.iter_content(chunk_size=chunk_size):
                        if not chunk:
                            continue
                        out.write(chunk)
                        written += len(chunk)
                        if on_progress:
                            on_progress(written, dl_total)
                if dl_total is not None and written != dl_total:
                    os.remove(tmp_path)
                    raise IncompleteDownloadError(f"incomplete download: got {written} of {dl_total} bytes")
                os.replace(tmp_path, dest_path)
            return dest_path
        except IncompleteDownloadError as exc:
            last_exc = exc
            incomplete_attempts += 1
            if incomplete_attempts < incomplete_retries:
                time.sleep(incomplete_backoff)
                continue
            raise
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(backoff * attempt)
                continue
            raise
    if last_exc:
        raise last_exc
    return dest_path
