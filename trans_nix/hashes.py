"""Nix hash encoding and verification."""

from __future__ import annotations

import base64
import hashlib

from .errors import DownloadError
from .settings import NIX32_ALPHABET


def nix_base32(raw: bytes) -> str:
    # Nix writes groups from the high group to the low group, while bits within
    # each group are read little-endian from the digest byte array.
    chars: list[str] = []
    groups = (len(raw) * 8 - 1) // 5 + 1
    for group in range(groups - 1, -1, -1):
        bit = group * 5
        byte = bit // 8
        shift = bit % 8
        value = raw[byte] >> shift
        if byte + 1 < len(raw):
            value |= raw[byte + 1] << (8 - shift)
        chars.append(NIX32_ALPHABET[value & 0x1F])
    return "".join(chars)


def check_hash(spec: str, digest: bytes, what: str) -> None:
    algorithm, sep, expected = spec.partition(":")
    if not sep:
        raise DownloadError(f"invalid {what} hash: {spec!r}")

    # Narinfo normally uses Nix base32, but accepting hex and base64 makes this
    # work with binary caches that emit another standard representation.
    encodings = {nix_base32(digest), digest.hex(), base64.b64encode(digest).decode()}
    if expected not in encodings:
        raise DownloadError(
            f"{what} hash mismatch ({algorithm}): expected {expected}, "
            f"got {nix_base32(digest)}"
        )


def new_hasher(spec: str, what: str):
    algorithm = spec.partition(":")[0]
    try:
        return hashlib.new(algorithm)
    except ValueError as exc:
        raise DownloadError(f"unsupported {what} hash algorithm: {algorithm}") from exc
