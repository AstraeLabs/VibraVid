# 19.09.25

import os
import platform


class BinaryPaths:
    def __init__(self):
        self.system = self._detect_system()
        self.arch = self._detect_arch()
        self.home_dir = os.path.expanduser('~')
    
    def _detect_system(self) -> str:
        """
        Detect and normalize the operating system name.
        
        Returns:
            str: Normalized operating system name ('windows', 'darwin', or 'linux')
        
        Raises:
            ValueError: If the operating system is not supported
        """
        system = platform.system().lower()
        supported_systems = ['windows', 'darwin', 'linux']
        
        if system not in supported_systems:
            raise ValueError(f"Unsupported operating system: {system}. Supported: {supported_systems}")
        
        return system
    
    def _detect_arch(self) -> str:
        """
        Detect and normalize the system architecture.
        
        Returns:
            str: Normalized architecture name
        """
        machine = platform.machine().lower()
        arch_map = {
            'amd64': 'x64', 
            'x86_64': 'x64',
            'x64': 'x64',
            'arm64': 'arm64',
            'aarch64': 'arm64',
            'armv7l': 'arm',
            'i386': 'ia32',
            'i686': 'ia32',
            'x86': 'x86'
        }
        return arch_map.get(machine, machine)
    
    def get_binary_directory(self) -> str:
        """
        Get the binary directory path based on the operating system.
        
        Returns:
            str: Path to the binary directory
        """
        if self.system == 'windows':
            return os.path.join(os.path.splitdrive(self.home_dir)[0] + os.path.sep, 'binary')
        
        elif self.system == 'darwin':
            return os.path.join(self.home_dir, 'Applications', 'binary')
        
        else:  # linux
            return os.path.join(self.home_dir, '.local', 'bin', 'binary')
    
    def ensure_binary_directory(self, mode: int = 0o755) -> str:
        """
        Create the binary directory if it doesn't exist and return its path.
        
        Args:
            mode (int, optional): Directory permissions. Defaults to 0o755.
        
        Returns:
            str: Path to the binary directory
        """
        binary_dir = self.get_binary_directory()
        os.makedirs(binary_dir, mode=mode, exist_ok=True)
        return binary_dir


binary_paths = BinaryPaths()