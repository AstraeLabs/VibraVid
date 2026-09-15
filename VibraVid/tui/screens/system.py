# 30.07.26
# by @ManoloZocco

"""System screen: external dependencies & binary versions, DRM status, log viewer, update check."""

import logging
import os
from pathlib import Path

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    Button,
    DataTable,
    Header,
    OptionList,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)

from VibraVid.cli.run import _probe_binary_version
from VibraVid.setup.system import (
    get_dovi_tool_path,
    get_ffmpeg_path,
    get_ffprobe_path,
    get_flux_path,
    get_mkvmerge_path,
    get_prd_path,
    get_velora_path,
    get_wvd_path,
)
from VibraVid.tui.i18n import t
from VibraVid.tui.widgets.custom_footer import CustomFooter
from VibraVid.utils import config_manager, get_log_file_path
from VibraVid.utils.upload.update import fetch_github_releases, get_execution_mode
from VibraVid.utils.upload.version import __title__, __version__

logger = logging.getLogger(__name__)


class SystemScreen(Screen):
    """Screen displaying system diagnostics, DRM status, log files, and updates."""

    BINDINGS = [
        ("r", "refresh_all", "Refresh"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._log_files: list[Path] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent():
            with TabPane(t("tab_deps"), id="tab-deps"):
                yield Static(t("external_dependencies"), classes="panel-title")
                yield DataTable(id="deps-table")
                yield Static(t("drm_status"), classes="panel-title")
                yield Vertical(id="drm-box")

            with TabPane(t("log_viewer"), id="tab-logs"):
                with Horizontal(id="log-control-bar"):
                    yield Static(t("select_log_file"), classes="field-label")
                    yield Button(t("refresh_log_list"), variant="default", id="btn-refresh-logs")
                with Horizontal(id="log-layout"):
                    yield OptionList(id="log-file-list")
                    yield RichLog(id="log-viewer", highlight=True, markup=False)

            with TabPane(t("updates_info"), id="tab-updates"):
                with VerticalScroll(id="update-box"):
                    yield Static(t("system_app_info"), classes="panel-title")
                    yield Static(id="system-info-text")
                    yield Button(t("check_for_updates"), variant="primary", id="btn-check-update")
                    yield Static("", id="update-status")
        yield CustomFooter()

    def on_mount(self) -> None:
        table = self.query_one("#deps-table", DataTable)
        table.add_columns(
            t("col_binary"),
            t("col_status"),
            t("col_version"),
            t("col_path"),
        )
        self._load_dependencies()
        self._load_drm_status()
        self._load_log_files()
        self._load_system_info()

    def action_refresh_all(self) -> None:
        self._load_dependencies()
        self._load_drm_status()
        self._load_log_files()
        self._load_system_info()
        self.notify("System diagnostics refreshed", severity="information")

    def _load_dependencies(self) -> None:
        table = self.query_one("#deps-table", DataTable)
        table.clear()

        deps: list[tuple[str, str]] = [
            ("FFmpeg", get_ffmpeg_path()),
            ("FFprobe", get_ffprobe_path()),
            ("flux", get_flux_path()),
            ("dovi_tool", get_dovi_tool_path()),
            ("mkvmerge", get_mkvmerge_path()),
            ("Velora", get_velora_path()),
        ]

        for dep_name, dep_path in deps:
            is_ok = bool(dep_path and os.path.exists(dep_path))
            status_str = "[green]OK[/]" if is_ok else "[red]Missing[/]"
            version = _probe_binary_version(dep_name, dep_path) if is_ok else ""
            version_str = f"v{version}" if version else "-"
            path_str = dep_path if is_ok else "Not found"
            table.add_row(dep_name, status_str, version_str, path_str)

    def _load_drm_status(self) -> None:
        box = self.query_one("#drm-box", Vertical)
        box.remove_children()

        wvd = get_wvd_path()
        prd = get_prd_path()

        wvd_ok = bool(wvd and os.path.exists(wvd))
        prd_ok = bool(prd and os.path.exists(prd))

        wvd_status = f"[green]OK[/] ({wvd})" if wvd_ok else "[red]Missing[/]"
        prd_status = f"[green]OK[/] ({prd})" if prd_ok else "[red]Missing[/]"

        use_cdm = config_manager.config.get_bool("DRM", "use_cdm", default=True)
        prefer_remote = config_manager.config.get_bool("DRM", "prefer_remote_cdm", default=False)
        vault_url = config_manager.config.get_dict("DRM", "vault", default={}).get("supa", {}).get("url", "")

        box.mount(Static(f"[bold cyan]Widevine (WVD):[/] {wvd_status}"))
        box.mount(Static(f"[bold cyan]PlayReady (PRD):[/] {prd_status}"))
        box.mount(Static(f"[bold cyan]Local CDM Enabled:[/] {use_cdm}"))
        box.mount(Static(f"[bold cyan]Prefer Remote CDM:[/] {prefer_remote}"))
        box.mount(Static(f"[bold cyan]DRM Vault URL:[/] {vault_url or 'None'}"))

    def _load_log_files(self) -> None:
        option_list = self.query_one("#log-file-list", OptionList)
        option_list.clear_options()

        base_dir = Path(config_manager.base_path)
        log_dir = base_dir / ".cache" / "logs"

        self._log_files = []
        if log_dir.exists():
            files = sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
            self._log_files = files

        curr_log = get_log_file_path()
        if curr_log and Path(curr_log).exists() and Path(curr_log) not in self._log_files:
            self._log_files.insert(0, Path(curr_log))

        if not self._log_files:
            option_list.add_option("No log files found")
            return

        for p in self._log_files:
            option_list.add_option(p.name)

        # Highlight newest log file automatically
        if self._log_files:
            option_list.highlighted = 0
            self._display_log(self._log_files[0])

    def _display_log(self, path: Path) -> None:
        viewer = self.query_one("#log-viewer", RichLog)
        viewer.clear()
        if not path.exists():
            viewer.write(f"Log file not found: {path}")
            return

        try:
            viewer.write(f"=== {path.name} ({path.stat().st_size} bytes) ===\n")
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            # Truncate to last 1000 lines for smooth display
            max_lines = 1000
            if len(lines) > max_lines:
                viewer.write(f"[Showing last {max_lines} lines out of {len(lines)}]\n")
                lines = lines[-max_lines:]
            for line in lines:
                viewer.write(line)
        except Exception as e:
            viewer.write(f"Error reading log file: {e}")

    @on(OptionList.OptionHighlighted, "#log-file-list")
    def _on_log_file_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if 0 <= event.option_index < len(self._log_files):
            self._display_log(self._log_files[event.option_index])

    @on(Button.Pressed, "#btn-refresh-logs")
    def _on_refresh_logs(self) -> None:
        self._load_log_files()
        self.notify("Log list refreshed", severity="information")

    def _load_system_info(self) -> None:
        info_widget = self.query_one("#system-info-text", Static)
        exec_mode = get_execution_mode()
        lines = [
            f"[bold cyan]Application:[/] {__title__}",
            f"[bold cyan]Version:[/] v{__version__}",
            f"[bold cyan]Execution Mode:[/] {exec_mode}",
            f"[bold cyan]Base Path:[/] {config_manager.base_path}",
            f"[bold cyan]Config Path:[/] {config_manager.config_file_path}",
            f"[bold cyan]Login Path:[/] {config_manager.login_file_path}",
            f"[bold cyan]Active Log File:[/] {get_log_file_path() or 'None'}",
        ]
        info_widget.update("\n".join(lines))

    @on(Button.Pressed, "#btn-check-update")
    def action_check_update(self) -> None:
        status = self.query_one("#update-status", Static)
        status.update("[bold yellow]Checking GitHub for updates...[/]")
        self._run_update_check()

    @work(exclusive=True)
    async def _run_update_check(self) -> None:
        status = self.query_one("#update-status", Static)
        try:
            releases = fetch_github_releases()
            if not releases or not isinstance(releases, list):
                status.update("[bold red]Could not fetch release information from GitHub.[/]")
                return

            latest = releases[0]
            tag_name = latest.get("tag_name") or latest.get("name", "Unknown")
            clean_latest = tag_name.lstrip("v")
            clean_current = __version__.lstrip("v")

            if clean_current == clean_latest:
                status.update(f"[bold green]✓ You are running the latest version (v{__version__}).[/]")
                self.notify("VibraVid is up to date!", severity="information")
            else:
                msg = f"[bold yellow]⚡ Update available! Latest: {tag_name} (Current: v{__version__})[/]"
                status.update(msg)
                self.notify(f"New update available: {tag_name}", severity="warning")
        except Exception as e:
            logger.error(f"Error checking updates: {e}")
            status.update(f"[bold red]Update check failed: {e}[/]")
