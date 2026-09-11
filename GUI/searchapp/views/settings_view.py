# 06.06.25

import importlib
import json
import logging
import os
import shutil
import sys
import zipfile

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from VibraVid.services._base.tv_display_manager import refresh_output_formats
from VibraVid.utils import config_manager

from .._download_infra import set_max_download_slots

logger = logging.getLogger(__name__)


@require_http_methods(["POST"])
def upload_service_zip(request: HttpRequest) -> JsonResponse:
    """Handle ZIP file upload to install a new service plugin."""
    uploaded = request.FILES.get("service_zip")
    if not uploaded:
        return JsonResponse({"success": False, "error": "Nessun file ZIP caricato."}, status=400)

    if not uploaded.name.lower().endswith(".zip"):
        return JsonResponse({"success": False, "error": "Il file deve essere un archivio .zip"}, status=400)

    # Determine services directory (VibraVid/services)
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))  # project root
    services_dir = os.path.join(base_dir, "VibraVid", "services")

    if not os.path.isdir(services_dir):
        return JsonResponse({"success": False, "error": f"Directory dei servizi non trovata: {services_dir}"}, status=500)

    import tempfile
    tmp_dir = tempfile.mkdtemp(prefix="vv_service_upload_")
    errors = []
    installed_services = []

    try:
        # Save uploaded ZIP to temp
        zip_path = os.path.join(tmp_dir, uploaded.name)
        with open(zip_path, "wb") as f:
            for chunk in uploaded.chunks():
                f.write(chunk)

        # Extract
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(tmp_dir)
        except zipfile.BadZipFile:
            return JsonResponse({"success": False, "error": "File ZIP non valido o corrotto."}, status=400)

        # Find service directories (folders with __init__.py)
        extracted_items = [d for d in os.listdir(tmp_dir) if os.path.isdir(os.path.join(tmp_dir, d))]

        service_folders = []
        for item in extracted_items:
            item_path = os.path.join(tmp_dir, item)
            init_file = os.path.join(item_path, "__init__.py")
            if os.path.isfile(init_file):
                service_folders.append(item)
            else:
                # Check one level deeper (ZIP might have a wrapper dir)
                for sub in os.listdir(item_path):
                    sub_path = os.path.join(item_path, sub)
                    if os.path.isdir(sub_path) and os.path.isfile(os.path.join(sub_path, "__init__.py")):
                        service_folders.append(os.path.join(item, sub))

        if not service_folders:
            return JsonResponse({
                "success": False,
                "error": "Nessun servizio valido trovato nello ZIP. Ogni servizio deve contenere __init__.py con 'indice' e '_useFor'."
            }, status=400)

        import ast as _ast

        for svc_rel in service_folders:
            svc_path = os.path.join(tmp_dir, svc_rel)
            svc_name = os.path.basename(svc_rel).lower()
            init_path = os.path.join(svc_path, "__init__.py")

            if svc_name.startswith("_") or svc_name in {"base", "_base"}:
                errors.append(f"'{svc_name}': reserved name, cannot be installed as a service")
                continue

            syntax_error = None
            for root, _, files in os.walk(svc_path):
                for fname in files:
                    if not fname.endswith(".py"):
                        continue
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, encoding="utf-8") as fh:
                            src = fh.read()
                        _ast.parse(src, filename=fname)
                    except SyntaxError as se:
                        rel = os.path.relpath(fpath, svc_path)
                        syntax_error = f"{rel}:{se.lineno}: {se.msg}"
                        break
                    except Exception as ex:
                        rel = os.path.relpath(fpath, svc_path)
                        syntax_error = f"{rel}: impossibile leggere ({ex})"
                        break
                if syntax_error:
                    break
            if syntax_error:
                errors.append(f"'{svc_name}': errore di sintassi nel plugin -> {syntax_error}")
                continue

            # Validate __init__.py has required declarations
            try:
                with open(init_path, encoding="utf-8") as f:
                    content = f.read()

                has_indice = False
                has_usefor = False
                for line in content.split("\n"):
                    stripped = line.strip()
                    if stripped.startswith("indice =") or stripped.startswith("indice="):
                        has_indice = True
                    if stripped.startswith("_useFor =") or stripped.startswith("_useFor="):
                        has_usefor = True

                if not has_indice:
                    errors.append(f"'{svc_name}': manca la dichiarazione 'indice' in __init__.py")
                    continue
                if not has_usefor:
                    errors.append(f"'{svc_name}': manca la dichiarazione '_useFor' in __init__.py")
                    continue

            except Exception as e:
                errors.append(f"'{svc_name}': errore nella lettura di __init__.py: {e}")
                continue

            # Check for conflicts
            dest_path = os.path.join(services_dir, svc_name)
            if os.path.exists(dest_path):
                shutil.rmtree(dest_path)

            # Copy service to services directory
            shutil.copytree(svc_path, dest_path)
            installed_services.append(svc_name)

        # Reload the service registries
        if installed_services:
            try:
                # Drop cached VibraVid.services.<name> modules for newly-installed services
                # so subsequent imports pick up the freshly-extracted files.
                for svc in installed_services:
                    prefix = f"VibraVid.services.{svc}"
                    for mod_name in [m for m in sys.modules if m == prefix or m.startswith(prefix + ".")]:
                        del sys.modules[mod_name]

                # Reload CLI service loader
                from VibraVid.services._base import site_loader
                importlib.reload(site_loader)
            except Exception as e:
                errors.append(f"Reload CLI services: {e}")

            try:
                # Drop any GUI API modules from sys.modules so previously-failed imports are retried fresh.
                for mod_name in [m for m in sys.modules if m.startswith("GUI.searchapp.api.") and not m.endswith(".base")]:
                    del sys.modules[mod_name]

                # Reload GUI API registry.
                from GUI.searchapp import api as gui_api_module
                gui_api_module._INITIALIZED = False
                gui_api_module._initialize_registry()
                gui_api_module.reset_site_categories_cache()
            except Exception as e:
                errors.append(f"Reload GUI API registry: {e}")

        # Report what the dropdown actually contains right now so the user can
        # verify their upload landed and which services failed to load.
        try:
            from GUI.searchapp import api as gui_api_module
            available_sites = sorted(gui_api_module.get_available_sites())
            load_errors_list = gui_api_module.get_load_errors()
        except Exception:
            available_sites = []
            load_errors_list = []

        result = {
            "success": len(installed_services) > 0,
            "installed": installed_services,
            "errors": errors,
            "available_sites_now": available_sites,
            "load_errors": load_errors_list,
            "message": f"Installati {len(installed_services)} servizi: {', '.join(installed_services)}" if installed_services else "Nessun servizio installato."
        }
        return JsonResponse(result, status=200 if installed_services else 400)

    except Exception as e:
        return JsonResponse({"success": False, "error": f"Errore durante l'installazione: {e}"}, status=500)

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@require_http_methods(["POST"])
def save_settings(request: HttpRequest) -> JsonResponse:
    try:
        data = json.loads(request.body.decode('utf-8'))
        file_type = data.get('file_type')  # 'config' or 'login'
        content = data.get('content', '').strip()

        if not file_type or not content:
            return JsonResponse({
                "success": False,
                "error": "Parametri mancanti"
            }, status=400)

        try:
            json.loads(content)
        except json.JSONDecodeError as e:
            return JsonResponse({
                "success": False,
                "error": f"JSON non valido: {str(e)}"
            }, status=400)

        conf_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "Conf")
        if file_type == 'config':
            file_path = os.path.join(conf_dir, "config.json")
        elif file_type == 'login':
            file_path = os.path.join(conf_dir, "login.json")
        else:
            return JsonResponse({
                "success": False,
                "error": "Tipo di file non valido"
            }, status=400)

        backup_path = file_path + ".backup"
        if os.path.exists(file_path):
            try:
                shutil.copy2(file_path, backup_path)
            except OSError as e:
                logger.exception("Backup failed: %s", e)

        with open(file_path, 'w', encoding='utf-8') as f:
            formatted = json.dumps(json.loads(content), indent=4, ensure_ascii=False)
            f.write(formatted)

        # Apply the download concurrency limit and refresh the in-memory config/login cache immediately
        if file_type == 'config':
            try:
                new_cfg = json.loads(content)
                slots = int(new_cfg.get("ARR", {}).get("max_concurrent_downloads", 1) or 1)
                set_max_download_slots(slots)
            except Exception as exc:
                logger.exception("Failed to apply max concurrent slots: %s", exc)

            try:
                config_manager.reload_config_only()
                refresh_output_formats()
            except Exception as exc:
                logger.exception("Failed to reload config cache after save: %s", exc)

        elif file_type == 'login':
            try:
                config_manager.reload_login_only()
            except Exception as exc:
                logger.exception("Failed to reload login cache after save: %s", exc)

        return JsonResponse({
            "success": True,
            "message": f"{file_type}.json salvato con successo"
        })

    except Exception as e:
        return JsonResponse({
            "success": False,
            "error": f"Errore nel salvataggio: {str(e)}"
        }, status=500)


__all__ = ['upload_service_zip', 'save_settings']
