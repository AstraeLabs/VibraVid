from GUI.searchapp.views import _shared


class _FailedProvider:
    def start_download(self, media_item, season=None, episodes=None):
        return False


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


def test_provider_false_result_fails_gui_download_task(monkeypatch):
    provider = _FailedProvider()

    monkeypatch.setattr(_shared, "get_api", lambda site: provider)
    monkeypatch.setattr(_shared, "_add_scheduled_download", lambda *args, **kwargs: None)
    monkeypatch.setattr(_shared, "_remove_scheduled_download", lambda *args, **kwargs: None)
    monkeypatch.setattr(_shared, "_acquire_download_slot", lambda: None)
    monkeypatch.setattr(_shared, "_release_download_slot", lambda: None)
    monkeypatch.setattr(_shared, "_is_scheduled_cancelled", lambda download_id: False)
    monkeypatch.setattr(_shared, "_log_gui_equivalent_command", lambda *args, **kwargs: None)
    monkeypatch.setattr(_shared, "_submit_download_task", lambda fn: _ImmediateFuture(fn))
    monkeypatch.setattr(_shared, "forget", lambda name: None)

    future = _shared._run_download_in_thread(
        "cineblog01",
        {
            "name": "Example",
            "type": "film",
            "slug": "",
        },
    )

    try:
        future.result()
    except RuntimeError as exc:
        assert str(exc) == "Provider reported download failure"
    else:
        raise AssertionError("provider False result must fail the GUI download task")
