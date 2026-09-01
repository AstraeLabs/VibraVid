# 29.07.26
# by @ManoloZocco

"""Search screen: query input + category filters + mouse-hover live metadata preview card + QoL hotkeys & batch actions."""

import logging
import re
import uuid
from dataclasses import dataclass

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Header, Input, LoadingIndicator, Static

from VibraVid.cli.command.equivalent_command import EquivalentCommandBuilder
from VibraVid.cli.command.queue import (
    _PROCESS_TAG,
    _load_queue,
    _now_iso,
    _queue_path,
    _QueueLock,
    _save_queue,
)
from VibraVid.core.ui.tracker import context_tracker, download_tracker
from VibraVid.tui import bridge
from VibraVid.tui.i18n import t
from VibraVid.tui.widgets.custom_footer import CustomFooter
from VibraVid.tui.widgets.fuzzy_list import FuzzyItem, FuzzyList
from VibraVid.utils.system_open import copy_to_clipboard

logger = logging.getLogger(__name__)

SORT_MODES = [
    "relevance",
    "year_desc",
    "year_asc",
    "providers_desc",
    "title_asc",
]

SORT_LABELS = {
    "relevance": "sort_relevance",
    "year_desc": "sort_year_desc",
    "year_asc": "sort_year_asc",
    "providers_desc": "sort_providers_desc",
    "title_asc": "sort_title_asc",
}


def _parse_year_filter(spec: str) -> tuple[int, int] | None:
    """Parse '2020' or '1990-2015' into (min, max); None if empty/invalid."""
    spec = (spec or "").strip()
    if not spec:
        return None
    parts = spec.split("-")
    try:
        if len(parts) == 1:
            year = int(parts[0])
            return (year, year)
        return (int(parts[0]), int(parts[1]))
    except ValueError:
        return None


def _item_year(item) -> int | None:
    try:
        return int(str(getattr(item, "year", "")).split("-")[0].strip())
    except (ValueError, TypeError):
        return None


def _normalize_title(title: str) -> str:
    cleaned = re.sub(r"[^\w\s]", "", (title or "").lower())
    return " ".join(cleaned.split())


def deduplicate_search_results(results: list[tuple[str, object]]) -> list[tuple[str, object, list[tuple[str, object]]]]:
    """Group search results by normalized title, year, and category, merging providers."""
    groups: dict[tuple[str, int | None, str], tuple[str, object, list[tuple[str, object]]]] = {}
    ordered_keys = []

    for site, item in results:
        name = getattr(item, "name", "") or getattr(item, "title", "") or ""
        norm_title = _normalize_title(str(name))
        year = _item_year(item)

        is_movie = getattr(item, "is_movie", False)
        is_song = getattr(item, "is_song", False)
        if is_movie:
            cat = "movie"
        elif is_song:
            cat = "music"
        else:
            cat = "serie"

        key = (norm_title, year, cat)
        if key not in groups:
            groups[key] = (site, item, [(site, item)])
            ordered_keys.append(key)
        else:
            _primary_site, _primary_item, providers = groups[key]
            providers.append((site, item))

    return [groups[k] for k in ordered_keys]


def _sort_results(
    results: list[tuple[str, object, list[tuple[str, object]]]],
    sort_mode: str,
) -> list[tuple[str, object, list[tuple[str, object]]]]:
    """Sort search results based on selected criteria."""
    if sort_mode == "year_desc":
        return sorted(results, key=lambda r: _item_year(r[1]) or -9999, reverse=True)
    elif sort_mode == "year_asc":
        return sorted(results, key=lambda r: _item_year(r[1]) or 9999)
    elif sort_mode == "providers_desc":
        return sorted(results, key=lambda r: len(r[2]), reverse=True)
    elif sort_mode == "title_asc":
        return sorted(
            results,
            key=lambda r: str(getattr(r[1], "name", "") or getattr(r[1], "title", "")).lower(),
        )
    return list(results)


def _get_library_status(item: object, site: str) -> str | None:
    """Check if item is already in history ('in_library') or queue ('in_queue')."""
    try:
        name = str(getattr(item, "name", "") or getattr(item, "title", "")).strip().lower()
        if not name:
            return None

        # 1. Check active downloads
        active = download_tracker.get_active_downloads()
        for d in active:
            d_title = str(d.get("title", "")).strip().lower()
            if name in d_title or d_title in name:
                return "in_queue"

        # 2. Check queue
        tag = _PROCESS_TAG
        path = _queue_path(tag)
        q_data = _load_queue(path)
        for q_item in q_data.get("items", []):
            if q_item.get("status") in ("pending", "running"):
                argv = " ".join(q_item.get("argv", [])).lower()
                if name in argv:
                    return "in_queue"

        # 3. Check history
        history = download_tracker.get_history()
        for h in history:
            if h.get("status") == "completed":
                h_title = str(h.get("title", "")).strip().lower()
                if name == h_title or (len(name) > 4 and name in h_title):
                    return "in_library"
    except Exception:
        pass
    return None


@dataclass
class SearchSnapshot:
    """Result set of the last search, kept on the app so it outlives the screen.

    Going Home unwinds the whole screen stack, which destroys the SearchScreen;
    without this the user has to run the same search again to get back to it.
    """

    site: str | None
    query: str
    year: str
    raw: list[tuple[str, object, list[tuple[str, object]]]]
    failed_sites: list[str]
    category: str
    sort_mode: str


class SearchScreen(Screen):
    """Runs catalog search and displays results alongside mouse-hover live metadata preview card and QoL controls."""

    BINDINGS = [
        Binding("q", "quick_enqueue", "Quick Queue", show=False),
        Binding("d", "quick_download", "Quick Download", show=False),
        Binding("i", "open_detail_forced", "Info/Detail", show=False),
        Binding("c", "copy_cli_command", "Copy CLI", show=False),
        Binding("y", "copy_cli_command", "Copy CLI", show=False),
        Binding("slash", "focus_filter", "Filter", show=False),
        Binding("s", "cycle_sort", "Sort", show=False),
        Binding("R", "retry_failed_sites", "Retry Timeout", show=False),
        Binding("Q", "batch_enqueue", "Batch Enqueue", show=False),
        Binding("u", "clear_selection", "Clear Selection", show=False),
        Binding("1", "select_preview_provider_0", "Prov 1", show=False),
        Binding("2", "select_preview_provider_1", "Prov 2", show=False),
        Binding("3", "select_preview_provider_2", "Prov 3", show=False),
        Binding("4", "select_preview_provider_3", "Prov 4", show=False),
        Binding("5", "select_preview_provider_4", "Prov 5", show=False),
    ]

    def __init__(self, site: str | None, initial_query: str = "") -> None:
        super().__init__()
        self._site = site
        self._initial_query = initial_query
        self._raw: list[tuple[str, object, list[tuple[str, object]]]] = []
        self._current_filter_category: str = "all"
        self._current_sort_mode: str = "relevance"
        self._highlighted_payload: tuple | None = None
        self._selected_keys: set[str] = set()
        self._selected_provider_index: int = 0
        self._failed_sites: list[str] = []
        self._last_query: str = ""

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="search-panel"):
            title = t("search_on_site", site=self._site) if self._site else t("global_search_title")
            yield Static(title, classes="panel-title")
            with Horizontal(id="search-inputs-bar"):
                yield Input(placeholder=t("search_input_placeholder"), id="query", value=self._initial_query)
                yield Input(placeholder=t("year_placeholder"), id="year")
                yield Button(t("sort_by", mode=t(SORT_LABELS[self._current_sort_mode])), id="sort-btn")
                yield Button(t("retry_failed_sites_btn", count=0), id="retry-failed-btn", variant="warning")

            with Grid(id="category-filter-pills"):
                yield Button(f"{t('all')} (0)", id="filter-all", variant="primary", classes="filter-pill")
                yield Button(f"🎬 {t('film')} (0)", id="filter-film", classes="filter-pill")
                yield Button(f"📺 {t('serie_anime')} (0)", id="filter-serie", classes="filter-pill")
                yield Button(f"🎵 {t('music')} (0)", id="filter-music", classes="filter-pill")

            yield LoadingIndicator(id="search-loading")
            yield Static("", id="search-status")
            with Horizontal(id="search-split"):
                with Vertical(id="results-container"):
                    yield FuzzyList(placeholder=t("filter_results_placeholder"), id="results")
                    with Horizontal(id="batch-actions-bar"):
                        yield Static("", id="batch-count-label")
                        yield Button(t("batch_enqueue_btn", count=0), id="btn-batch-enqueue", variant="primary")
                        yield Button(t("batch_clear_btn"), id="btn-batch-clear")
                with Vertical(id="preview-container"):
                    with Vertical(id="preview-box"):
                        yield Static(
                            t("preview_initial_text"),
                            id="search-preview-box",
                        )
                        with Vertical(id="preview-providers-box"):
                            yield Static(
                                t("active_provider_badge"), classes="preview-prov-title", id="preview-prov-title"
                            )
                            with Grid(id="preview-providers-wrap"):
                                pass
                        with Grid(id="preview-actions-row"):
                            yield Button(t("download_now"), id="preview-open-btn", variant="primary")
                            yield Button(t("add_to_queue"), id="preview-queue-btn")
                            yield Button(t("copy_cli_btn"), id="preview-copy-btn")
                            yield Button(t("detail_sheet_btn"), id="preview-detail-btn")
        yield CustomFooter()

    def on_mount(self) -> None:
        self.query_one("#search-loading", LoadingIndicator).display = False
        self.query_one("#preview-actions-row", Grid).display = False
        self.query_one("#preview-providers-box", Vertical).display = False
        self.query_one("#retry-failed-btn", Button).display = False
        self.query_one("#batch-actions-bar", Horizontal).display = False
        self.query_one("#query", Input).focus()
        if self._initial_query:
            self._start_search()
        else:
            self._restore_snapshot()

    # ── Directional Navigation (Left / Right) ──────────────────────────────

    def action_nav_left(self) -> None:
        """Left Arrow: move focus left from preview to results, or up to inputs, or go back."""
        query_input = self.query_one("#query", Input)
        year_input = self.query_one("#year", Input)
        results = self.query_one("#results", FuzzyList)
        open_btn = self.query_one("#preview-open-btn", Button)
        queue_btn = self.query_one("#preview-queue-btn", Button)
        copy_btn = self.query_one("#preview-copy-btn", Button)
        detail_btn = self.query_one("#preview-detail-btn", Button)

        preview_container = self.query_one("#preview-container")
        if self.focused in (open_btn, queue_btn, copy_btn, detail_btn) or (
            self.focused and self.focused in preview_container.walk_children()
        ):
            results.focus()
        elif (
            self.focused == results
            or (self.focused and self.focused in results.walk_children())
            or self.focused == year_input
        ):
            query_input.focus()
        else:
            self.app.pop_screen()

    def action_nav_right(self) -> None:
        """Right Arrow: move focus right from inputs to results, or from results to preview buttons."""
        query_input = self.query_one("#query", Input)
        year_input = self.query_one("#year", Input)
        results = self.query_one("#results", FuzzyList)
        open_btn = self.query_one("#preview-open-btn", Button)

        if self.focused in (query_input, year_input):
            results.focus()
        elif self.focused == results or (self.focused and self.focused in results.walk_children()):
            if self._highlighted_payload and open_btn.display:
                open_btn.focus()

    # ── Search orchestration ──────────────────────────────────────────────

    @on(Input.Submitted, "#query")
    @on(Input.Submitted, "#year")
    def _on_submit(self) -> None:
        self._start_search()

    def _start_search(self) -> None:
        query = self.query_one("#query", Input).value.strip()
        if not query:
            self.app.notify(t("type_something_warning"), severity="warning")
            return
        self._last_query = query
        year_spec = _parse_year_filter(self.query_one("#year", Input).value)
        self._set_loading(True)
        self._search_worker(query, year_spec)

    @work(thread=True, exclusive=True, group="search")
    def _search_worker(self, query: str, year_spec: tuple[int, int] | None) -> None:
        try:
            if self._site:
                items = bridge.search_titles(self._site, query)
                results = [(self._site, it) for it in items]
                errors: dict[str, str] = {}
            else:
                found, errors = bridge.search_global(query)
                results = [(site, it) for site, items in found.items() for it in items]
        except Exception as e:
            logger.exception("search failed")
            self.app.call_from_thread(self._search_failed, str(e))
            return
        self.app.call_from_thread(self._apply_results, results, errors, year_spec)

    def _set_loading(self, active: bool) -> None:
        self.query_one("#search-loading", LoadingIndicator).display = active

    def _search_failed(self, message: str) -> None:
        self._set_loading(False)
        self.query_one("#search-status", Static).update(f"[bold red]✖ {t('search_failed')}:[/] {message}")

    def _apply_results(
        self,
        results: list[tuple[str, object]],
        errors: dict[str, str],
        year_spec: tuple[int, int] | None,
        merge: bool = False,
    ) -> None:
        self._set_loading(False)
        if year_spec:
            lo, hi = year_spec
            results = [r for r in results if (y := _item_year(r[1])) is not None and lo <= y <= hi]

        if merge:
            # Flatten existing raw results and merge new ones
            existing_flat = []
            for _site, _it, provs in self._raw:
                for p_site, p_it in provs:
                    existing_flat.append((p_site, p_it))
            all_flat = existing_flat + results
            deduped = deduplicate_search_results(all_flat)
        else:
            deduped = deduplicate_search_results(results)

        self._failed_sites = list(errors.keys()) if errors else []
        retry_btn = self.query_one("#retry-failed-btn", Button)
        if self._failed_sites:
            retry_btn.label = t("retry_failed_sites_btn", count=len(self._failed_sites))
            retry_btn.display = True
        else:
            retry_btn.display = False

        self._raw = deduped
        self._update_category_counts(deduped)
        self._populate_results_list()

        status = t("results_found_status", count=len(deduped))
        if errors:
            failed_names = ", ".join(sorted(errors))
            status += t("sites_timed_out_status", count=len(errors), sites=failed_names)
        elif not deduped:
            status = t("no_results_status")

        self.query_one("#search-status", Static).update(status)
        self._store_snapshot()

    def _store_snapshot(self) -> None:
        """Remember the current result set, so leaving the screen does not discard it."""
        if not self._raw:
            return
        self.app.search_snapshot = SearchSnapshot(
            site=self._site,
            query=self._last_query,
            year=self.query_one("#year", Input).value,
            raw=list(self._raw),
            failed_sites=list(self._failed_sites),
            category=self._current_filter_category,
            sort_mode=self._current_sort_mode,
        )

    def _restore_snapshot(self) -> bool:
        """Repopulate from the last search of this session; False when there is none."""
        snapshot = getattr(self.app, "search_snapshot", None)
        if not isinstance(snapshot, SearchSnapshot) or snapshot.site != self._site or not snapshot.raw:
            return False

        self._raw = list(snapshot.raw)
        self._failed_sites = list(snapshot.failed_sites)
        self._last_query = snapshot.query
        self._current_filter_category = snapshot.category
        self._current_sort_mode = snapshot.sort_mode

        self.query_one("#query", Input).value = snapshot.query
        self.query_one("#year", Input).value = snapshot.year
        self.query_one("#sort-btn", Button).label = t("sort_by", mode=t(SORT_LABELS[self._current_sort_mode]))

        for btn_id, category in (
            ("#filter-all", "all"),
            ("#filter-film", "film"),
            ("#filter-serie", "serie"),
            ("#filter-music", "music"),
        ):
            btn = self.query_one(btn_id, Button)
            btn.variant = "primary" if category == self._current_filter_category else "default"

        retry_btn = self.query_one("#retry-failed-btn", Button)
        if self._failed_sites:
            retry_btn.label = t("retry_failed_sites_btn", count=len(self._failed_sites))
        retry_btn.display = bool(self._failed_sites)

        self._update_category_counts(self._raw)
        self._populate_results_list()
        self.query_one("#search-status", Static).update(
            t("restored_results_status", count=len(self._raw), query=snapshot.query)
        )
        return True

    def _update_category_counts(self, results: list[tuple[str, object, list[tuple[str, object]]]]) -> None:
        total = len(results)
        films = sum(1 for _, it, _ in results if getattr(it, "is_movie", False))
        series = sum(
            1 for _, it, _ in results if not getattr(it, "is_movie", False) and not getattr(it, "is_song", False)
        )
        music = sum(1 for _, it, _ in results if getattr(it, "is_song", False))

        self.query_one("#filter-all", Button).label = f"{t('all')} ({total})"
        self.query_one("#filter-film", Button).label = f"🎬 {t('film')} ({films})"
        self.query_one("#filter-serie", Button).label = f"📺 {t('serie_anime')} ({series})"
        self.query_one("#filter-music", Button).label = f"🎵 {t('music')} ({music})"

    def _populate_results_list(self) -> None:
        filtered = []
        for site, it, providers in self._raw:
            is_movie = getattr(it, "is_movie", False)
            is_song = getattr(it, "is_song", False)

            if self._current_filter_category == "film" and not is_movie:
                continue
            if self._current_filter_category == "serie" and (is_movie or is_song):
                continue
            if self._current_filter_category == "music" and not is_song:
                continue

            filtered.append((site, it, providers))

        sorted_filtered = _sort_results(filtered, self._current_sort_mode)
        global_mode = self._site is None
        items = []

        for site, it, providers in sorted_filtered:
            name = getattr(it, "name", "?")
            year = getattr(it, "year", "") or ""
            is_movie = getattr(it, "is_movie", False)
            is_song = getattr(it, "is_song", False)

            if is_movie:
                type_tag = "[bold yellow]🎬 FILM[/bold yellow]"
            elif is_song:
                type_tag = "[bold magenta]🎵 MUSIC[/bold magenta]"
            else:
                type_tag = "[bold green]📺 SERIE[/bold green]"

            provider_names = [p[0] for p in providers]
            if len(provider_names) > 1 or global_mode:
                site_tag = f" [bold cyan][{', '.join(provider_names)}][/bold cyan]"
            else:
                site_tag = ""

            year_str = f" [dim]({year})[/dim]" if year else ""
            item_key = f"{site}:{getattr(it, 'id', name)}"
            is_selected = item_key in self._selected_keys
            sel_badge = "[bold green]✓[/bold green] " if is_selected else ""

            lib_status = _get_library_status(it, site)
            if lib_status == "in_library":
                lib_badge = f" [bold green][{t('in_library_badge')}][/bold green]"
            elif lib_status == "in_queue":
                lib_badge = f" [bold yellow][{t('in_queue_badge')}][/bold yellow]"
            else:
                lib_badge = ""

            label = f"{sel_badge}{site_tag} {type_tag} [bold white]{name}[/bold white]{year_str}{lib_badge}"
            items.append(FuzzyItem(key=item_key, label=label, payload=(site, it, providers)))

        self.query_one("#results", FuzzyList).set_items(items, reset_filter=False)
        self._update_batch_bar()
        self._store_snapshot()

    def _update_batch_bar(self) -> None:
        bar = self.query_one("#batch-actions-bar", Horizontal)
        if self._selected_keys:
            bar.display = True
            count = len(self._selected_keys)
            self.query_one("#batch-count-label", Static).update(t("batch_selected_count", count=count))
            self.query_one("#btn-batch-enqueue", Button).label = t("batch_enqueue_btn", count=count)
        else:
            bar.display = False

    # ── Category Filter Pills handlers ────────────────────────────────────

    @on(Button.Pressed, ".filter-pill")
    def _on_filter_pill_pressed(self, event: Button.Pressed) -> None:
        for btn_id in ("#filter-all", "#filter-film", "#filter-serie", "#filter-music"):
            btn = self.query_one(btn_id, Button)
            btn.variant = "default"

        event.button.variant = "primary"
        if event.button.id == "filter-film":
            self._current_filter_category = "film"
        elif event.button.id == "filter-serie":
            self._current_filter_category = "serie"
        elif event.button.id == "filter-music":
            self._current_filter_category = "music"
        else:
            self._current_filter_category = "all"

        self._populate_results_list()

    # ── Sorting & Partial Retry Handlers ──────────────────────────────────

    @on(Button.Pressed, "#sort-btn")
    def _on_sort_pressed(self) -> None:
        self.action_cycle_sort()

    def action_cycle_sort(self) -> None:
        """Cycle through available sort modes and reorder list."""
        curr_idx = SORT_MODES.index(self._current_sort_mode) if self._current_sort_mode in SORT_MODES else 0
        next_idx = (curr_idx + 1) % len(SORT_MODES)
        self._current_sort_mode = SORT_MODES[next_idx]

        sort_btn = self.query_one("#sort-btn", Button)
        sort_btn.label = t("sort_by", mode=t(SORT_LABELS[self._current_sort_mode]))

        self._populate_results_list()
        self.app.notify(t("sort_by", mode=t(SORT_LABELS[self._current_sort_mode])), severity="information")

    @on(Button.Pressed, "#retry-failed-btn")
    def _on_retry_failed_pressed(self) -> None:
        self.action_retry_failed_sites()

    def action_retry_failed_sites(self) -> None:
        """Re-run search on failed / timed out sites."""
        if not self._failed_sites or not self._last_query:
            return
        to_retry = list(self._failed_sites)
        self.app.notify(t("retrying_sites_msg", sites=", ".join(to_retry)), severity="information")
        self._set_loading(True)
        self._retry_worker(self._last_query, to_retry)

    @work(thread=True, exclusive=True, group="search")
    def _retry_worker(self, query: str, sites: list[str]) -> None:
        try:
            found, errors = bridge.search_multi_sites(sites, query)
            results = [(site, it) for site, items in found.items() for it in items]
        except Exception as e:
            logger.exception("retry failed")
            self.app.call_from_thread(self._search_failed, str(e))
            return
        year_spec = _parse_year_filter(self.query_one("#year", Input).value)
        self.app.call_from_thread(self._apply_results, results, errors, year_spec, True)

    # ── Live Preview Card Rendering (Mouse Hover & Selection) ───────────────

    @on(FuzzyList.Highlighted, "#results")
    def _on_highlighted(self, event: FuzzyList.Highlighted) -> None:
        if not event.item or not event.item.payload:
            return
        payload = event.item.payload
        self._highlighted_payload = payload
        self._selected_provider_index = 0
        self._render_preview_card(payload)

    def _provider_pills(self) -> list[Button]:
        """Return the provider pill pool, in mount order (index matches the button ID suffix)."""
        wrap = self.query_one("#preview-providers-wrap", Grid)
        return [child for child in wrap.children if isinstance(child, Button)]

    def _ensure_provider_pills(self, count: int) -> list[Button]:
        """Grow the pill pool to at least `count` buttons and return it.

        Pills are mounted once and never removed: `remove_children()` is deferred by the
        message loop, so recreating them on every highlight raised DuplicateIds while the
        previous buttons were still registered.
        """
        pills = self._provider_pills()
        if len(pills) < count:
            new_pills = [
                Button("", id=f"btn-prov-{idx}", classes="preview-prov-pill") for idx in range(len(pills), count)
            ]
            self.query_one("#preview-providers-wrap", Grid).mount(*new_pills)
            pills.extend(new_pills)
        return pills

    def _render_preview_card(self, payload: tuple) -> None:
        self.query_one("#preview-actions-row", Grid).display = True
        site, item = payload[0], payload[1]
        providers = payload[2] if len(payload) > 2 else [(site, item)]

        if self._selected_provider_index >= len(providers):
            self._selected_provider_index = 0
        active_site, active_item = providers[self._selected_provider_index]

        name = getattr(active_item, "name", "") or getattr(active_item, "title", "?")
        year = getattr(active_item, "year", None)
        typ = getattr(active_item, "type", "Movie/Serie")
        desc = getattr(active_item, "desc", "") or getattr(active_item, "description", "") or ""
        desc = desc.strip()
        slug = getattr(active_item, "slug", "")

        is_movie = getattr(active_item, "is_movie", False)
        is_song = getattr(active_item, "is_song", False)

        open_btn = self.query_one("#preview-open-btn", Button)
        if is_movie:
            badge = f"[bold yellow]🎬 {t('film').upper()}[/bold yellow]"
            open_btn.label = f"⬇️ {t('download_movie')}"
        elif is_song:
            badge = f"[bold magenta]🎵 {t('music').upper()}[/bold magenta]"
            open_btn.label = f"⬇️ {t('download_track')}"
        else:
            badge = f"[bold green]📺 {t('serie_anime').upper()}[/bold green]"
            open_btn.label = f"📺 {t('select_seasons_episodes')}"

        prov_str = ", ".join(p[0] for p in providers)
        lib_status = _get_library_status(active_item, active_site)
        if lib_status == "in_library":
            status_line = f"\n[bold green]{t('in_library_badge')}[/bold green]"
        elif lib_status == "in_queue":
            status_line = f"\n[bold yellow]{t('in_queue_badge')}[/bold yellow]"
        else:
            status_line = ""

        lines = [
            f"{badge}  [bold white]{name}[/bold white]" + (f" [dim]({year})[/dim]" if year else "") + status_line,
            f"[dim]{t('label_providers')}[/] [bold cyan]{prov_str}[/bold cyan]   [dim]{t('label_format')}[/] [bold white]{typ}[/bold white]",
        ]

        if slug:
            lines.append(f"[dim]{t('label_slug')}[/] {slug}")

        lines.append("")
        lines.append(f"[bold cyan]{t('synopsis_plot')}:[/bold cyan]")
        if desc:
            lines.append(f"[italic]{desc[:240]}...[/italic]" if len(desc) > 240 else f"[italic]{desc}[/italic]")
        else:
            lines.append(f"[dim]{t('no_description')}[/dim]")

        preview = self.query_one("#search-preview-box", Static)
        preview.update("\n".join(lines))

        # Render provider switcher pills
        prov_box = self.query_one("#preview-providers-box", Vertical)
        if len(providers) > 1:
            prov_box.display = True
            for idx, pill in enumerate(self._ensure_provider_pills(len(providers))):
                if idx < len(providers):
                    pill.label = f"[{idx + 1}] {providers[idx][0]}"
                    pill.variant = "primary" if idx == self._selected_provider_index else "default"
                    pill.display = True
                else:
                    pill.display = False
        else:
            prov_box.display = False
            for pill in self._provider_pills():
                pill.display = False

    @on(Button.Pressed, ".preview-prov-pill")
    def _on_provider_pill_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id.startswith("btn-prov-"):
            try:
                idx = int(btn_id.split("-")[-1])
                self.action_select_preview_provider(idx)
            except ValueError:
                pass

    def action_select_preview_provider_0(self) -> None:
        self.action_select_preview_provider(0)

    def action_select_preview_provider_1(self) -> None:
        self.action_select_preview_provider(1)

    def action_select_preview_provider_2(self) -> None:
        self.action_select_preview_provider(2)

    def action_select_preview_provider_3(self) -> None:
        self.action_select_preview_provider(3)

    def action_select_preview_provider_4(self) -> None:
        self.action_select_preview_provider(4)

    def action_select_preview_provider(self, idx: int) -> None:
        """Select active provider index for highlighted item."""
        if isinstance(self.focused, Input):
            return
        if not self._highlighted_payload:
            return
        providers = self._highlighted_payload[2] if len(self._highlighted_payload) > 2 else []
        if 0 <= idx < len(providers):
            self._selected_provider_index = idx
            self._render_preview_card(self._highlighted_payload)
            self.app.notify(t("select_provider_hint", provider=providers[idx][0]), severity="information")

    # ── Interactive Actions on Highlighted Item ────────────────────────────

    def _get_active_target(self) -> tuple[str, object, list[tuple[str, object]]] | None:
        if not self._highlighted_payload:
            return None
        providers = (
            self._highlighted_payload[2]
            if len(self._highlighted_payload) > 2
            else [(self._highlighted_payload[0], self._highlighted_payload[1])]
        )
        idx = max(0, min(self._selected_provider_index, len(providers) - 1))
        active_site, active_item = providers[idx]
        return active_site, active_item, providers

    @on(Button.Pressed, "#preview-open-btn")
    def _on_preview_open(self) -> None:
        self.action_quick_download()

    def action_quick_download(self) -> None:
        """Download directly if single item (movie/music) or open detail screen for series."""
        if isinstance(self.focused, Input):
            return
        target = self._get_active_target()
        if not target:
            return
        site, item, providers = target
        is_movie = getattr(item, "is_movie", False)
        is_song = getattr(item, "is_song", False)

        if is_movie or is_song:
            self._start_direct_download(site, item)
        else:
            from VibraVid.tui.screens.detail import TitleDetailScreen

            self.app.push_screen(TitleDetailScreen(site, item, providers=providers))

    @on(Button.Pressed, "#preview-detail-btn")
    def _on_preview_detail(self) -> None:
        self.action_open_detail_forced()

    def action_open_detail_forced(self) -> None:
        """Always open TitleDetailScreen, even for single movie/song items."""
        if isinstance(self.focused, Input):
            return
        target = self._get_active_target()
        if not target:
            return
        site, item, providers = target
        from VibraVid.tui.screens.detail import TitleDetailScreen

        self.app.push_screen(TitleDetailScreen(site, item, providers=providers))

    @work(thread=True, exclusive=True, group="download")
    def _start_direct_download(self, site: str, item: object) -> None:
        context_tracker.is_gui = True
        try:
            success = bridge.start_download(site, item, season=None, episodes=None)
            if success:
                self.app.call_from_thread(
                    self.app.notify,
                    t("started_download_for", item=getattr(item, "name", "item")),
                    severity="information",
                )
            else:
                self.app.call_from_thread(self.app.notify, t("download_failed_to_start"), severity="error")
        except Exception as e:
            logger.exception("download error")
            self.app.call_from_thread(self.app.notify, t("download_error", error=str(e)), severity="error")
        finally:
            context_tracker.is_gui = False

    @on(Button.Pressed, "#preview-queue-btn")
    def _on_preview_queue(self) -> None:
        self.action_quick_enqueue()

    def action_quick_enqueue(self) -> None:
        """Enqueue highlighted item to background queue immediately."""
        if isinstance(self.focused, Input):
            return
        target = self._get_active_target()
        if not target:
            return
        site, item, _ = target
        search_term = str(getattr(item, "name", "") or getattr(item, "title", "") or "")
        builder = EquivalentCommandBuilder(excluded_dests=[])
        argv = builder.build_argv_from_params(site=site, search=search_term, item="1")

        if not argv:
            self.app.notify(t("could_not_build_cmd"), severity="error")
            return

        tag = _PROCESS_TAG
        path = _queue_path(tag)
        job_item = {
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
                data.setdefault("items", []).append(job_item)
                _save_queue(path, data)
            self.app.notify(
                t("added_to_queue_msg", title=search_term[:20], job_id=job_item["id"]),
                severity="information",
            )
            self._populate_results_list()
        except Exception as e:
            self.app.notify(t("queue_error", error=str(e)), severity="error")

    @on(Button.Pressed, "#preview-copy-btn")
    def _on_preview_copy(self) -> None:
        self.action_copy_cli_command()

    def action_copy_cli_command(self) -> None:
        """Build equivalent CLI command and copy to clipboard."""
        if isinstance(self.focused, Input):
            return
        target = self._get_active_target()
        if not target:
            return
        site, item, _ = target
        search_term = str(getattr(item, "name", "") or getattr(item, "title", "") or "")
        builder = EquivalentCommandBuilder(excluded_dests=[])
        argv = builder.build_argv_from_params(site=site, search=search_term, item="1")

        if not argv:
            self.app.notify(t("could_not_build_cmd"), severity="error")
            return

        cmd_str = f"python main.py {' '.join(argv)}"
        success, msg = copy_to_clipboard(cmd_str)
        if success:
            self.app.notify(f"{t('cli_command_copied')}\n{cmd_str}", severity="information")
        else:
            self.app.notify(msg, severity="warning")

    def action_focus_filter(self) -> None:
        """Jump focus to search result fuzzy filter input."""
        results = self.query_one("#results", FuzzyList)
        results.focus_input()

    # ── Multi-Selection & Batch Actions ───────────────────────────────────

    @on(FuzzyList.ToggleRequested, "#results")
    def _on_toggle_requested(self, event: FuzzyList.ToggleRequested) -> None:
        self._toggle_item_selection(event.item.key)

    def action_toggle_selection(self) -> None:
        """Toggle multi-selection on highlighted item."""
        if isinstance(self.focused, Input):
            return
        results = self.query_one("#results", FuzzyList)
        item = results.get_highlighted_item()
        if item:
            self._toggle_item_selection(item.key)

    def _toggle_item_selection(self, item_key: str) -> None:
        if item_key in self._selected_keys:
            self._selected_keys.remove(item_key)
        else:
            self._selected_keys.add(item_key)
        self._populate_results_list()

    @on(Button.Pressed, "#btn-batch-clear")
    def _on_batch_clear_pressed(self) -> None:
        self.action_clear_selection()

    def action_clear_selection(self) -> None:
        """Clear all selected items."""
        if isinstance(self.focused, Input):
            return
        self._selected_keys.clear()
        self._populate_results_list()

    @on(Button.Pressed, "#btn-batch-enqueue")
    def _on_batch_enqueue_pressed(self) -> None:
        self.action_batch_enqueue()

    def action_batch_enqueue(self) -> None:
        """Batch enqueue all selected items."""
        if isinstance(self.focused, Input):
            return
        if not self._selected_keys:
            self.app.notify(t("no_item_selected_to_enqueue"), severity="warning")
            return

        tag = _PROCESS_TAG
        path = _queue_path(tag)
        builder = EquivalentCommandBuilder(excluded_dests=[])
        enqueued_count = 0

        with _QueueLock(path):
            data = _load_queue(path)
            items_list = data.setdefault("items", [])

            for site, item, _providers in self._raw:
                item_key = f"{site}:{getattr(item, 'id', getattr(item, 'name', ''))}"
                if item_key in self._selected_keys:
                    search_term = str(getattr(item, "name", "") or getattr(item, "title", "") or "")
                    argv = builder.build_argv_from_params(site=site, search=search_term, item="1")
                    if argv:
                        job_item = {
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
                        items_list.append(job_item)
                        enqueued_count += 1

            _save_queue(path, data)

        self._selected_keys.clear()
        self._populate_results_list()
        self.app.notify(t("batch_enqueued_msg", count=enqueued_count), severity="information")

    @on(FuzzyList.Chosen, "#results")
    def _on_chosen(self, event: FuzzyList.Chosen) -> None:
        """When an item is clicked or selected: update preview & maintain focus on results list."""
        payload = event.item.payload
        self._highlighted_payload = payload
        self._selected_provider_index = 0
        self._render_preview_card(payload)
        self.query_one("#results", FuzzyList).action_focus_list()

    @on(FuzzyList.Activated, "#results")
    def _on_activated(self, event: FuzzyList.Activated) -> None:
        """When an item is double-clicked: trigger primary action."""
        payload = event.item.payload
        if not payload:
            return
        self._highlighted_payload = payload
        self._selected_provider_index = 0
        self.action_quick_download()
