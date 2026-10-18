# 30.07.26
# by @ManoloZocco

"""Settings screen: section navigation, interactive config form and login editor."""

import json
import logging
from typing import Any

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Header, Input, ListItem, ListView, Static, Switch

from VibraVid.tui.i18n import t
from VibraVid.tui.widgets.custom_footer import CustomFooter
from VibraVid.utils import config_manager

logger = logging.getLogger(__name__)

def _cdm_json(target, raw: str):
    """A ``cdm`` typed as a JSON list or table (a list of files / quality rules) is saved as that, not as text."""
    key = target[1] if isinstance(target, tuple) else target
    if key == "cdm" and raw[:1] in ("[", "{"):
        try:
            return json.loads(raw)
        except ValueError:
            pass
    return raw


SECTIONS: list[tuple[str, str]] = [
    ("DEFAULT", "sec_default"),
    ("OUTPUT", "sec_output"),
    ("DOWNLOAD", "sec_download"),
    ("PROCESS", "sec_process"),
    ("REQUESTS", "sec_requests"),
    ("DRM", "sec_drm"),
    ("ARR", "sec_arr"),
    ("LOGIN", "sec_login"),
]

ARR_BANNER_TEXT = (
    "ℹ Note: ARR features (Sonarr, Radarr, Seerr automation) require the VibraVid Django web GUI active."
)


class SettingsScreen(Screen):
    """Screen for viewing and editing config.toml and login.toml settings."""

    BINDINGS = [
        ("ctrl+s", "save_current_section", "Save Section"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._current_section = "DEFAULT"
        self._field_map: dict[str, tuple[str, Any, type]] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="settings-sidebar"):
                yield Static(t("settings_sections"), classes="panel-title")
                yield ListView(id="settings-sections")
            with Vertical(id="settings-content"):
                yield Static(f"DEFAULT — {t('sec_default')}", id="section-title", classes="panel-title")
                yield VerticalScroll(id="settings-form")
                with Horizontal(id="settings-actions"):
                    yield Button(t("save_section"), variant="primary", id="btn-save")
                    yield Button(t("reload_config"), variant="default", id="btn-reload")
                    yield Button(t("system_info"), variant="warning", id="btn-goto-system")
                    yield Static("", id="settings-status")
        yield CustomFooter()

    def on_mount(self) -> None:
        sections_list = self.query_one("#settings-sections", ListView)
        for sec_id, _ in SECTIONS:
            sections_list.append(
                ListItem(Static(sec_id, classes="category-label"), id=f"sec-{sec_id}")
            )
        sections_list.index = 0
        self._load_section("DEFAULT")

    def _load_section(self, section: str) -> None:
        self._current_section = section
        sec_key = dict(SECTIONS).get(section, section)
        title_label = t(sec_key)
        self.query_one("#section-title", Static).update(f"{section} — {title_label}")

        form = self.query_one("#settings-form", VerticalScroll)
        form.remove_children()
        self._field_map.clear()

        if section == "ARR":
            form.mount(Static(ARR_BANNER_TEXT, classes="settings-banner"))

        if section == "LOGIN":
            self._build_login_form(form)
        else:
            self._build_config_form(section, form)

    def _build_config_form(self, section: str, form: VerticalScroll) -> None:
        config_data = config_manager._config_data.get(section, {})
        if not config_data:
            form.mount(Static(f"No options found for section {section}.", classes="field-label"))
            return

        for key, val in config_data.items():
            field_id = f"cfg-{section}-{key}".replace(".", "_").replace(" ", "_")
            orig_type = type(val)
            self._field_map[field_id] = (section, key, orig_type)

            if isinstance(val, bool):
                row = Horizontal(
                    Static(f"{key}:", classes="field-label"),
                    Switch(value=val, id=field_id),
                    classes="settings-field"
                )
            elif isinstance(val, (list, dict)):
                val_str = json.dumps(val)
                row = Horizontal(
                    Static(f"{key} (JSON):", classes="field-label"),
                    Input(value=val_str, id=field_id),
                    classes="settings-field"
                )
            else:
                row = Horizontal(
                    Static(f"{key}:", classes="field-label"),
                    Input(value="" if val is None else str(val), id=field_id),
                    classes="settings-field"
                )
            form.mount(row)

    def _build_login_form(self, form: VerticalScroll) -> None:
        login_data = config_manager._login_data
        if not login_data:
            form.mount(Static("No login configuration found.", classes="field-label"))
            return

        for service, fields in login_data.items():
            form.mount(Static(f"Provider / Service: {service}", classes="sub-title"))
            if isinstance(fields, dict):
                for key, val in fields.items():
                    field_id = f"login-{service}-{key}".replace(".", "_").replace(" ", "_")
                    orig_type = type(val)
                    self._field_map[field_id] = ("LOGIN", (service, key), orig_type)
                    is_password = key.lower() in ("password", "token", "adminbetoken", "etp_rt", "st")
                    if isinstance(val, (list, dict)):
                        row = Horizontal(
                            Static(f"{key} (JSON):", classes="field-label"),
                            Input(value=json.dumps(val), id=field_id),
                            classes="settings-field"
                        )
                    else:
                        row = Horizontal(
                            Static(f"{key}:", classes="field-label"),
                            Input(value="" if val is None else str(val), password=is_password, id=field_id),
                            classes="settings-field"
                        )
                    form.mount(row)
            else:
                field_id = f"login-{service}".replace(".", "_").replace(" ", "_")
                orig_type = type(fields)
                self._field_map[field_id] = ("LOGIN", service, orig_type)
                if isinstance(fields, (list, dict)):
                    row = Horizontal(
                        Static(f"{service} (JSON):", classes="field-label"),
                        Input(value=json.dumps(fields), id=field_id),
                        classes="settings-field"
                    )
                else:
                    row = Horizontal(
                        Static(f"{service}:", classes="field-label"),
                        Input(value="" if fields is None else str(fields), id=field_id),
                        classes="settings-field"
                    )
                form.mount(row)

    @on(ListView.Highlighted, "#settings-sections")
    def _on_section_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is None or event.item.id is None or not event.item.id.startswith("sec-"):
            return
        sec = event.item.id[len("sec-"):]
        self._load_section(sec)

    @on(Button.Pressed, "#btn-save")
    def action_save_current_section(self) -> None:
        status = self.query_one("#settings-status", Static)
        try:
            if self._current_section == "LOGIN":
                for field_id, (_, target, orig_type) in self._field_map.items():
                    try:
                        inp = self.query_one(f"#{field_id}", Input)
                        raw_val = inp.value.strip()
                        if orig_type in (list, dict):
                            try:
                                val = json.loads(raw_val)
                            except Exception:
                                logger.warning(f"Field {field_id}: invalid JSON, keeping previous value")
                                continue
                        else:
                            val = _cdm_json(target, raw_val)
                        if isinstance(target, tuple):
                            service, key = target
                            config_manager._login_data.setdefault(service, {})[key] = val
                        else:
                            config_manager._login_data[target] = val
                    except Exception as ex:
                        logger.debug(f"Skipping unmounted field {field_id}: {ex}")
                config_manager.save_login()
                status.update("[bold green]Login credentials saved to login.toml[/]")
                self.notify("login.toml saved successfully!", severity="information")
            else:
                section_data = config_manager._config_data.setdefault(self._current_section, {})
                for field_id, (sec, key, orig_type) in self._field_map.items():
                    if sec != self._current_section:
                        continue
                    if orig_type is bool:
                        sw = self.query_one(f"#{field_id}", Switch)
                        val = sw.value
                    else:
                        inp = self.query_one(f"#{field_id}", Input)
                        raw_val = inp.value.strip()
                        if orig_type is int:
                            val = int(raw_val) if raw_val else 0
                        elif orig_type is float:
                            val = float(raw_val) if raw_val else 0.0
                        elif orig_type in (list, dict):
                            try:
                                val = json.loads(raw_val)
                            except Exception:
                                if orig_type is list:
                                    val = [x.strip() for x in raw_val.split(",") if x.strip()]
                                else:
                                    val = raw_val
                        else:
                            val = _cdm_json(key, raw_val)

                    section_data[key] = val
                    config_manager.config.set_key(sec, key, val)

                config_manager.save_config()
                status.update(f"[bold green]Section [{self._current_section}] saved to config.toml[/]")
                self.notify(f"Section [{self._current_section}] saved!", severity="information")
        except Exception as e:
            logger.error(f"Error saving settings: {e}")
            status.update(f"[bold red]Save error: {e}[/]")
            self.notify(f"Failed to save settings: {e}", severity="error")

    @on(Button.Pressed, "#btn-reload")
    def action_reload(self) -> None:
        status = self.query_one("#settings-status", Static)
        try:
            config_manager.reload()
            self._load_section(self._current_section)
            status.update("[bold green]Configuration reloaded from disk[/]")
            self.notify("Configuration reloaded", severity="information")
        except Exception as e:
            status.update(f"[bold red]Reload error: {e}[/]")

    @on(Button.Pressed, "#btn-goto-system")
    def action_goto_system(self) -> None:
        from VibraVid.tui.screens.system import SystemScreen
        self.app.push_screen(SystemScreen())
