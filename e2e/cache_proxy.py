from __future__ import annotations

import hashlib
import os
import shutil
import sys
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import NoReturn, Self

UPSTREAMS = {
    "cache": "https://cache.nixos.org",
    "nixhub": "https://search.devbox.sh",
}


class ResponseCache:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _panic(self, url: str, error: BaseException) -> NoReturn:
        print(
            f"INFRASTRUCTURE PANIC: upstream request failed: {url}: {error}",
            file=sys.stderr,
            flush=True,
        )
        os._exit(86)

    def _lock(self, key: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def get(self, url: str) -> Path:
        key = hashlib.sha256(url.encode()).hexdigest()
        destination = self.root / key
        if destination.is_file():
            return destination
        with self._lock(key):
            if destination.is_file():
                return destination
            temporary = self.root / f".{key}.{os.getpid()}.tmp"
            try:
                request = urllib.request.Request(
                    url, headers={"User-Agent": "trans-nix-e2e-cache/1"}
                )
                with urllib.request.urlopen(request, timeout=60) as response:
                    if response.status != 200:
                        raise RuntimeError(f"unexpected HTTP status {response.status}")
                    with temporary.open("wb") as output:
                        shutil.copyfileobj(response, output, 1024 * 1024)
                os.replace(temporary, destination)
                return destination
            except (OSError, RuntimeError, TimeoutError) as error:
                temporary.unlink(missing_ok=True)
                self._panic(url, error)


class CachingProxy:
    def __init__(self, cache_dir: Path) -> None:
        response_cache = ResponseCache(cache_dir)

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parsed = urllib.parse.urlsplit(self.path)
                components = parsed.path.lstrip("/").split("/", 1)
                if len(components) != 2 or components[0] not in UPSTREAMS:
                    self.send_error(404)
                    return
                upstream = UPSTREAMS[components[0]]
                path = "/" + components[1]
                url = urllib.parse.urlunsplit(
                    (
                        "https",
                        urllib.parse.urlsplit(upstream).netloc,
                        path,
                        parsed.query,
                        "",
                    )
                )
                cached = response_cache.get(url)
                self.send_response(200)
                self.send_header("Content-Length", str(cached.stat().st_size))
                self.end_headers()
                with cached.open("rb") as source:
                    shutil.copyfileobj(source, self.wfile, 1024 * 1024)

            def log_message(self, format: str, *args: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return self.server.server_port

    @property
    def nixhub_base(self) -> str:
        return f"http://127.0.0.1:{self.port}/nixhub/v2"

    @property
    def cache_base(self) -> str:
        return f"http://127.0.0.1:{self.port}/cache"

    def __enter__(self) -> Self:
        self.thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
