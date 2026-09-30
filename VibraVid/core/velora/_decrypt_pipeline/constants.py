# 01.04.25

import logging

from VibraVid.utils import config_manager

logger = logging.getLogger("manual")
REQUEST_TIMEOUT = config_manager.config.get_int("REQUESTS", "timeout")
RETRY_COUNT = config_manager.config.get_int("REQUESTS", "max_retry")
MAX_TOKEN_REFRESH_ROUNDS = config_manager.config.get_int("DOWNLOAD", "max_token_refresh_rounds")
TOKEN_REFRESH_BACKOFF_SECONDS = config_manager.config.get_float(
    "DOWNLOAD", "token_refresh_backoff_seconds", default=2.0
)
TOKEN_REFRESH_STALL_ROUNDS = max(1, config_manager.config.get_int("DOWNLOAD", "token_refresh_stall_rounds", default=3))
MAX_MISSING_SEGMENT_RATIO = min(
    1.0,
    max(0.0, config_manager.config.get_float("DOWNLOAD", "max_missing_segment_ratio", default=0.05)),
)
_DECRYPT_ERROR_RATIO_LIMIT = 0.5
STREAMING_MUX_MIN_WAIT_SECONDS = 60.0
STREAMING_MUX_MAX_WAIT_SECONDS = 900.0
STREAMING_MUX_SECONDS_PER_SEGMENT = 0.2
STREAMING_MUX_MIN_THROUGHPUT_BPS = 1_048_576
_LIVE_MERGE_BUFSIZE = 2 * 1024 * 1024
