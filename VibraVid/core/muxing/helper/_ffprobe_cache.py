# 15.07.26

import functools
import os


def ffprobe_cached(func):
    """Cache a probe result keyed by (abspath, mtime_ns, size, args). The entry is invalidated automatically when the file is rewritten (mtime/size change)."""
    cache: dict = {}

    @functools.wraps(func)
    def wrapper(file_path, *args, **kwargs):
        try:
            st = os.stat(file_path)
        except OSError:
            return func(file_path, *args, **kwargs)

        key = (os.path.abspath(file_path), st.st_mtime_ns, st.st_size, args, tuple(sorted(kwargs.items())))
        if key in cache:
            return cache[key]
        result = func(file_path, *args, **kwargs)
        cache[key] = result
        return result

    wrapper.cache_clear = cache.clear
    return wrapper
