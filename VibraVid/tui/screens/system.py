# 30.07.26
# by @ManoloZocco

"""System screen: external dependencies & binary versions, DRM status, log viewer, update check."""

import logging
import os
from pathlib import Path

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import Screen
from textual.widgets import (
    Button,
    DataTable,
    OptionList,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)

from VibraVid.cli.run import _probe_binary_version
from VibraVid.setup import resolve_cdm
from VibraVid.setup.system import (
    get_dovi_tool_path,
    get_ffmpeg_path,
    get_ffprobe_path,
    get_flux_path,
    get_mkvmerge_path,
    get_mkvpropedit_path,
    get_velora_path,
)
from VibraVid.tui.i18n import t
from VibraVid.tui.widgets.custom_footer import CustomFooter
from VibraVid.tui.widgets.topbar import TopBar
from VibraVid.utils import config_manager, get_log_file_path
from VibraVid.utils.upload.update import check_for_updates, download_release_asset, get_execution_mode, update_command
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
        self._update_info: dict | None = None
        self._update_downloading = False

    def compose(self) -> ComposeResult:
        yield TopBar(t("nav_system"), t("ctx_system"))
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
                    yield Button(t("download_update"), variant="success", id="btn-download-update", disabled=True)
                    yield Static("", id="update-status")
        yield CustomFooter([("tab", t("hint_field")), ("r", t("hint_refresh"))])

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
        self._run_update_check()

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
            ("mkvpropedit", get_mkvpropedit_path()),
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

        error = ""
        try:
            chosen = resolve_cdm(None)
        except (FileNotFoundError, ValueError) as exc:
            chosen = None
            error = f"[red]{exc}[/]"

        def status(path: str | None, remote: dict | None) -> str:
            if chosen is None:
                return error
            if remote:
                return f"[green]Remote[/] ({remote.get('device_name') or remote.get('host') or '?'})"
            return f"[green]OK[/] ({path})" if path and os.path.exists(path) else "[red]Missing[/]"

        wvd_status = status(chosen and chosen.wvd_path, chosen and chosen.widevine_remote)
        prd_status = status(chosen and chosen.prd_path, chosen and chosen.playready_remote)

        use_cdm = config_manager.config.get_bool("DRM", "use_cdm", default=True)
        vault_url = config_manager.config.get_dict("DRM", "vault", default={}).get("supa", {}).get("url", "")

        box.mount(Static(f"[bold cyan]Widevine (WVD):[/] {wvd_status}"))
        box.mount(Static(f"[bold cyan]PlayReady (PRD):[/] {prd_status}"))
        box.mount(Static(f"[bold cyan]Local CDM Enabled:[/] {use_cdm}"))
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
        if self._update_downloading:
            return
        status = self.query_one("#update-status", Static)
        status.update("[bold yellow]Checking GitHub for updates...[/]")
        self.query_one("#btn-download-update", Button).disabled = True
        self._run_update_check(force=True)

    def _display_update_result(self, result: dict) -> None:
        self._update_info = result
        if self._update_downloading:
            return
        status = self.query_one("#update-status", Static)
        button = self.query_one("#btn-download-update", Button)
        button.disabled = not (result["available"] and result["mode"] == "installer" and result["asset"])
        if result["error"]:
            status.update(f"[yellow]GitHub check unavailable: {result['error']}[/]")
        elif not result["available"]:
            status.update(f"[bold green]You are running the latest version ({result['current']}).[/]")
        elif result["mode"] != "installer":
            status.update(f"[bold yellow]New version {result['latest']} (current {result['current']}) · Update with: [white]{update_command(result['mode'])}[/white][/]")
        elif not result["asset"]:
            status.update(f"[yellow]New version {result['latest']}, but no compatible binary was found.[/]")
        else:
            status.update(f"[bold yellow]Update available: {result['current']} → {result['latest']} · Run with [white]-UP[/white] to auto-update, or download it here[/]")

    @work(thread=True, exclusive=True, group="release-check")
    def _run_update_check(self, force: bool = False) -> None:
        result = check_for_updates(force=force)
        try:
            self.app.call_from_thread(self._display_update_result, result)
        except (NoMatches, RuntimeError):
            logger.debug("Update check finished after the System screen was closed")

    @on(Button.Pressed, "#btn-download-update")
    def action_download_update(self) -> None:
        info = self._update_info
        if self._update_downloading or not info or not info["available"] or info["mode"] != "installer" or not info["asset"]:
            return
        self._update_downloading = True
        self.query_one("#btn-check-update", Button).disabled = True
        self.query_one("#btn-download-update", Button).disabled = True
        self.query_one("#update-status", Static).update("[cyan]Downloading release and verifying SHA-256...[/]")
        self._run_update_download(info["asset"])

    def _display_download_result(self, message: str) -> None:
        self._update_downloading = False
        if not self.is_attached:
            return
        
        self.query_one("#btn-check-update", Button).disabled = False
        self.query_one("#update-status", Static).update(message)
        info = self._update_info
        self.query_one("#btn-download-update", Button).disabled = not bool(
            info and info["available"] and info["mode"] == "installer" and info["asset"]
        )

    @work(thread=True, exclusive=True, group="release-download")
    def _run_update_download(self, asset: dict) -> None:
        try:
            downloaded = download_release_asset(asset)
            message = f"[green]Downloaded to {downloaded}. Current executable was not changed: replace it manually or use -UP.[/]"
        except Exception as e:
            logger.error(f"Error downloading update: {e}")
            message = f"[bold red]Update download failed: {e}[/]"
        try:
            self.app.call_from_thread(self._display_download_result, message)
        except RuntimeError:
            logger.debug("Update download finished after the System screen was closed")
