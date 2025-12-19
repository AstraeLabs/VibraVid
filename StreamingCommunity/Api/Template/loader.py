# 01.10.25 

import os
import sys
import glob
import logging
import importlib
from typing import Dict


# External import
from rich.console import Console


# Variable
console = Console()
folder_name = "Service"


class LazySearchModule:
    def __init__(self, module_name: str, indice: int):
        """
        Lazy loader for a search module.

        Args:
            module_name: Name of the site module (e.g., 'streamingcommunity')
            indice: Sort index for the module
        """
        self.module_name = module_name
        self.indice = indice
        self._module = None
        self._search_func = None
        self._use_for = None
    
    def _load_module(self):
        """Load the module on first access."""
        if self._module is None:
            try:
                self._module = importlib.import_module(
                    f'StreamingCommunity.Api.{folder_name}.{self.module_name}'
                )
                self._search_func = getattr(self._module, 'search')
                self._use_for = getattr(self._module, '_useFor')
                logging.info(f"Loaded module: {self.module_name}")
            except Exception as e:
                console.print(f"[red]Failed to load module {self.module_name}: {str(e)}")
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
        return self._search_func(*args, **kwargs)
    
    @property
    def use_for(self):
        """Get _useFor attribute (loads module if needed).
        
        Returns:
            List of content types this module supports
        """
        if self._use_for is None:
            self._load_module()

        return self._use_for
    
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


def load_search_functions() -> Dict[str, LazySearchModule]:
    """Load and return all available search functions from site modules.
    
    Returns:
        Dictionary mapping '{module_name}_search' to LazySearchModule instances
    """
    loaded_functions = {}
    
    # Determine base path (calculated once)
    if getattr(sys, 'frozen', False):

        # When frozen (exe), sys._MEIPASS points to temporary extraction directory
        base_path = os.path.join(sys._MEIPASS, "StreamingCommunity")
        api_dir = os.path.join(base_path, 'Api', folder_name)
        
    else:
        # When not frozen, __file__ is in StreamingCommunity/Api/Template/loader.py
        # Go up two levels to get to StreamingCommunity/Api
        base_path = os.path.dirname(os.path.dirname(__file__))
        api_dir = os.path.join(base_path, folder_name)
    
    # Quick scan: just read directory structure and module metadata
    modules_metadata = []
    
    for init_file in glob.glob(os.path.join(api_dir, '*', '__init__.py')):
        module_name = os.path.basename(os.path.dirname(init_file))
        
        try:
            # Read only the __init__.py file to extract metadata (no import)
            with open(init_file, 'r', encoding='utf-8') as f:
                content = f.read()
                
            # Quick check for deprecation without importing
            if '_deprecate = True' in content or '_deprecate=True' in content:
                continue
            
            # Extract indice using simple string search (faster than regex)
            indice = None
            for line in content.split('\n'):
                line = line.strip()
                if line.startswith('indice =') or line.startswith('indice='):
                    try:
                        indice = int(line.split('=')[1].strip())
                        break
                    except (ValueError, IndexError):
                        pass
            
            if indice is not None:
                modules_metadata.append((module_name, indice))
                logging.info(f"Found module: {module_name} (index: {indice})")
                
        except Exception as e:
            console.print(f"[yellow]Warning: Could not read metadata from {module_name}: {str(e)}")
    
    # Sort by index and create lazy loaders with consecutive indices
    sorted_modules = sorted(modules_metadata, key=lambda x: x[1])
    for new_indice, (module_name, _) in enumerate(sorted_modules):
        loaded_functions[f'{module_name}_search'] = LazySearchModule(module_name, new_indice)

        # Update indice in __init__.py for each module
        init_file = os.path.join(api_dir, module_name, '__init__.py')
        try:
            with open(init_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            with open(init_file, 'w', encoding='utf-8') as f:
                for line in lines:
                    if line.strip().startswith('indice =') or line.strip().startswith('indice='):
                        f.write(f'indice = {new_indice}\n')
                    else:
                        f.write(line)
                        
        except Exception as e:
            console.print(f"[yellow]Warning: Could not update indice in {module_name}: {str(e)}")

    logging.info(f"Loaded {len(loaded_functions)} search modules")
    return loaded_functions


def get_folder_name() -> str:
    """Get the folder name where site modules are located.
    
    Returns:
        The folder name as a string
    """
    return folder_name