"""Persistent root validation, manifests, and atomic links."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .errors import DownloadError
from .filesystem import path_lexists, remove_path
from .settings import (
    MANIFEST_NAME,
    MAX_RELOCATED_ROOT_BYTES,
    RELOCATED_DIR_NAME,
    SUPPORTED_PLATFORMS,
)


def validate_segment(value: str, what: str) -> str:
    if (
        not value
        or value in (".", "..")
        or "/" in value
        or "\0" in value
        or (os.altsep is not None and os.altsep in value)
    ):
        raise DownloadError(f"unsafe {what}: {value!r}")
    return value


def validate_platform(platform: str) -> str:
    if platform not in SUPPORTED_PLATFORMS:
        supported = ", ".join(sorted(SUPPORTED_PLATFORMS))
        raise DownloadError(
            f"unsupported platform {platform!r}; expected one of: {supported}"
        )
    return platform


def relocated_root(short_storage_slug: str, version: str) -> Path:
    short_storage_slug = validate_segment(short_storage_slug, "short storage slug")
    version = validate_segment(version, "version")
    home = os.environ.get("HOME")
    if not home:
        raise DownloadError("HOME must be set")
    home_path = Path(home)
    if not home_path.is_absolute():
        raise DownloadError(f"HOME must be absolute: {home!r}")
    return home_path / RELOCATED_DIR_NAME / short_storage_slug / version


def validate_relocated_root_length(root_path: Path) -> None:
    length = len(os.fsencode(root_path))
    if length > MAX_RELOCATED_ROOT_BYTES:
        raise DownloadError(
            f"relocated root is too long: {root_path} is {length} bytes; "
            f"maximum is {MAX_RELOCATED_ROOT_BYTES} bytes"
        )


def load_manifest(root_path: Path) -> dict | None:
    try:
        value = json.loads((root_path / MANIFEST_NAME).read_text())
    except OSError, json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def manifest_matches(
    manifest: dict | None,
    package: str,
    short_storage_slug: str,
    version: str,
    platform: str,
    root_basename: str,
    root_path: Path,
    output_name: str | None = None,
) -> bool:
    return bool(
        manifest
        and manifest.get("format") == 4
        and manifest.get("package") == package
        and manifest.get("shortStorageSlug") == short_storage_slug
        and manifest.get("version") == version
        and manifest.get("platform") == platform
        and manifest.get("nixPackageOutput") == output_name
        and manifest.get("root") == root_basename
        and manifest.get("installRoot") == os.fspath(root_path)
    )


def same_symlink(path: Path, target: Path) -> bool:
    if not path.is_symlink():
        return False
    current = Path(os.readlink(path))
    if not current.is_absolute():
        current = path.parent / current
    return os.path.normpath(os.path.abspath(current)) == os.path.normpath(
        os.path.abspath(target)
    )


def install_link(link: Path, target: Path, force: bool) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    if same_symlink(link, target):
        return

    if path_lexists(link):
        # Mise may prepare an empty install directory before invoking the hook.
        if link.is_dir() and not link.is_symlink() and not any(link.iterdir()):
            link.rmdir()
        elif force:
            remove_path(link)
        else:
            raise DownloadError(
                f"mise install path already exists: {link}; pass --force to replace it"
            )

    temporary = link.parent / f".{link.name}.link-{os.getpid()}"
    remove_path(temporary)
    try:
        os.symlink(os.fspath(target), temporary, target_is_directory=True)
        os.replace(temporary, link)
    finally:
        remove_path(temporary)
