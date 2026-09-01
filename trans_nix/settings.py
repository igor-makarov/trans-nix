"""Process-level configuration and format constants."""

from __future__ import annotations

import os
import re

NIXHUB_BASE = os.environ.get(
    "TRANS_NIX_NIXHUB_BASE", "https://search.devbox.sh/v2"
).rstrip("/")
CACHE_BASE = os.environ.get("TRANS_NIX_CACHE_BASE", "https://cache.nixos.org")
MANIFEST_NAME = ".nix-closure-manifest.json"
RELOCATED_DIR_NAME = ".tn"
MAX_RELOCATED_ROOT_BYTES = 37
SUPPORTED_PLATFORMS = {"x86_64-linux", "aarch64-linux", "aarch64-darwin"}
NIX32_ALPHABET = "0123456789abcdfghijklmnpqrsvwxyz"
DIGEST_RE = re.compile(r"^[0-9abcdfghijklmnpqrsvwxyz]{32}$")
NUMERIC_SELECTOR_RE = re.compile(r"^\d+(?:\.\d+)*$")
PRERELEASE_RE = re.compile(
    r"(?:^|[._+\-])(alpha|beta|pre|preview|rc|dev)\d*", re.IGNORECASE
)
STORE_PATH_BYTES_RE = re.compile(
    rb"/nix/store/([0123456789abcdfghijklmnpqrsvwxyz]{32})-"
    rb"([A-Za-z0-9+._?=-]+)"
)
MACHO_MAGICS = {
    b"\xfe\xed\xfa\xce",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"\xca\xfe\xba\xbf",
}
