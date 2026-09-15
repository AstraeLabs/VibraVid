# 01.10.25

import contextvars
import glob
import importlib
import importlib.util
import logging
import os
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
KNOWN_CONTENT_TYPES = ("Anime", "Film_Serie", "Serie", "Song", "Tor")
current_site_var: "contextvars.ContextVar[str | None]" = contextvars.ContextVar("vibravid_current_site", default=None)


class LazySearchModule:
    def __init__(
        self,
        module_name: str,
        indice: int,
        use_for: str = None,
        source: str = "default",
        base_path: str = None,
        has_cli_args: bool = False,
        hide: bool = False,
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
            hide: Whether the module declares `_hide = True` (excluded from CLI/GUI/ARR listings)
        """
        self.module_name = module_name
        self.indice = indice
        self._module = None
        self._search_func = None
        self._use_for = use_for
        self.source = source
        self.base_path = base_path
        self.has_cli_args = has_cli_args
        self.hide = hide

    def _load_module(self):
        """Load the module on first access."""
        if self._module is None:
            try:
                if self.source.lower() == "default":
                    self._module = importlib.import_module(f"VibraVid.{folder_name}.{self.module_name}")
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
                            spec.loader.exec_module(self._module)
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

    # [OPT #2] Persisted metadata cache keyed by init_file path -> {mtime, size, indice, use_for, hide, has_cli_args}.
    # Lets a second process skip open()+parse for any __init__.py unchanged since the previous run.
    manifest = disk_cache.load("_site_loader", "manifest") or {}
    manifest_dirty = False
    seen_init_files = set()

    for source in imp_sources:
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

        source_modules = []
        for init_file in found_inits:
            module_name = os.path.basename(os.path.dirname(init_file))

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
                    hide = cached.get("hide", False)
                    has_cli_args = cached.get("has_cli_args", False)
                else:
                    indice = None
                    use_for = None
                    hide = False
                    has_cli_args = False

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
                            elif line.startswith("_hide =") or line.startswith("_hide="):
                                value = line.split("=")[1].split("#")[0].strip().lower()
                                hide = value == "true"

                    manifest[init_file] = {
                        "mtime": stat.st_mtime,
                        "size": stat.st_size,
                        "indice": indice,
                        "use_for": use_for,
                        "hide": hide,
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

                source_modules.append((module_name, indice, use_for, source_label, base_path, has_cli_args, hide))
                loaded_module_names.add(module_name)
                logger.debug(f"Found module '{module_name}' from source '{source_label}': use_for={use_for}, indice={indice}, hide={hide}")

            except Exception as e:
                logger.error(f"Exception reading metadata from {module_name}: {str(e)}")
                console.print(f"[red]Error: Could not read metadata from {module_name}: {str(e)}[/red]")

        modules_metadata.extend(source_modules)

    # Check for duplicate indice values
    indice_map = {}
    for module_name, indice, _use_for, _source, _base_path, _has_cli_args, _hide in modules_metadata:
        if indice in indice_map:
            existing_module = indice_map[indice]
            logger.warning(f"Duplicate indice detected: Both '{module_name}' and '{existing_module}' have indice={indice}")
            console.print(f"[yellow]Warning: Duplicate indice={indice} for modules '{module_name}' and '{existing_module}'[/yellow]")
        else:
            indice_map[indice] = module_name

    # Hidden modules (_hide = True) are excluded from CLI/GUI/ARR listings entirely and
    # don't consume a slot in the renumbered index sequence.
    visible_modules = [m for m in modules_metadata if not m[6]]
    hidden_modules = [m for m in modules_metadata if m[6]]
    for module_name, indice, use_for, source, base_path, has_cli_args, _hide in hidden_modules:
        loaded_functions[f"{module_name}_search"] = LazySearchModule(
            module_name, indice, use_for, source, base_path, has_cli_args, hide=True
        )
        logger.info(f"Module '{module_name}' is hidden (_hide = True); excluded from CLI/GUI/ARR listings.")

    # Sort by index and create lazy loaders with consecutive indices
    sorted_modules = sorted(visible_modules, key=lambda x: x[1])
    for new_indice, (module_name, old_indice, use_for, source, base_path, has_cli_args, _hide) in enumerate(sorted_modules):
        loaded_functions[f"{module_name}_search"] = LazySearchModule(
            module_name, new_indice, use_for, source, base_path, has_cli_args
        )

        # Update indice in __init__.py for each module only if changed and from default source
        if source.lower() == "default":
            if new_indice == old_indice:
                continue

            init_file = os.path.join(base_path, module_name, "__init__.py")
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
    if total_service_dirs > len(loaded_functions):
        skipped_count = total_service_dirs - len(loaded_functions)
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
    return importlib.import_module(f"{base_module.__name__}.{submodule}")
