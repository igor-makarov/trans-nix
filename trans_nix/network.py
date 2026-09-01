"""HTTP transport with retry and response decoding."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .errors import DownloadError
from .settings import NIXHUB_BASE


def nixhub_url(endpoint: str, **query: str) -> str:
    return f"{NIXHUB_BASE}/{endpoint}?{urllib.parse.urlencode(query)}"


def request(url: str, *, retries: int = 4):
    headers = {"User-Agent": "trans-nix/1"}
    for attempt in range(retries):
        try:
            return urllib.request.urlopen(
                urllib.request.Request(url, headers=headers), timeout=60
            )
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt + 1 == retries:
                raise DownloadError(f"request failed: {url}: {exc}") from exc
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def fetch_bytes(url: str) -> bytes:
    with request(url) as response:
        return response.read()


def fetch_json(url: str) -> dict:
    try:
        value = json.loads(fetch_bytes(url))
    except json.JSONDecodeError as exc:
        raise DownloadError(f"invalid JSON from {url}: {exc}") from exc
    if not isinstance(value, dict):
        raise DownloadError(f"expected a JSON object from {url}")
    return value
