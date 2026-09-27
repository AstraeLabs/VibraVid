from GUI.searchapp.views import _shared


class _FailedProvider:
    def start_download(self, media_item, season=None, episodes=None):
        return False


def test_provider_false_result_is_terminal_failure(monkeypatch):
    provider = _FailedProvider()

    monkeypatch.setattr(_shared, "get_api", lambda site: provider)
    monkeypatch.setattr(_shared, "_add_scheduled_download", lambda *args, **kwargs: None)
    monkeypatch.setattr(_shared, "_remove_scheduled_download", lambda *args, **kwargs: None)
    monkeypatch.setattr(_shared, "_acquire_download_slot", lambda: None)
    monkeypatch.setattr(_shared, "_release_download_slot", lambda: None)
    monkeypatch.setattr(_shared, "_is_scheduled_cancelled", lambda download_id: False)
    monkeypatch.setattr(_shared, "_log_gui_equivalent_command", lambda *args, **kwargs: None)

    class _ImmediateFuture:
        def __init__(self, fn):
            try:
                self._result = fn()
                self._error = None
            except Exception as exc:
                self._result = None
                self._error = exc

        def result(self):
            if self._error:
                raise self._error
            return self._result

    monkeypatch.setattr(_shared, "_submit_download_task", lambda fn: _ImmediateFuture(fn))

    failures = []
    monkeypatch.setattr(
        _shared,
        "_mark_task_failed",
        lambda download_id, title, site, media_type, error: failures.append(
            {
                "download_id": download_id,
                "title": title,
                "site": site,
                "media_type": media_type,
                "error": error,
            }
        ),
    )

    future = _shared._run_download_in_thread(
        "altadefinizione",
        {
            "name": "Cime tempestose",
            "type": "film",
            "slug": "",
        },
    )

    try:
        future.result()
    except RuntimeError as exc:
        assert str(exc) == "altadefinizione reported that the download did not complete successfully"
    else:
        raise AssertionError("provider False result must fail the GUI download task")

    assert len(failures) == 1
    assert failures[0]["site"] == "altadefinizione"
    assert failures[0]["title"] == "Cime tempestose"
    assert failures[0]["error"] == "altadefinizione reported that the download did not complete successfully"
