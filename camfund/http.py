"""Polite HTTP session: retries with backoff, a fixed delay between requests."""

from __future__ import annotations

import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = (
    "Mozilla/5.0 (compatible; camfund/0.1; Cambridge PhD funding monitor; "
    "low-rate daily check)"
)


class PoliteSession(requests.Session):
    """``requests.Session`` that sleeps ``delay`` seconds before each request.

    Parameters
    ----------
    delay : float
        Minimum spacing between consecutive requests, in seconds.
    timeout : float
        Default per-request timeout, in seconds.
    """

    def __init__(self, delay: float = 0.5, timeout: float = 30.0) -> None:
        super().__init__()
        self.delay = delay
        self.timeout = timeout
        self._last = 0.0
        self.headers["User-Agent"] = USER_AGENT
        retry = Retry(
            total=4,
            backoff_factor=1.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "POST"),
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.mount("https://", adapter)
        self.mount("http://", adapter)

    def request(self, method, url, **kwargs):  # type: ignore[override]
        wait = self.delay - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        kwargs.setdefault("timeout", self.timeout)
        try:
            return super().request(method, url, **kwargs)
        finally:
            self._last = time.monotonic()
