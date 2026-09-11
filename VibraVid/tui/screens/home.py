# 29.07.26
# by @ManoloZocco

"""Home screen: Search Engine style landing page with central search bar, scope selectors & filtered provider pills."""

import logging

from rich.cells import cell_len
from textual import events, on
from textual.app import ComposeResult
from textual.containers import (
    Container,
    Grid,
    Horizontal,
    VerticalScroll,
)
from textual.screen import Screen
from textual.widgets import Button, Input, Static

from VibraVid.tui.bridge import SiteInfo, sites_by_category
from VibraVid.tui.i18n import t
from VibraVid.tui.screens.search import SearchScreen
from VibraVid.tui.widgets.custom_footer import CustomFooter

logger = logging.getLogger(__name__)

# width of the " • " separator between two provider pills
SEPARATOR_WIDTH = 3

# cells a pill takes on top of its label: the .site-pill padding plus the two
# cells Textual reserves around a Button label. Guarded by the layout audit tests.
PILL_OVERHEAD = 4

# below this width the five scope pills no longer leave room for their labels
NARROW_WIDTH = 110

ASCII_LOGO = """[bold cyan]
██╗   ██╗██╗██████╗ ██████╗  █████╗ ██╗   ██╗██╗██████╗ 
██║   ██║██║██╔══██╗██╔══██╗██╔══██╗██║   ██║██║██╔══██╗
██║   ██║██║██████╔╝██████╔╝███████║██║   ██║██║██║  ██║
╚██╗ ██╔╝██║██╔══██╗██╔══██╗██╔══██║╚██╗ ██╔╝██║██║  ██║
 ╚████╔╝ ██║██████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║██████╔╝
[/bold cyan]"""


class HomeScreen(Screen):
    """Search Engine style main screen with central search, category selectors & live-filtered provider pills."""

    def __init__(self) -> None:
        super().__init__()
        self._selected_scope: str = "global"
        self._selected_site: str | None = None
        self._grouped: dict[str, list[SiteInfo]] = {}
        self._all_sites: list[SiteInfo] = []
        self._searching_lock: bool = False
        self._shown_sites: list[SiteInfo] = []
        self._pills_width: int = 0

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="home-outer"):
            with Container(id="home-card"):
                yield Static(ASCII_LOGO, id="home-logo")
                yield Static(
                    f"[bold #7aa2f7]{t('home_tagline')}[/bold #7aa2f7]",
                    id="home-tagline",
                )

                with Horizontal(id="home-search-bar"):
                    yield Input(placeholder=t("search_placeholder"), id="main-search-input")
                    yield Button(t("search_btn"), id="btn-submit-search", variant="primary")

                yield Static(t("select_search_category"), classes="home-section-title")
                with Grid(id="home-scope-pills"):
                    yield Button(f"🌐 {t('scope_global')}", id="scope-global", variant="primary", classes="scope-pill")
                    yield Button(f"🌸 {t('scope_anime')}", id="scope-anime", classes="scope-pill")
                    yield Button(f"🎬 {t('scope_film')}", id="scope-film", classes="scope-pill")
                    yield Button(f"📺 {t('scope_serie')}", id="scope-serie", classes="scope-pill")
                    yield Button(f"🎵 {t('scope_music')}", id="scope-music", classes="scope-pill")

                yield Static(t("filter_provider_site"), classes="home-section-title")
                with Horizontal(id="home-provider-filter-bar"):
                    yield Input(placeholder=t("filter_provider_placeholder"), id="provider-filter-input")

                with Container(id="home-sites-box"):
                    yield VerticalScroll(id="home-sites-wrap")

        yield CustomFooter()

    async def on_mount(self) -> None:
        self._grouped = sites_by_category()
        self._all_sites = []
        added_names = set()
        for site_list in self._grouped.values():
            for site in site_list:
                if site.name not in added_names:
                    added_names.add(site.name)
                    self._all_sites.append(site)

        await self._render_provider_pills(self._all_sites)
        self.query_one("#main-search-input", Input).focus()

    async def on_resize(self, event: events.Resize) -> None:
        """Adapt the card to the terminal: the logo is decoration, the controls are not."""
        self.query_one("#home-logo", Static).display = event.size.height >= 30
        self.query_one("#home-scope-pills", Grid).set_class(event.size.width < NARROW_WIDTH, "-narrow")
        if self._shown_sites and self._pills_available_width() != self._pills_width:
            await self._render_provider_pills(self._shown_sites)

    def _pills_available_width(self) -> int:
        """Usable width inside the provider box, before the layout has settled too."""
        measured = self.query_one("#home-sites-wrap", VerticalScroll).content_size.width
        if measured:
            return measured
        return max(20, min(104, int(self.size.width * 0.95)) - 12)

    def _rows_that_fit(self, buttons: list[Button], available: int) -> list[list[Button]]:
        """Pack the pills into rows by measured width instead of a fixed count per row.

        A fixed count overflows as soon as the terminal is narrow or the provider
        names are long, and the pills past the edge become unclickable.
        """
        rows: list[list[Button]] = []
        row: list[Button] = []
        used = 0
        for btn in buttons:
            width = cell_len(str(btn.label)) + PILL_OVERHEAD
            separator = SEPARATOR_WIDTH if row else 0
            if row and used + separator + width > available:
                rows.append(row)
                row, used, separator = [], 0, 0
            row.append(btn)
            used += separator + width
        if row:
            rows.append(row)
        return rows

    async def _render_provider_pills(self, sites: list[SiteInfo]) -> None:
        sites_wrap = self.query_one("#home-sites-wrap", VerticalScroll)
        await sites_wrap.remove_children()
        self._shown_sites = list(sites)
        self._pills_width = self._pills_available_width()

        is_all_active = (self._selected_site is None)
        all_variant = "primary" if is_all_active else "default"

        all_buttons = [Button(f"🌐 {t('all_providers')}", id="site-all", variant=all_variant, classes="site-pill")]
        for site in sites:
            btn_id = f"site-btn-{site.name.replace('_', '-')}"
            label = site.name.capitalize()
            is_active = (self._selected_site == site.name)
            variant = "primary" if is_active else "default"
            all_buttons.append(Button(label, id=btn_id, variant=variant, classes="site-pill"))

        rows = []
        for chunk in self._rows_that_fit(all_buttons, self._pills_width):
            row_items = []
            for idx, btn in enumerate(chunk):
                row_items.append(btn)
                if idx < len(chunk) - 1:
                    row_items.append(Static(" • ", classes="site-dot"))
            rows.append(Horizontal(*row_items, classes="site-pill-row"))

        await sites_wrap.mount(*rows)

    @on(Input.Changed, "#provider-filter-input")
    async def _on_provider_filter_changed(self, event: Input.Changed) -> None:
        query = event.value.strip().lower()
        if not query:
            filtered = self._all_sites
        else:
            filtered = [s for s in self._all_sites if query in s.name.lower() or query in (s.use_for or "").lower()]
        await self._render_provider_pills(filtered)

    # ── Scope and Site Pill Click Handlers ─────────────────────────────────

    @on(Button.Pressed, ".scope-pill")
    def _on_scope_pressed(self, event: Button.Pressed) -> None:
        for btn_id in ("#scope-global", "#scope-anime", "#scope-film", "#scope-serie", "#scope-music"):
            btn = self.query_one(btn_id, Button)
            btn.variant = "default"

        event.button.variant = "primary"
        if event.button.id == "scope-anime":
            self._selected_scope = "anime"
        elif event.button.id == "scope-film":
            self._selected_scope = "film"
        elif event.button.id == "scope-serie":
            self._selected_scope = "serie"
        elif event.button.id == "scope-music":
            self._selected_scope = "music"
        else:
            self._selected_scope = "global"

    @on(Button.Pressed, ".site-pill")
    def _on_site_pressed(self, event: Button.Pressed) -> None:
        for pill in self.query(".site-pill"):
            pill.variant = "default"

        event.button.variant = "primary"
        if event.button.id == "site-all":
            self._selected_site = None
        else:
            site_name = event.button.id[len("site-btn-"):].replace("-", "_")
            self._selected_site = site_name

    # ── Search Submission (Crash Guarded) ──────────────────────────────────

    @on(Input.Submitted, "#main-search-input")
    @on(Button.Pressed, "#btn-submit-search")
    def _on_search(self) -> None:
        if self._searching_lock:
            return
        self._searching_lock = True
        try:
            query = self.query_one("#main-search-input", Input).value.strip()
            if not query:
                self.app.notify(t("enter_title_warning"), severity="warning")
                return

            screen = SearchScreen(site=self._selected_site, initial_query=query)
            if self._selected_scope != "global":
                screen._current_filter_category = self._selected_scope
            self.app.push_screen(screen)
        finally:
            self.set_timer(0.6, self._unlock_search)

    def _unlock_search(self) -> None:
        self._searching_lock = False

    # ── Keyboard Directional Navigation ────────────────────────────────────

    def action_nav_left(self) -> None:
        """Left Arrow: move to previous sibling button if focused on pill."""
        focused = self.focused
        if isinstance(focused, Button) and focused.parent:
            children = [c for c in focused.parent.children if isinstance(c, Button)]
            if focused in children:
                idx = children.index(focused)
                if idx > 0:
                    children[idx - 1].focus()

    def action_nav_right(self) -> None:
        """Right Arrow: move to next sibling button if focused on pill."""
        focused = self.focused
        if isinstance(focused, Button) and focused.parent:
            children = [c for c in focused.parent.children if isinstance(c, Button)]
            if focused in children:
                idx = children.index(focused)
                if idx < len(children) - 1:
                    children[idx + 1].focus()
