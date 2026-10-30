# 01.10.25

import contextvars
import glob
import importlib
import importlib.machinery
import importlib.util
import logging
import os
import re
import sys

from rich.console import Console

from VibraVid.setup import get_is_binary_installation
from VibraVid.utils import config_manager, disk_cache
from VibraVid.utils.os import os_manager

from . import git_source

console = Console()
logger = logging.getLogger(__name__)
folder_name = "services"
imp_sources = config_manager.config.get_list("DEFAULT", "imp_service")
_HIDDEN_SITES_ENV_VARS = ("VIBRAVID_HIDDEN_SITES", "VIBRAVID_DISABLED_SITES")
_warned_unknown_hidden: set[str] = set()
KNOWN_CONTENT_TYPES = ("Anime", "Film_Serie", "Live", "Serie", "Song", "Tor")
current_site_var: "contextvars.ContextVar[str | None]" = contextvars.ContextVar("vibravid_current_site", default=None)
_BINARY_MISMATCH_MARKERS = (
    "dll load failed",
    "%1 is not a valid win32 application",
    "invalid elf header",
    "wrong elf class",
    "mach-o",
    "wrong architecture",
    "incompatible architecture",
    "magic number is wrong",
)


def _reraise_if_binary_mismatch(module_name: str, submodule: str, exc: Exception) -> None:
    """Re-raise as a clearer error when the failure looks like a compiled-extension (.pyd/.so) mismatch."""
    if any(marker in str(exc).lower() for marker in _BINARY_MISMATCH_MARKERS):
        raise ImportError(
            f"Compiled submodule '{submodule}' of service '{module_name}' is not compatible with this "
            f"platform/Python version (looks like a .pyd/.so build made for a different OS, architecture, "
            f"or Python ABI). A build matching this system is required. Original error: {exc}"
        ) from exc


def hidden_services() -> set[str]:
    """Return the set of service names that are configured to be hidden."""
    try:
        configured = config_manager.config.get_list("DEFAULT", "hidden_services", default=[])
    except Exception:
        configured = []
    if isinstance(configured, str):
        configured = configured.split(",")

    names = {str(name).strip().lower() for name in configured or []}
    for var in _HIDDEN_SITES_ENV_VARS:
        names.update(name.strip().lower() for name in os.environ.get(var, "").split(","))
    names.discard("")
    return names


def site_codes(names) -> dict[str, str]:
    """Map each service name (lower-case) to a short, unique code of at most 4 characters, usable wherever a site name is accepted (``-i``)."""
    names = sorted({str(name).strip().lower() for name in names if name}, key=lambda n: (len(re.sub(r"[^a-z0-9]", "", n)), n))
    clean = {n: re.sub(r"[^a-z0-9]", "", n) for n in names}

    def candidates(name: str) -> list[str]:
        letters = clean[name]
        words = [w for w in re.split(r"[^a-z0-9]+", name) if w]
        options = [letters[:4]]
        if len(letters) > 4:
            if len(words) > 1:
                options.append(words[0][:2] + words[1][:2])
            options += [letters[:2] + letters[-2:], letters[:1] + letters[-3:]]
            options += [letters[:3] + extra for extra in letters[3:] + "123456789"]
        return list(dict.fromkeys(options))

    options = {n: candidates(n) for n in names}
    prefix_owners: dict[str, int] = {}
    for opts in options.values():
        prefix_owners[opts[0]] = prefix_owners.get(opts[0], 0) + 1

    codes = {n: opts[0] for n, opts in options.items() if prefix_owners[opts[0]] == 1}
    for name in names:
        if name in codes:
            continue
        used = set(codes.values())
        codes[name] = next((c for c in options[name] if c not in used and (c == name or c not in names)), options[name][0])

    return codes


class CompiledService:
    def __init__(self, module_name: str, service_dir: str):
        self.module_name = module_name
        self.service_dir = service_dir

    def is_candidate(self) -> bool:
        """Folder without __init__.py that contains at least one variant sub-folder."""
        if os.path.isfile(os.path.join(self.service_dir, "__init__.py")):
            return False
        return any(os.path.isdir(os.path.join(self.service_dir, entry)) for entry in os.listdir(self.service_dir))

    def find_entry(self) -> str | None:
        """Path of the compiled package module matching the running Python/OS, or None."""
        for variant in sorted(os.listdir(self.service_dir)):
            variant_dir = os.path.join(self.service_dir, variant)
            if not os.path.isdir(variant_dir):
                continue
            for suffix in importlib.machinery.EXTENSION_SUFFIXES:
                entry = os.path.join(variant_dir, self.module_name + suffix)
                if os.path.isfile(entry):
                    return entry
        return None

    def variants(self) -> list[str]:
        return sorted(e for e in os.listdir(self.service_dir) if os.path.isdir(os.path.join(self.service_dir, e)))

    def load(self, qualified_name: str):
        """Import the compiled package as ``qualified_name`` (reuses it if already imported)."""
        if qualified_name in sys.modules:
            return sys.modules[qualified_name]

        entry = self.find_entry()
        if entry is None:
            raise ImportError(
                f"No compiled build of service '{self.module_name}' matches this system "
                f"(Python {sys.version_info.major}.{sys.version_info.minor}, expected suffix one of "
                f"{', '.join(importlib.machinery.EXTENSION_SUFFIXES)}); available variants: {', '.join(self.variants())}"
            )

        # The package's submodules (downloader, scrapper, ...) are sibling extensions in the same variant folder
        spec = importlib.util.spec_from_file_location(
            qualified_name,
            entry,
            loader=importlib.machinery.ExtensionFileLoader(qualified_name, entry),
            submodule_search_locations=[os.path.dirname(entry)],
        )
        
        module = importlib.util.module_from_spec(spec)
        sys.modules[qualified_name] = module
        token = current_site_var.set(self.module_name)
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(qualified_name, None)
            raise
        finally:
            current_site_var.reset(token)
        return module


class LazySearchModule:
    def __init__(
        self,
        module_name: str,
        indice: int,
        use_for: str = None,
        source: str = "default",
        base_path: str = None,
        has_cli_args: bool = False,
    ):
        """
        Lazy loader for a search module.

        Args:
            module_name: Name of the site module (e.g., 'streamingcommunity')
            indice: Sort index for the module
            use_for: Content types this module supports
            source: Source of the module ('default' or custom path)
            base_path: Base path for custom sources
            has_cli_args: Whether the module's source defines register_cli_args
        """
        self.module_name = module_name
        self.indice = indice
        self._module = None
        self._search_func = None
        self._use_for = use_for
        self.source = source
        self.base_path = base_path
        self.has_cli_args = has_cli_args

    def _load_module(self):
        """Load the module on first access."""
        if self._module is None:
            try:
                compiled = CompiledService(self.module_name, os.path.join(self.base_path, self.module_name)) if self.base_path else None
                if compiled is not None and compiled.is_candidate():
                    qualified = f"VibraVid.{folder_name}.{self.module_name}" if self.source.lower() == "default" else self.module_name
                    self._module = compiled.load(qualified)
                elif self.source.lower() == "default":
                    try:
                        self._module = importlib.import_module(f"VibraVid.{folder_name}.{self.module_name}")
                    except (ImportError, OSError) as exc:
                        _reraise_if_binary_mismatch(self.module_name, self.module_name, exc)
                        raise
                else:
                    # Load from custom path
                    paths_to_add = [self.base_path]

                    # Also add module directory for relative imports
                    module_dir = os.path.join(self.base_path, self.module_name)
                    if module_dir != self.base_path:
                        paths_to_add.append(module_dir)

                    # Add paths temporarily
                    added_paths = []
                    for path in paths_to_add:
                        if path not in sys.path:
                            sys.path.insert(0, path)
                            added_paths.append(path)

                    try:
                        logger.debug(f"Loading module '{self.module_name}' from custom path: {self.base_path}")
                        spec = importlib.util.spec_from_file_location(
                            self.module_name,
                            os.path.join(self.base_path, self.module_name, "__init__.py"),
                            submodule_search_locations=[module_dir],
                        )
                        if spec and spec.loader:
                            self._module = importlib.util.module_from_spec(spec)
                            sys.modules[self.module_name] = self._module
                            try:
                                spec.loader.exec_module(self._module)
                            except (ImportError, OSError) as exc:
                                _reraise_if_binary_mismatch(self.module_name, self.module_name, exc)
                                raise
                        else:
                            raise ImportError(f"Could not load module {self.module_name} from {self.base_path}")

                    finally:
                        # Remove added paths
                        for path in added_paths:
                            if path in sys.path:
                                sys.path.remove(path)

                self._search_func = getattr(self._module, "search", None)
                if self._search_func is None:
                    raise AttributeError(f"Module '{self.module_name}' does not have a 'search' function")

                self._use_for = getattr(self._module, "_useFor", None)
                if self._use_for is None:
                    raise AttributeError(f"Module '{self.module_name}' does not define '_useFor'")

                logger.debug(f"Successfully loaded module '{self.module_name}' from source '{self.source}'")
            except Exception as e:
                console.print(f"[red]Failed to load module {self.module_name} from source '{self.source}': {str(e)}")
                raise

    def __call__(self, *args, **kwargs):
        """Execute search function when called.

        Args:
            *args: Positional arguments to pass to search function
            **kwargs: Keyword arguments to pass to search function

        Returns:
            Result from the search function
        """
        self._load_module()
        token = current_site_var.set(self.module_name)

        try:
            return self._search_func(*args, **kwargs)
        finally:
            current_site_var.reset(token)

    @property
    def use_for(self):
        """Get _useFor attribute (loads module if needed).

        Returns:
            List of content types this module supports
        """
        if self._use_for is None:
            self._load_module()

        return self._use_for

    def get_module(self):
        """Return the underlying site module, loading it if needed.

        Returns:
            The imported module object (e.g. to look up optional hooks like 'register_cli_args').
        """
        self._load_module()
        return self._module

    def __getitem__(self, index: int):
        """Support tuple unpacking: func, use_for = loaded_functions['name'].

        Args:
            index: Index to access (0 for function, 1 for use_for)

        Returns:
            Self (as callable) for index 0, use_for for index 1
        """
        if index == 0:
            return self
        elif index == 1:
            return self.use_for

        raise IndexError("LazySearchModule only supports indices 0 and 1")


def load_search_functions() -> dict[str, LazySearchModule]:
    """Load and return all available search functions from site modules.

    Returns:
        Dictionary mapping '{module_name}_search' to LazySearchModule instances
    """
    loaded_functions = {}
    modules_metadata = []
    loaded_module_names = set()
    total_service_dirs = 0  # [OPT #1] accumulated during the main loop, no second glob pass needed

    # [OPT #2] Persisted metadata cache keyed by init_file path -> {mtime, size, indice, use_for, has_cli_args}.
    # Lets a second process skip open()+parse for any __init__.py unchanged since the previous run.
    manifest = disk_cache.load("_site_loader", "manifest") or {}
    manifest_dirty = False
    seen_init_files = set()

    for source in imp_sources:
        if not source.strip():
            continue

        source_label = source
        if source.lower() == "default":
            if get_is_binary_installation():
                base_path = os.path.join(sys._MEIPASS, "VibraVid", folder_name)
            else:
                base_path = os.path.dirname(os.path.dirname(__file__))
        elif git_source.is_remote_source(source):
            source_label = git_source.redact(source)
            base_path = git_source.resolve_remote_source(source)
            if not base_path:
                logger.error(f"Could not resolve imp_service git source: {source_label}")
                console.print(f"[red]Error: Could not resolve imp_service git source: {source_label}[/red]")
                continue
        else:
            base_path = source

        if not os.path.isdir(base_path):
            logger.error(f"Import source path not found: {base_path}")
            continue

        logger.debug(f"Loading site modules from source '{source_label}': {base_path}")

        # Escape base_path for glob to handle paths with special characters like brackets
        escaped_base_path = os_manager.get_glob_path(base_path)
        found_inits = glob.glob(os.path.join(escaped_base_path, "*", "__init__.py"))
        service_entries = [(init_file, os.path.basename(os.path.dirname(init_file)), None) for init_file in found_inits]

        # Compiled services have no __init__.py: the package is a per-platform .pyd/.so inside a variant folder
        for dir_name in sorted(os.listdir(base_path)):
            compiled = CompiledService(dir_name, os.path.join(base_path, dir_name))
            if dir_name.startswith(("_", ".")) or not os.path.isdir(compiled.service_dir) or not compiled.is_candidate():
                continue

            entry_file = compiled.find_entry()
            if entry_file is None:
                total_service_dirs += 1
                logger.warning(f"Compiled service '{dir_name}' has no build for this system (variants: {', '.join(compiled.variants())})")
                console.print(f"[yellow]Warning: compiled service '{dir_name}' has no build matching this Python/OS[/yellow]")
                continue
            service_entries.append((entry_file, dir_name, compiled))

        source_modules = []
        for init_file, module_name, compiled in service_entries:

            # Skip helper/base modules (starting with underscore)
            if module_name.startswith("_"):
                logger.debug(f"Skipping helper module '{module_name}'")
                continue

            total_service_dirs += 1
            seen_init_files.add(init_file)
            if module_name in loaded_module_names:
                logger.info(f"Skipping duplicate module '{module_name}' from source '{source_label}'")
                continue

            try:
                stat = os.stat(init_file)
                cached = manifest.get(init_file)

                if cached and cached.get("mtime") == stat.st_mtime and cached.get("size") == stat.st_size:
                    indice = cached.get("indice")
                    use_for = cached.get("use_for")
                    has_cli_args = cached.get("has_cli_args", False)
                else:
                    indice = None
                    use_for = None
                    has_cli_args = False

                    if compiled is not None:
                        qualified = f"VibraVid.{folder_name}.{module_name}" if source.lower() == "default" else module_name
                        compiled_module = compiled.load(qualified)
                        indice = getattr(compiled_module, "indice", None)
                        use_for = getattr(compiled_module, "_useFor", None)
                        has_cli_args = hasattr(compiled_module, "register_cli_args")
                    else:
                        with open(init_file, encoding="utf-8") as f:
                            for raw_line in f:
                                if not has_cli_args and "def register_cli_args" in raw_line:
                                    has_cli_args = True

                                line = raw_line.strip()
                                if not indice and (line.startswith("indice =") or line.startswith("indice=")):
                                    try:
                                        indice = int(line.split("=")[1].strip())
                                    except (ValueError, IndexError) as e:
                                        logger.warning(f"Module '{module_name}': Failed to parse indice value - {str(e)}")
                                elif not use_for and (line.startswith("_useFor =") or line.startswith("_useFor=")):
                                    try:
                                        use_for = line.split("=")[1].strip().strip('"').strip("'")
                                    except IndexError as e:
                                        logger.warning(f"Module '{module_name}': Failed to parse _useFor value - {str(e)}")

                    manifest[init_file] = {
                        "mtime": stat.st_mtime,
                        "size": stat.st_size,
                        "indice": indice,
                        "use_for": use_for,
                        "has_cli_args": has_cli_args,
                    }
                    manifest_dirty = True

                # Validate that both indice and _useFor are defined
                if indice is None:
                    logger.error(f"Module '{module_name}' from source '{source_label}': Missing or invalid 'indice' declaration")
                    console.print(f"[red]Error: Module '{module_name}' is missing 'indice' declaration[/red]")
                    continue

                if use_for is None:
                    logger.error(f"Module '{module_name}' from source '{source_label}': Missing or invalid '_useFor' declaration")
                    console.print(f"[red]Error: Module '{module_name}' is missing '_useFor' declaration[/red]")
                    continue

                if use_for not in KNOWN_CONTENT_TYPES:
                    logger.warning(f"Module '{module_name}': unknown _useFor='{use_for}' (expected one of {', '.join(KNOWN_CONTENT_TYPES)})")
                    console.print(f"[yellow]Warning: Module '{module_name}' declares unknown _useFor='{use_for}' (expected: {', '.join(KNOWN_CONTENT_TYPES)})[/yellow]")

                source_modules.append((module_name, indice, use_for, source_label, base_path, has_cli_args))
                loaded_module_names.add(module_name)
                logger.debug(f"Found module '{module_name}' from source '{source_label}': use_for={use_for}, indice={indice}")

            except Exception as e:
                logger.error(f"Exception reading metadata from {module_name}: {str(e)}")
                console.print(f"[red]Error: Could not read metadata from {module_name}: {str(e)}[/red]")

        modules_metadata.extend(source_modules)

    # Check for duplicate indice values
    indice_map = {}
    for module_name, indice, _use_for, _source, _base_path, _has_cli_args in modules_metadata:
        if indice in indice_map:
            existing_module = indice_map[indice]
            logger.warning(f"Duplicate indice detected: Both '{module_name}' and '{existing_module}' have indice={indice}")
            console.print(f"[yellow]Warning: Duplicate indice={indice} for modules '{module_name}' and '{existing_module}'[/yellow]")
        else:
            indice_map[indice] = module_name

    hidden = hidden_services()
    unknown_hidden = hidden - {m[0].lower() for m in modules_metadata} - _warned_unknown_hidden
    if unknown_hidden:
        _warned_unknown_hidden.update(unknown_hidden)
        logger.warning(f"hidden_services lists unknown service(s): {', '.join(sorted(unknown_hidden))} (use the folder names under '{folder_name}')")

    sorted_modules = sorted(modules_metadata, key=lambda x: x[1])
    visible_count = 0
    for new_indice, (module_name, old_indice, use_for, source, base_path, has_cli_args) in enumerate(sorted_modules):
        if module_name.lower() in hidden:
            logger.info(f"Module '{module_name}' is in hidden_services: not loaded.")
        else:
            loaded_functions[f"{module_name}_search"] = LazySearchModule(
                module_name, visible_count, use_for, source, base_path, has_cli_args
            )
            visible_count += 1

        # Update indice in __init__.py for each module only if changed and from default source
        if source.lower() == "default":
            if new_indice == old_indice:
                continue

            init_file = os.path.join(base_path, module_name, "__init__.py")
            if not os.path.isfile(init_file):
                continue
            
            logger.info(f"Updating indice for module {module_name}: {old_indice} -> {new_indice}")

            try:
                with open(init_file, encoding="utf-8") as f:
                    lines = f.readlines()

                with open(init_file, "w", encoding="utf-8") as f:
                    for line in lines:
                        if line.strip().startswith("indice =") or line.strip().startswith("indice="):
                            f.write(f"indice = {new_indice}\n")
                        else:
                            f.write(line)

                # Keep the manifest cache in sync so the rewrite doesn't cause a spurious
                # cache miss (or a stale cached indice) on the next run.
                new_stat = os.stat(init_file)
                entry = manifest.get(init_file, {})
                entry.update({"mtime": new_stat.st_mtime, "size": new_stat.st_size, "indice": new_indice})
                manifest[init_file] = entry
                manifest_dirty = True

            except Exception as e:
                console.print(f"[yellow]Warning: Could not update indice in {module_name}: {str(e)}")

    logger.debug(f"Successfully loaded {len(loaded_functions)} search functions from {len(imp_sources)} source(s)")
    hidden_count = len(sorted_modules) - visible_count
    if total_service_dirs > len(loaded_functions) + hidden_count:
        skipped_count = total_service_dirs - len(loaded_functions) - hidden_count
        logger.warning(f"{skipped_count} module(s) were found but not loaded due to configuration errors")

    # Prune manifest entries for service folders that no longer exist, then persist once.
    stale_keys = [k for k in manifest if k not in seen_init_files]
    if stale_keys:
        for k in stale_keys:
            del manifest[k]
        manifest_dirty = True

    if manifest_dirty:
        disk_cache.save("_site_loader", "manifest", manifest)

    return loaded_functions


def refresh_external_sources() -> list[dict]:
    """Refresh every git `imp_service` source now; services of a source that changed are dropped from `sys.modules` so the next use re-imports them."""
    results = git_source.refresh_remote_sources(imp_sources)
    if not any(r["updated"] for r in results):
        return results

    external = [name for name, lazy in load_search_functions().items() if lazy.source.lower() != "default"]
    for alias in external:
        name = alias[: -len("_search")]
        for mod_name in [m for m in sys.modules if m == name or m.startswith(name + ".")]:
            del sys.modules[mod_name]
    return results


def get_folder_name() -> str:
    """Get the folder name where site modules are located.

    Returns:
        The folder name as a string
    """
    return folder_name


def resolve_service_submodule(module_name: str, submodule: str):
    """Import '<submodule>' of a site module, regardless of which imp_service source it came from."""
    lazy = load_search_functions().get(f"{module_name}_search")
    if lazy is None:
        raise ModuleNotFoundError(f"No module named '{module_name}' (not found in any imp_service source)")

    base_module = lazy.get_module()
    try:
        return importlib.import_module(f"{base_module.__name__}.{submodule}")
    except (ImportError, OSError) as exc:
        _reraise_if_binary_mismatch(module_name, submodule, exc)
        raise
