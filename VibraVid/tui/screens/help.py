# 30.07.26
# by @ManoloZocco

"""Modal help overlay listing all global & contextual keybindings."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from VibraVid.tui.i18n import t

_GLOBAL_KEYS = [
    ("F1 / H", "hk_home"),
    ("F2", "hk_search"),
    ("F3 / d", "hk_downloads"),
    ("F4 / q", "hk_queue"),
    ("F5 / h", "hk_history"),
    ("F6 / ,", "hk_settings"),
    ("F7 / s", "hk_system"),
    ("F8 / F9 / ?", "hk_help"),
    ("ESC", "hk_back"),
    ("Ctrl+Q", "hk_quit"),
]

_NAV_KEYS = [
    ("← / →", "hk_arrow_lr"),
    ("↑ / ↓", "hk_arrow_ud"),
    ("ENTER", "hk_enter"),
]

_CONTEXT_KEYS = [
    ("q (Ricerca)", "hk_search_quick_queue"),
    ("SPAZIO", "hk_search_select"),
    ("Q / Shift+Q", "hk_search_batch_queue"),
    ("s (Ricerca)", "hk_search_sort"),
    ("c / y", "hk_search_copy_cli"),
    ("R (Ricerca)", "hk_search_retry"),
    ("i (Ricerca)", "hk_search_detail"),
    ("1 - 5", "hk_search_select_provider"),
    ("a (Serie)", "hk_select_all"),
    ("u (Serie/Batch)", "hk_deselect_all"),
    ("r (Serie)", "hk_range_modal"),
    ("v (Serie)", "hk_visual_range"),
    ("i (Serie)", "hk_invert_selection"),
    ("Shift+Frecce", "hk_range_arrows"),
    ("Shift+Click", "hk_range_click"),
    ("Ctrl+S", "hk_save_section"),
]


class HelpScreen(ModalScreen):
    """Modal screen displaying organized keybindings and navigation guide."""

    BINDINGS = [
        Binding("escape", "close_help", "Close"),
        Binding("question_mark", "close_help", "Close"),
    ]

    def action_close_help(self) -> None:
        self.dismiss()

    def compose(self) -> ComposeResult:
        with Vertical(id="help-box"):
            yield Static(t("keyboard_help_guide"), classes="placeholder-title")
            with VerticalScroll(id="help-scroll"):
                yield Static(f"[bold #7aa2f7]{t('global_navigation')}[/bold #7aa2f7]", classes="help-section-header")
                for key, desc_key in _GLOBAL_KEYS:
                    yield Static(f"[bold #7dcfff]{key:>10}[/bold #7dcfff]  [#c0caf5]{t(desc_key)}[/#c0caf5]")

                yield Static(
                    f"\n[bold #7aa2f7]{t('navigation_keyboard')}[/bold #7aa2f7]", classes="help-section-header"
                )
                for key, desc_key in _NAV_KEYS:
                    yield Static(f"[bold #7dcfff]{key:>10}[/bold #7dcfff]  [#c0caf5]{t(desc_key)}[/#c0caf5]")

                yield Static(
                    f"\n[bold #7aa2f7]{t('contextual_shortcuts')}[/bold #7aa2f7]", classes="help-section-header"
                )
                for key, desc_key in _CONTEXT_KEYS:
                    yield Static(f"[bold #7dcfff]{key:>10}[/bold #7dcfff]  [#c0caf5]{t(desc_key)}[/#c0caf5]")

            yield Static(f"\n{t('press_esc_to_close')}", classes="placeholder-hint")
