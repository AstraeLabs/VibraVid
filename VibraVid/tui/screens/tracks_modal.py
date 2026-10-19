# 19.10.26

"""Modal listing the tracks of a manifest so the user picks which ones to download (auto_select = false)."""

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, SelectionList, Static

from VibraVid.tui.i18n import t


def describe_track(track: dict) -> str:
    """One line per track: type, resolution/channels, codec, language, bitrate and flags."""
    kind = track.get("type", "")
    parts = [kind.ljust(8)]
    detail = track.get("resolution") or (f"{track['channels']}ch" if track.get("channels") else "")
    if detail:
        parts.append(str(detail))
    if track.get("video_range"):
        parts.append(track["video_range"])
    if track.get("codecs"):
        parts.append(track["codecs"])
    if track.get("language"):
        parts.append(track["language"])
    if track.get("bitrate"):
        parts.append(f"{track['bitrate'] // 1000} kbps")
    for flag in ("forced", "sdh", "cc"):
        if track.get(flag):
            parts.append(flag.upper())
    return "  ".join(parts)


class TrackSelectModal(ModalScreen[list[int] | None]):
    """Returns the chosen track indices, or None when cancelled."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title: str, tracks: list[dict]) -> None:
        super().__init__()
        self._title = title
        self._tracks = tracks

    def compose(self) -> ComposeResult:
        with Vertical(id="tracks-modal-box"):
            yield Static(t("tracks_modal_title"), classes="panel-title")
            yield Static(self._title, classes="placeholder-hint")
            yield SelectionList[int](
                *[(describe_track(track), track["index"], bool(track.get("selected"))) for track in self._tracks],
                id="tracks-list",
            )
            yield Static(t("tracks_modal_hint"), classes="placeholder-hint")
            with Horizontal(id="modal-buttons"):
                yield Button(t("confirm"), id="submit-btn", variant="primary")
                yield Button(t("cancel"), id="cancel-btn")

    def on_mount(self) -> None:
        self.query_one("#tracks-list", SelectionList).focus()

    @on(Button.Pressed, "#submit-btn")
    def _on_submit(self) -> None:
        chosen = sorted(self.query_one("#tracks-list", SelectionList).selected)
        if not chosen:
            self.app.notify(t("tracks_select_one"), severity="warning")
            return
        self.dismiss(chosen)

    @on(Button.Pressed, "#cancel-btn")
    def action_cancel(self) -> None:
        self.dismiss(None)
