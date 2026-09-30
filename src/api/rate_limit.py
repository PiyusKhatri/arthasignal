from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

PUBLIC_RATE_LIMIT = "60/minute"
AUTH_SIGNUP_RATE_LIMIT = "5/minute"
AUTH_LOGIN_RATE_LIMIT = "10/minute"
AUTH_REFRESH_RATE_LIMIT = "30/minute"
AUTH_PASSWORD_RESET_RATE_LIMIT = "5/minute"

limiter = Limiter(key_func=get_remote_address)
