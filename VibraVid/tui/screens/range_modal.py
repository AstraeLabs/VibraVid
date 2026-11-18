# 31.07.26
# by @ManoloZocco

"""Modal dialog for entering episode range expressions (e.g. 1-10, 15, odd, even)."""


from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from VibraVid.tui.i18n import t


class RangeSelectModal(ModalScreen[str | None]):
    """Modal dialog to enter episode range expression."""

    def compose(self) -> ComposeResult:
        with Vertical(id="range-modal-box"):
            yield Static(t("range_modal_title"), classes="panel-title")
            yield Static(t("range_modal_hint"), classes="placeholder-hint")
            yield Input(
                placeholder=t("range_modal_placeholder"),
                id="range-input",
            )
            with Horizontal(id="modal-buttons"):
                yield Button(t("confirm"), id="submit-btn", variant="primary")
                yield Button(t("cancel"), id="cancel-btn")

    @on(Button.Pressed, "#submit-btn")
    def _on_submit(self) -> None:
        val = self.query_one("#range-input", Input).value.strip()
        self.dismiss(val if val else None)

    @on(Button.Pressed, "#cancel-btn")
    def _on_cancel(self) -> None:
        self.dismiss(None)

    @on(Input.Submitted, "#range-input")
    def _on_input_submitted(self) -> None:
        val = self.query_one("#range-input", Input).value.strip()
        self.dismiss(val if val else None)
