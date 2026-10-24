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
from textual.widgets import Button, Input, ListItem, ListView, Static, Switch

from VibraVid.tui.i18n import t
from VibraVid.tui.widgets.custom_footer import CustomFooter
from VibraVid.tui.widgets.topbar import TopBar
from VibraVid.utils import config_manager

logger = logging.getLogger(__name__)

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
LOGIN = "LOGIN"
SERVICE_PREFIX = "svc:"
OPTION_KEYS = {"cdm", "args", "device_type", "country", "process"}
HIDDEN_LOGIN_KEYS = {"process"}
PLACEHOLDER = "[paste here]"
SECRET_KEYS = ("password", "token", "adminbetoken", "etp_rt", "st")
FIELD_NOTES = {"cdm": "note_cdm", "args": "note_args", "cookies": "note_cookies", "tmdb": "note_tmdb", "tvdb": "note_tvdb"}


def service_is_set_up(fields) -> bool:
    """Whether a [service] table of login.toml holds a credential (not just a cdm, args or the shipped placeholder)."""
    if not isinstance(fields, dict):
        return bool(fields)
    for key, value in fields.items():
        if key in OPTION_KEYS:
            continue
        if isinstance(value, str):
            if value.strip() and PLACEHOLDER not in value:
                return True
        elif value:
            return True
    return False


def _dom_id(prefix: str, name: str) -> str:
    return prefix + "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in name)


def _cdm_json(target, raw: str):
    """A ``cdm`` typed as a JSON list or table (a list of files / quality rules) is saved as that, not as text."""
    key = target[1] if isinstance(target, tuple) else target
    if key == "cdm" and raw[:1] in ("[", "{"):
        try:
            return json.loads(raw)
        except ValueError:
            pass
    return raw


class SettingsScreen(Screen):
    """Screen for viewing and editing config.toml and login.toml settings."""

    BINDINGS = [
        ("ctrl+s", "save_current_section", "Save Section"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._current_section = "DEFAULT"
        self._field_map: dict[str, tuple[str, Any, type]] = {}
        self._service_items: dict[str, str] = {}  # dom id of the sidebar entry -> service name
        self._service_labels: dict[str, Static] = {}  # service name -> its sidebar label

    def compose(self) -> ComposeResult:
        yield TopBar(t("nav_settings"), t("ctx_settings"))
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
        yield CustomFooter([("↑↓", t("hint_section")), ("tab", t("hint_field")), ("ctrl+s", t("hint_save"))])

    def on_mount(self) -> None:
        sections_list = self.query_one("#settings-sections", ListView)
        sections_list.append(ListItem(Static(t("settings_config_heading"), classes="side-heading"), disabled=True))
        for sec_id, _ in SECTIONS:
            if sec_id != LOGIN:
                sections_list.append(ListItem(Static(sec_id, classes="category-label"), id=f"sec-{sec_id}"))

        sections_list.append(ListItem(Static(t("settings_services"), classes="side-heading"), disabled=True))
        for service, fields in config_manager._login_data.items():
            item_id = _dom_id("svc-", service)
            label = Static(self._service_label(service, fields), classes="category-label")
            self._service_items[item_id] = service
            self._service_labels[service] = label
            sections_list.append(ListItem(label, id=item_id))

        sections_list.index = 1
        self._load_section("DEFAULT")

    @staticmethod
    def _service_label(service: str, fields) -> str:
        return f"[#86efac]✓[/] {service}" if service_is_set_up(fields) else f"[#71717a]·[/] {service}"

    def _refresh_service_labels(self) -> None:
        for service, label in self._service_labels.items():
            label.update(self._service_label(service, config_manager._login_data.get(service)))

    def _load_service(self, service: str) -> None:
        """One service of login.toml on its own page, with its state in the title."""
        self._current_section = LOGIN
        fields = config_manager._login_data.get(service, {})
        state = t("service_ready") if service_is_set_up(fields) else t("service_empty")
        self.query_one("#section-title", Static).update(f"{service}  [#71717a]·  {state}[/]")

        form = self.query_one("#settings-form", VerticalScroll)
        form.remove_children()
        self._field_map.clear()
        self._mount_login_fields(form, service, fields)

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

        if section == LOGIN:
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
            self._mount_login_fields(form, service, fields)

    def _mount_login_fields(self, form: VerticalScroll, service: str, fields) -> None:
        """The inputs of one [service] table (or of one top-level value), each with the note saying what it is for."""
        if isinstance(fields, dict):
            for key, val in fields.items():
                if key in HIDDEN_LOGIN_KEYS:
                    continue
                
                field_id = f"login-{service}-{key}".replace(".", "_").replace(" ", "_")
                orig_type = type(val)
                self._field_map[field_id] = (LOGIN, (service, key), orig_type)
                is_password = key.lower() in SECRET_KEYS
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
                note_key = FIELD_NOTES.get(key.lower()) or ("note_secret" if is_password else None)
                if note_key:
                    row.add_class("-noted")
                    form.mount(Static(t(note_key), classes="field-note"))
        else:
            field_id = f"login-{service}".replace(".", "_").replace(" ", "_")
            orig_type = type(fields)
            self._field_map[field_id] = (LOGIN, service, orig_type)
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
        item_id = event.item.id if event.item is not None else None
        if item_id is None:
            return
        if item_id in self._service_items:
            self._load_service(self._service_items[item_id])
        elif item_id.startswith("sec-"):
            self._load_section(item_id[len("sec-"):])

    @on(Button.Pressed, "#btn-save")
    def action_save_current_section(self) -> None:
        status = self.query_one("#settings-status", Static)
        try:
            if self._current_section == LOGIN:
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
                self._refresh_service_labels()
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
