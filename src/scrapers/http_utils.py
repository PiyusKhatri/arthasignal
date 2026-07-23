from __future__ import annotations

import logging
import threading
import time

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 15
MIN_REQUEST_INTERVAL_SECONDS = 1.0
MAX_REQUEST_INTERVAL_SECONDS = 2.0

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

_last_request_lock = threading.Lock()
_last_request_time = 0.0


def _throttle() -> None:
    global _last_request_time
    with _last_request_lock:
        elapsed = time.monotonic() - _last_request_time
        wait_for = MIN_REQUEST_INTERVAL_SECONDS - elapsed
        if wait_for > 0:
            time.sleep(wait_for)
        _last_request_time = time.monotonic()


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout, requests.HTTPError)),
)
def fetch(
    url: str,
    *,
    session: requests.Session | None = None,
    params: dict | None = None,
    headers: dict | None = None,
) -> requests.Response:
    _throttle()
    http = session or requests
    logger.info("Fetching %s", url)
    merged_headers = dict(DEFAULT_HEADERS)
    if headers:
        merged_headers.update(headers)
    response = http.get(url, headers=merged_headers, params=params, timeout=DEFAULT_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout, requests.HTTPError)),
)
def post(
    url: str,
    *,
    session: requests.Session | None = None,
    data: dict | None = None,
    headers: dict | None = None,
) -> requests.Response:
    _throttle()
    http = session or requests
    logger.info("Posting to %s", url)
    merged_headers = dict(DEFAULT_HEADERS)
    if headers:
        merged_headers.update(headers)
    response = http.post(url, data=data, headers=merged_headers, timeout=DEFAULT_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response
