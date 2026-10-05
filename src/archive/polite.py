from __future__ import annotations

import fcntl
import logging
import os
import time
from pathlib import Path
from typing import Any
from urllib import robotparser
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; arthasignal-research; internal research, low rate)"
LOCK_DIR = Path(os.environ.get("ARTHASIGNAL_RATE_DIR", "~/.cache/arthasignal/ratelimit")).expanduser()
DEFAULT_INTERVAL = 3.0
MINIMUM_INTERVAL = 2.0
RETRIES = 8
MAX_BACKOFF = 300.0


class RobotsDisallowed(RuntimeError):
    pass


class HostLimiter:
    def __init__(self, host: str, interval: float) -> None:
        if interval < MINIMUM_INTERVAL:
            raise ValueError(f"interval {interval}s is below the {MINIMUM_INTERVAL}s floor")
        LOCK_DIR.mkdir(parents=True, exist_ok=True)
        self.path = LOCK_DIR / host
        self.interval = interval

    def wait(self) -> None:
        with open(self.path, "a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            handle.seek(0)
            raw = handle.read().strip()
            last = float(raw) if raw else 0.0
            pause = last + self.interval - time.time()
            if pause > 0:
                time.sleep(pause)
            handle.seek(0)
            handle.truncate()
            handle.write(f"{time.time():.3f}")
            handle.flush()
            fcntl.flock(handle, fcntl.LOCK_UN)


class PoliteClient:
    def __init__(self, interval: float = DEFAULT_INTERVAL, respect_robots: bool = True, retries: int = RETRIES, timeout: float = 60.0) -> None:
        self.interval = interval
        self.retries = retries
        self.timeout = timeout
        self.respect_robots = respect_robots
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": USER_AGENT})
        self.limiters: dict[str, HostLimiter] = {}
        self.robots: dict[str, robotparser.RobotFileParser | None] = {}
        self.requests = 0

    def _limiter(self, host: str) -> HostLimiter:
        if host not in self.limiters:
            self.limiters[host] = HostLimiter(host, self.interval)
        return self.limiters[host]

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parts = urlparse(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self.robots:
            parser = robotparser.RobotFileParser()
            self._limiter(parts.netloc).wait()
            try:
                response = self.http.get(f"{base}/robots.txt", timeout=min(30.0, self.timeout))
                parser.modified()
                if response.status_code in (401, 403):
                    parser.disallow_all = True
                elif response.status_code == 200 and "html" not in response.headers.get("content-type", "").lower():
                    parser.parse(response.text.splitlines())
                else:
                    parser.allow_all = True
                self.robots[base] = parser
            except requests.RequestException:
                self.robots[base] = None
        parser = self.robots[base]
        return True if parser is None else parser.can_fetch(USER_AGENT, url)

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        if not self.allowed(url):
            raise RobotsDisallowed(url)
        host = urlparse(url).netloc
        kwargs.setdefault("timeout", self.timeout)
        error: Exception | None = None
        for attempt in range(self.retries):
            self._limiter(host).wait()
            self.requests += 1
            try:
                response = self.http.request(method, url, **kwargs)
                if response.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"http {response.status_code}")
                return response
            except requests.RequestException as exc:
                error = exc
                logger.warning("%s %s failed (%s), attempt %d", method, url, exc, attempt + 1)
                time.sleep(min(MAX_BACKOFF, self.interval * 2 ** (attempt + 1)))
        raise RuntimeError(f"{method} {url} failed after {self.retries} attempts: {error}")

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("POST", url, **kwargs)
