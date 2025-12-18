# 26.03.24

import os
import logging
from logging.handlers import RotatingFileHandler


# Internal utilities
from StreamingCommunity.Util.config_json import config_manager


class Logger:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Logger, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
        
    def __init__(self):
        # Initialize only once
        if getattr(self, '_initialized', False):
            return
        
        # Configure root logger
        self.debug_mode = config_manager.get_bool('DEFAULT', "debug")
        self.logger = logging.getLogger('')
        
        # Remove any existing handlers to avoid duplication
        for handler in self.logger.handlers[:]:
            self.logger.removeHandler(handler)
        
        # Reduce logging level for external libraries
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        
        # Set logging level based on debug_mode
        if self.debug_mode:
            self.logger.setLevel(logging.DEBUG)
            self._configure_console_log_file()

        else:
            self.logger.setLevel(logging.ERROR)
            self._configure_console_logging()
        
        self._initialized = True
        
    def _configure_console_logging(self):
        """Configure console logging output to terminal."""
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.ERROR)
        formatter = logging.Formatter('[%(filename)s:%(lineno)s - %(funcName)20s() ] %(asctime)s - %(levelname)s - %(message)s')
        console_handler.setFormatter(formatter)
        self.logger.addHandler(console_handler)
            
    def _configure_console_log_file(self):
        """Create a console.log file only when debug mode is enabled."""
        console_log_path = "console.log"
        try:
            # Remove existing file if present
            if os.path.exists(console_log_path):
                os.remove(console_log_path)
                
            # Create handler for console.log
            console_file_handler = RotatingFileHandler(
                console_log_path,
                maxBytes=5*1024*1024,  # 5 MB
                backupCount=3
            )
            console_file_handler.setLevel(logging.DEBUG)
            formatter = logging.Formatter('[%(filename)s:%(lineno)s - %(funcName)20s() ] %(asctime)s - %(levelname)s - %(message)s')
            console_file_handler.setFormatter(formatter)
            self.logger.addHandler(console_file_handler)
            
        except Exception as e:
            print(f"Error creating console.log: {e}")