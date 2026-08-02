from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

PUBLIC_RATE_LIMIT = "60/minute"

limiter = Limiter(key_func=get_remote_address)
