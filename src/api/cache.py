from __future__ import annotations

import functools
from typing import Any, Callable

from cachetools import TTLCache

MARKET_PULSE_TTL_SECONDS = 300
TECHNICAL_SIGNALS_TTL_SECONDS = 900

market_pulse_cache: TTLCache = TTLCache(maxsize=8, ttl=MARKET_PULSE_TTL_SECONDS)
technical_signals_cache: TTLCache = TTLCache(maxsize=1024, ttl=TECHNICAL_SIGNALS_TTL_SECONDS)


def cached(cache: TTLCache, key_fn: Callable[..., Any]) -> Callable:
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            key = key_fn(*args, **kwargs)
            if key in cache:
                return cache[key]
            result = func(*args, **kwargs)
            cache[key] = result
            return result

        return wrapper

    return decorator
