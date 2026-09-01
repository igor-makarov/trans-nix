"""Pure relocation planning and byte-preserving path rewriting."""

from __future__ import annotations

import os
from pathlib import Path

from .errors import DownloadError
from .settings import (
    DIGEST_RE,
    MAX_RELOCATED_ROOT_BYTES,
    RELOCATED_DIR_NAME,
    STORE_PATH_BYTES_RE,
)


def relocation_destinations_by_digest(
    exact: dict[bytes, bytes],
) -> dict[bytes, bytes]:
    """Index canonical relocated destinations by their Nix store hash."""
    by_digest: dict[bytes, bytes] = {}
    for old, new in exact.items():
        match = STORE_PATH_BYTES_RE.fullmatch(old)
        if match is None:
            raise DownloadError(f"invalid canonical store reference: {old!r}")
        digest = match.group(1)
        destination = new.rstrip(b"/")
        previous = by_digest.setdefault(digest, destination)
        if previous != destination:
            raise DownloadError(f"store hash has multiple destinations: {digest!r}")
    return by_digest


def rewrite_store_paths(
    data: bytes,
    exact: dict[bytes, bytes],
    by_digest: dict[bytes, bytes] | None = None,
) -> tuple[bytes, int]:
    matches = list(STORE_PATH_BYTES_RE.finditer(data))
    if not matches:
        return data, 0
    if by_digest is None:
        by_digest = relocation_destinations_by_digest(exact)

    rewritten = bytearray(data)
    for match in matches:
        old = match.group(0)
        new = exact.get(old)
        if new is None:
            # Nix discovers references by their 32-byte store hash. A binary can
            # therefore contain a noncanonical name for a closure member (for
            # example, compiler metadata containing "hash-glib-glib-version")
            # even though only "hash-glib-version" exists in the store. Resolve
            # such references to the canonical relocated directory and preserve
            # their byte length with redundant path separators.
            destination = by_digest.get(match.group(1))
            if destination is None:
                raise DownloadError(f"store reference is outside the closure: {old!r}")
            if len(destination) > len(old):
                raise DownloadError(
                    f"canonical relocated path is too long for store reference: "
                    f"{old!r} ({len(old)}) -> {destination!r} ({len(destination)})"
                )
            new = destination + b"/" * (len(old) - len(destination))
        if len(old) != len(new):
            raise DownloadError(
                f"rewrite would change path length: {old!r} ({len(old)}) -> "
                f"{new!r} ({len(new)})"
            )
        rewritten[match.start() : match.end()] = new
    return bytes(rewritten), len(matches)


def safe_rewrite_limit(data: bytes, overlap: int) -> int:
    """Return a prefix boundary that does not split a store-path match."""
    limit = max(0, len(data) - overlap)
    for match in STORE_PATH_BYTES_RE.finditer(data):
        if match.start() < limit < match.end():
            return match.start()
    return limit


def store_basename(store_path: str) -> str:
    prefix = "/nix/store/"
    if not store_path.startswith(prefix):
        raise DownloadError(f"unexpected store path: {store_path!r}")
    basename = store_path[len(prefix) :]
    if not basename or "/" in basename or basename in (".", ".."):
        raise DownloadError(f"unsafe store path: {store_path!r}")
    digest = basename[:32]
    if not DIGEST_RE.fullmatch(digest):
        raise DownloadError(f"invalid store path digest: {store_path!r}")
    return basename


def reference_parts(reference: str) -> tuple[str, str]:
    digest = reference[:32]
    if not DIGEST_RE.fullmatch(digest):
        raise DownloadError(f"invalid narinfo reference: {reference!r}")
    if len(reference) > 32 and reference[32] != "-":
        raise DownloadError(f"invalid narinfo reference: {reference!r}")
    return digest, reference


def store_suffix(basename: str) -> str:
    if len(basename) <= 33 or basename[32] != "-":
        raise DownloadError(f"store path lacks a package name: {basename!r}")
    return basename[33:]


def padded_root_path(output: bytes, target_length: int) -> bytes:
    """Pad with redundant trailing separators while retaining the same path."""
    padding = target_length - len(output)
    if padding < 0:
        raise DownloadError(
            f"output path is {len(output)} bytes, but the package store path only "
            f"has {target_length} bytes"
        )
    return output + b"/" * padding


def build_relocations(
    root_basename: str, infos: dict[str, dict[str, str]], root_path: Path
) -> tuple[dict[bytes, bytes], dict[str, str], int]:
    """Return exact rewrites, relative destinations, and hex counter width."""
    root_bytes = os.fsencode(root_path)
    dependencies = sorted(set(infos) - {root_basename})
    counter_width = 0
    if dependencies:
        # old: /nix/store/<32-byte hash>-<name>
        # new: <root>/.tn/<zero-padded hex counter>-<name>
        counter_width = 38 - len(root_bytes)
        if counter_width < 1:
            raise DownloadError(
                f"relocated root is too long for dependencies: {root_path} is "
                f"{len(root_bytes)} bytes; maximum is {MAX_RELOCATED_ROOT_BYTES} bytes"
            )
        capacity = 16**counter_width
        if len(dependencies) > capacity:
            raise DownloadError(
                f"a {counter_width}-digit hexadecimal dependency counter can name "
                f"{capacity} paths, but the closure has {len(dependencies)} dependencies"
            )

    exact: dict[bytes, bytes] = {}
    destinations = {root_basename: "."}
    root_old = os.fsencode(f"/nix/store/{root_basename}")
    root_new = padded_root_path(root_bytes, len(root_old))
    exact[root_old] = root_new

    for counter, basename in enumerate(dependencies):
        suffix = store_suffix(basename)
        counter_text = f"{counter:0{counter_width}x}"
        relative = f"{RELOCATED_DIR_NAME}/{counter_text}-{suffix}"
        old = os.fsencode(f"/nix/store/{basename}")
        new = root_bytes + b"/" + os.fsencode(relative)
        if len(old) != len(new):
            raise DownloadError(
                f"internal relocation length error: {old!r} ({len(old)}) -> "
                f"{new!r} ({len(new)})"
            )
        exact[old] = new
        destinations[basename] = relative

    return exact, destinations, counter_width
