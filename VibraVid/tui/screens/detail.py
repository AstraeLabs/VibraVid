# 29.07.26
# by @ManoloZocco

"""Title detail screen: metadata, season/episode multi-select, DSL preview, directional nav & QoL shortcuts."""

import logging

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import Screen
from textual.widgets import (
    Button,
    Header,
    ListItem,
    ListView,
    LoadingIndicator,
    SelectionList,
    Static,
)
from textual.widgets._selection_list import Selection

from VibraVid.tui import bridge
from VibraVid.tui.i18n import t
from VibraVid.tui.screens.range_modal import RangeSelectModal
from VibraVid.tui.widgets.custom_footer import CustomFooter
from VibraVid.tui.widgets.range_selection_list import RangeSelectionList, parse_range_expression

logger = logging.getLogger(__name__)


def compact_ranges(numbers: set[int], total: int) -> str:
    """Compact a set of ints into the CLI DSL: [1,2,3,5] -> '1-3,5'; all -> '*'."""
    if not numbers:
        return ""
    ordered = sorted(set(numbers))
    if len(ordered) >= total > 0:
        return "*"

    parts = []
    start = prev = ordered[0]
    for n in ordered[1:]:
        if n == prev + 1:
            prev = n
            continue
        parts.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = n
    parts.append(str(start) if start == prev else f"{start}-{prev}")
    return ",".join(parts)


class TitleDetailScreen(Screen):
    """Shows one catalog item; for series lets the user pick episodes across providers."""

    BINDINGS = [
        Binding("a", "select_all_episodes", t("select_all")),
        Binding("u", "deselect_all_episodes", t("clear_selection")),
        Binding("r", "range_select_modal", t("range_selection")),
        Binding("v", "toggle_visual_anchor", t("visual_range")),
        Binding("i", "invert_episodes", t("invert_selection")),
    ]

    def __init__(
        self,
        site: str,
        item,
        providers: list[tuple[str, object]] | None = None,
    ) -> None:
        super().__init__()
        if not providers:
            providers = [(site, item)]
        self._providers: list[tuple[str, object]] = list(providers)
        self._current_site: str = site
        self._item = item
        self._seasons: list = []
        self._current_season: int | None = None
        self._episode_selections: dict[int, set[int]] = {}

    @property
    def _site(self) -> str:
        return self._current_site

    @property
    def _is_single(self) -> bool:
        return bool(getattr(self._item, "is_movie", False) or getattr(self._item, "is_song", False))

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="detail-panel"):
            yield Static(str(getattr(self._item, "name", "?")), classes="detail-title")
            yield Static(self._meta_line(), classes="detail-meta")
            yield Static(self._desc_line(), classes="detail-meta")
            with Vertical(id="series-area"):
                yield Static(t("seasons_episodes_title"), classes="panel-title")
                yield LoadingIndicator(id="seasons-loading")
                with Horizontal(id="series-browser"):
                    with Vertical(id="providers-box"):
                        yield Static(t("providers"), classes="box-title")
                        yield ListView(id="providers")
                    with Vertical(id="seasons-box"):
                        yield Static(t("seasons"), classes="box-title")
                        yield ListView(id="seasons")
                    with Vertical(id="episodes-box"):
                        yield Static(t("episodes"), classes="box-title")
                        yield RangeSelectionList(id="episodes")
                yield Static("", id="dsl-preview", classes="dsl-preview")
                with Horizontal(id="actions-row"):
                    yield Button(t("download_selected_episodes"), id="dl", variant="primary")
                    yield Button(t("add_to_queue"), id="queue")
        yield CustomFooter()

    def _meta_line(self) -> str:
        parts = [
            f"type: {getattr(self._item, 'type', '?')}",
            f"site: {self._site}",
        ]
        year = getattr(self._item, "year", None)
        if year:
            parts.append(f"year: {year}")
        slug = getattr(self._item, "slug", None)
        if slug:
            parts.append(f"slug: {slug}")
        return "  ·  ".join(parts)

    def _desc_line(self) -> str:
        desc = str(getattr(self._item, "desc", "") or "").strip()
        return desc[:300] + ("..." if len(desc) > 300 else "") if desc else ""

    def on_mount(self) -> None:
        if self._is_single:
            self.query_one("#series-area", Vertical).display = False
            self.query_one("#dsl-preview", Static).update("selections = None (single item)")
            self.query_one("#dl", Button).focus()
            return
        self._load_providers()
        self._load_seasons()

    def _load_providers(self) -> None:
        providers_list = self.query_one("#providers", ListView)
        providers_list.clear()
        selected_idx = 0
        for idx, (site_name, p_item) in enumerate(self._providers):
            item_widget = ListItem(Static(site_name))
            item_widget.provider_payload = (site_name, p_item)
            providers_list.append(item_widget)
            if site_name == self._current_site:
                selected_idx = idx
        if self._providers:
            providers_list.index = selected_idx

    @on(ListView.Highlighted, "#providers")
    @on(ListView.Selected, "#providers")
    def _on_provider_selected(self, event: ListView.Highlighted | ListView.Selected) -> None:
        item = getattr(event, "item", None)
        payload = getattr(item, "provider_payload", None) if item else None
        if not payload:
            return
        site_name, p_item = payload
        if site_name == self._current_site and p_item == self._item:
            return

        self._current_site = site_name
        self._item = p_item

        self.query_one(".detail-meta", Static).update(self._meta_line())

        self.query_one("#seasons", ListView).clear()
        self.query_one("#episodes", SelectionList).clear_options()
        self._episode_selections.clear()
        self._current_season = None

        self.query_one("#seasons-loading", LoadingIndicator).display = True
        self._load_seasons()

    # ── Directional Navigation (Left / Right) ──────────────────────────────

    def action_nav_left(self) -> None:
        """Left Arrow: move between Download/Queue -> Episodes -> Seasons -> Providers -> pop_screen."""
        if self._is_single:
            self.app.pop_screen()
            return

        providers = self.query_one("#providers", ListView)
        seasons = self.query_one("#seasons", ListView)
        episodes = self.query_one("#episodes", SelectionList)
        dl_btn = self.query_one("#dl", Button)
        queue_btn = self.query_one("#queue", Button)
        seasons_box = self.query_one("#seasons-box")

        if self.focused in (dl_btn, queue_btn):
            episodes.focus()
        elif self.focused == episodes:
            seasons.focus()
        elif self.focused in (seasons, seasons_box) or (self.focused and self.focused in seasons_box.walk_children()):
            providers.focus()
        else:
            self.app.pop_screen()

    def action_nav_right(self) -> None:
        """Right Arrow: move between Providers -> Seasons -> Episodes -> Download button."""
        if self._is_single:
            self.query_one("#dl", Button).focus()
            return

        providers = self.query_one("#providers", ListView)
        seasons = self.query_one("#seasons", ListView)
        episodes = self.query_one("#episodes", SelectionList)
        providers_box = self.query_one("#providers-box")
        seasons_box = self.query_one("#seasons-box")

        if self.focused in (providers, providers_box) or (self.focused and self.focused in providers_box.walk_children()):
            seasons.focus()
        elif self.focused in (seasons, seasons_box) or (self.focused and self.focused in seasons_box.walk_children()):
            episodes.focus()
        elif self.focused == episodes:
            self.query_one("#dl", Button).focus()

    # ── Episode Selection Shortcuts (a / u) ──────────────────────────────

    def action_select_all_episodes(self) -> None:
        """Shortcut 'a': Select all episodes in current season."""
        if self._current_season is None or self._is_single:
            return
        episodes: RangeSelectionList = self.query_one("#episodes", RangeSelectionList)
        episodes.select_all()
        self.app.notify(t("selected_all_episodes_msg"), severity="information")

    def action_deselect_all_episodes(self) -> None:
        """Shortcut 'u': Clear all episode selections in current season."""
        if self._current_season is None or self._is_single:
            return
        episodes: RangeSelectionList = self.query_one("#episodes", RangeSelectionList)
        episodes.deselect_all()
        self.app.notify(t("cleared_episodes_msg"), severity="information")

    @on(RangeSelectionList.RequestRangeModal, "#episodes")
    def _on_request_range_modal(self) -> None:
        self.action_range_select_modal()

    def action_range_select_modal(self) -> None:
        """Shortcut 'r': Open range expression modal dialog."""
        if self._current_season is None or self._is_single:
            return

        def _apply_range(expr: str | None) -> None:
            if not expr or self._current_season is None:
                return
            season_data = next((s for s in self._seasons if s.number == self._current_season), None)
            if not season_data:
                return
            available_eps = [ep.number for ep in (getattr(season_data, "episodes", []) or [])]
            matched = parse_range_expression(expr, available_eps)
            episodes: RangeSelectionList = self.query_one("#episodes", RangeSelectionList)
            episodes.deselect_all()
            for ep_num in matched:
                episodes.select(ep_num)
            self.app.notify(
                t("episodes_range_selected", count=len(matched), expr=expr),
                severity="information",
            )

        self.app.push_screen(RangeSelectModal(), _apply_range)

    def action_toggle_visual_anchor(self) -> None:
        """Shortcut 'v': Toggle visual anchor mode on episode list."""
        if self._current_season is None or self._is_single:
            return
        episodes: RangeSelectionList = self.query_one("#episodes", RangeSelectionList)
        is_active, start_idx, end_idx = episodes.toggle_visual_anchor()
        if is_active:
            ep_val = (
                episodes.get_option_at_index(start_idx).value
                if start_idx is not None and start_idx < episodes.option_count
                else "?"
            )
            self.app.notify(
                f"Ancora impostata a E{ep_val}. Spostati e premi 'v' o INVIO per selezionare il range.",
                severity="information",
            )
        else:
            self.app.notify("Range di episodi selezionato!", severity="information")

    def action_invert_episodes(self) -> None:
        """Shortcut 'i': Invert episode selection in current season."""
        if self._current_season is None or self._is_single:
            return
        episodes: RangeSelectionList = self.query_one("#episodes", RangeSelectionList)
        episodes.invert_selection()
        self.app.notify("Selezione episodi invertita!", severity="information")

    # ── Seasons loading (worker) ──────────────────────────────────────────

    @work(thread=True, exclusive=True, group="seasons")
    def _load_seasons(self) -> None:
        try:
            seasons = bridge.get_seasons(self._site, self._item)
        except Exception as e:
            logger.exception("season loading failed")
            self.app.call_from_thread(self._seasons_failed, str(e))
            return
        self.app.call_from_thread(self._apply_seasons, seasons or [])

    def _seasons_failed(self, message: str) -> None:
        # The worker can finish after the user left this screen, while its widgets are being removed.
        try:
            self.query_one("#seasons-loading", LoadingIndicator).display = False
            self.query_one("#dsl-preview", Static).update(f"[red]{t('could_not_load_seasons', message=message)}[/red]")
        except NoMatches:
            logger.debug("Season loading failed after the detail screen was closed: %s", message)

    def _apply_seasons(self, seasons: list) -> None:
        # The worker can finish after the user left this screen, while its widgets are being removed.
        try:
            self.query_one("#seasons-loading", LoadingIndicator).display = False
            self._seasons = list(seasons)
            season_list = self.query_one("#seasons", ListView)
            season_list.clear()
            if not self._seasons:
                self.query_one("#dsl-preview", Static).update(t("no_season_data"))
                return

            for season in self._seasons:
                count = len(getattr(season, "episodes", []) or [])
                label = f"S{season.number}  ·  {count} ep"
                item = ListItem(Static(label))
                item.season_payload = season
                season_list.append(item)
            season_list.index = 0
            self._show_season(self._seasons[0])
            season_list.focus()
        except NoMatches:
            logger.debug("Seasons loaded after the detail screen was closed")

    def _show_season(self, season) -> None:
        self._current_season = season.number
        selected = self._episode_selections.get(season.number, set())
        episodes: SelectionList = self.query_one("#episodes", SelectionList)
        episodes.clear_options()
        for ep in getattr(season, "episodes", []) or []:
            label = f"E{ep.number}  ·  {getattr(ep, 'name', '') or 'Episode'}"
            episodes.add_option(Selection(label, ep.number, initial_state=ep.number in selected))
        self._update_dsl()

    @on(ListView.Highlighted, "#seasons")
    def _on_season_highlighted(self, event: ListView.Highlighted) -> None:
        season = getattr(event.item, "season_payload", None) if event.item else None
        if season is not None and season.number != self._current_season:
            self._show_season(season)

    @on(SelectionList.SelectedChanged, "#episodes")
    def _on_episodes_changed(self) -> None:
        if self._current_season is None:
            return
        selected = set(self.query_one("#episodes", SelectionList).selected)
        self._episode_selections[self._current_season] = selected
        self._update_dsl()

    # ── DSL preview ───────────────────────────────────────────────────────

    def _update_dsl(self) -> None:
        picked = {s: eps for s, eps in self._episode_selections.items() if eps}
        if not picked:
            self.query_one("#dsl-preview", Static).update(t("dsl_hint"))
            return

        if len(picked) == 1:
            season, eps = next(iter(picked.items()))
            total = self._season_episode_count(season)
            text = f"selections = {{'season': '{season}', 'episode': '{compact_ranges(eps, total)}'}}"
        else:
            seasons = compact_ranges(set(picked), len(self._seasons))
            all_full = all(len(eps) >= self._season_episode_count(s) > 0 for s, eps in picked.items())
            episode = "*" if all_full else "<per-season>"
            text = f"selections = {{'season': '{seasons}', 'episode': '{episode}'}}"
        self.query_one("#dsl-preview", Static).update(text)

    def _season_episode_count(self, season_number: int) -> int:
        for season in self._seasons:
            if season.number == season_number:
                return len(getattr(season, "episodes", []) or [])
        return 0

    # ── Download action ───────────────────────────────────────────────────

    @on(Button.Pressed, "#dl")
    def _on_download(self) -> None:
        if self._is_single:
            self._start_download_worker(None, None)
        else:
            picked = {s: eps for s, eps in self._episode_selections.items() if eps}
            if not picked:
                self.app.notify(t("select_at_least_one_ep"), severity="warning")
                return
            if len(picked) == 1:
                season, eps = next(iter(picked.items()))
                total = self._season_episode_count(season)
                season_str = str(season)
                episode_str = compact_ranges(eps, total)
            else:
                seasons = compact_ranges(set(picked), len(self._seasons))
                all_full = all(len(eps) >= self._season_episode_count(s) > 0 for s, eps in picked.items())
                season_str = seasons
                episode_str = "*" if all_full else "1-*"
            self._start_download_worker(season_str, episode_str)

    @work(thread=True, exclusive=True, group="download")
    def _start_download_worker(self, season: str | None, episodes: str | None) -> None:
        from VibraVid.core.ui.tracker import context_tracker
        context_tracker.is_gui = True
        try:
            success = bridge.start_download(self._site, self._item, season=season, episodes=episodes)
            if success:
                self.app.call_from_thread(self.app.notify, t("download_started_bg"), severity="information")
            else:
                self.app.call_from_thread(self.app.notify, t("download_failed_to_start"), severity="error")
        except Exception as e:
            logger.exception("download failed")
            self.app.call_from_thread(self.app.notify, t("download_error", error=str(e)), severity="error")
        finally:
            context_tracker.is_gui = False

    # ── Queue action ──────────────────────────────────────────────────────

    @on(Button.Pressed, "#queue")
    def _on_queue(self) -> None:
        import uuid

        from VibraVid.cli.command.equivalent_command import EquivalentCommandBuilder
        from VibraVid.cli.command.queue import (
            _PROCESS_TAG,
            _load_queue,
            _now_iso,
            _queue_path,
            _QueueLock,
            _save_queue,
        )

        search_term = str(getattr(self._item, "name", "") or getattr(self._item, "title", "") or "")
        season_str = None
        episode_str = None

        if not self._is_single:
            picked = {s: eps for s, eps in self._episode_selections.items() if eps}
            if not picked:
                self.app.notify(t("select_at_least_one_ep_queue"), severity="warning")
                return
            if len(picked) == 1:
                season, eps = next(iter(picked.items()))
                total = self._season_episode_count(season)
                season_str = str(season)
                episode_str = compact_ranges(eps, total)
            else:
                seasons = compact_ranges(set(picked), len(self._seasons))
                all_full = all(len(eps) >= self._season_episode_count(s) > 0 for s, eps in picked.items())
                season_str = seasons
                episode_str = "*" if all_full else "1-*"

        builder = EquivalentCommandBuilder(excluded_dests=[])
        argv = builder.build_argv_from_params(
            site=self._site,
            search=search_term,
            item="1",
            season=season_str,
            episode=episode_str,
        )

        if not argv:
            self.app.notify(t("could_not_build_cmd_queue"), severity="error")
            return

        tag = _PROCESS_TAG
        path = _queue_path(tag)
        item = {
            "id": uuid.uuid4().hex[:8],
            "argv": argv,
            "status": "pending",
            "tag": tag,
            "enqueued_at": _now_iso(),
            "started_at": None,
            "finished_at": None,
            "returncode": None,
            "attempts": 0,
        }

        try:
            with _QueueLock(path):
                data = _load_queue(path)
                data.setdefault("items", []).append(item)
                _save_queue(path, data)
            self.app.notify(t("added_item_to_queue_msg", id=item['id'], title=search_term[:25]), severity="information")
        except Exception as e:
            logger.exception("Failed to enqueue item")
            self.app.notify(t("queue_error", error=str(e)), severity="error")
