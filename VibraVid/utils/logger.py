# 29.01.24

import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from VibraVid.utils import config_manager
from VibraVid.utils._log_buffer import flush_startup_buffer

# INFO    → info, warning, error, critical
# WARNING → warning, error, critical
# ERROR   → error, critical
# DEBUG   → debug, info, warning, error, critical

def resolve_log_level(name) -> int:
    """Numeric level for a ``log_level`` config value; an unknown name (e.g. ``FULL``) falls back to INFO instead of crashing at import."""
    level = logging.getLevelName(str(name or "").strip().upper())
    if isinstance(level, int):
        return level
    
    print(f"[VibraVid] Unknown DEFAULT.log_level {name!r} in config.json (use DEBUG, INFO, WARNING, ERROR or CRITICAL): using INFO.", file=sys.stderr)
    return logging.INFO


conf_log_level = str(config_manager.config.get("DEFAULT", "log_level") or "").upper()
LOG_LEVEL = resolve_log_level(conf_log_level)
_log_file = None


def get_log_file_path():
    """Return the current log file path, if logging has been initialized."""
    return str(_log_file) if _log_file is not None else None


def setup_logger(name=None, no_log: bool = False):
    global _log_file
    app_base_path = config_manager.base_path

    if no_log:
        _log_file = None
        flush_startup_buffer(None)
        logger = logging.getLogger(name)
        logger.setLevel(LOG_LEVEL)
        root_logger = logging.getLogger()
        root_logger.setLevel(LOG_LEVEL)
        return logger

    cache_dir = Path(os.path.join(app_base_path, ".cache"))
    log_dir = cache_dir / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"Warning: Could not create log directory {log_dir}: {e}", file=sys.stderr)

    if _log_file is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        _log_file = log_dir / f"{timestamp}.log"

    log_format = logging.Formatter(
        "[%(asctime)s.%(msecs)03d] [%(levelname)s] [%(name)s] %(message)s", datefmt="%H:%M:%S"
    )

    logger = logging.getLogger(name)
    logger.setLevel(LOG_LEVEL)

    root_logger = logging.getLogger()
    root_logger.setLevel(LOG_LEVEL)

    already_has_file_handler = any(
        isinstance(h, (RotatingFileHandler, logging.FileHandler)) for h in root_logger.handlers
    )

    if not already_has_file_handler:
        try:
            file_handler = RotatingFileHandler(
                str(_log_file), maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
            )
            file_handler.setFormatter(log_format)
            file_handler.setLevel(LOG_LEVEL)
            root_logger.addHandler(file_handler)
            flush_startup_buffer(file_handler)

            logging.captureWarnings(True)
        except Exception as e:
            print(f"Error: Could not create file handler for {_log_file}: {e}", file=sys.stderr)
            raise

    return logger


logger = logging.getLogger(__name__)
