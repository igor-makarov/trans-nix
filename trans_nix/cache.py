"""Nix binary-cache metadata and archive transport."""

from __future__ import annotations

from pathlib import Path

from .errors import DownloadError
from .network import fetch_bytes, request
from .settings import CACHE_BASE


def parse_narinfo(data: bytes, url: str) -> dict[str, str]:
    """Decode and validate the fields needed from a narinfo response."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DownloadError(f"non-UTF-8 narinfo from {url}") from exc
    result: dict[str, str] = {}
    for line in text.splitlines():
        if not line:
            continue
        key, sep, value = line.partition(": ")
        if not sep:
            raise DownloadError(f"malformed narinfo line from {url}: {line!r}")
        result[key] = value
    required = {
        "StorePath",
        "URL",
        "Compression",
        "NarHash",
        "NarSize",
    }
    missing = required - result.keys()
    if missing:
        raise DownloadError(f"narinfo from {url} lacks: {', '.join(sorted(missing))}")
    return result


def fetch_narinfo(digest: str) -> dict[str, str]:
    url = f"{CACHE_BASE}/{digest}.narinfo"
    return parse_narinfo(fetch_bytes(url), url)


def download_archive(url: str, destination: Path) -> int:
    size = 0
    with request(url) as response, destination.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
            size += len(chunk)
    return size
