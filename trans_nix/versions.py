"""Pure version selection plus the NixHub metadata adapter."""

from __future__ import annotations

import re
from collections.abc import Callable

from .errors import DownloadError
from .network import fetch_json, nixhub_url
from .settings import DIGEST_RE, NUMERIC_SELECTOR_RE, PRERELEASE_RE


def natural_version_key(version: str) -> tuple:
    parts: list[tuple[int, object]] = []
    for part in re.findall(r"\d+|[A-Za-z]+|[^A-Za-z\d]+", version):
        if part.isdigit():
            parts.append((2, int(part)))
        elif part.isalpha():
            parts.append((1, part.lower()))
        else:
            parts.append((0, part))
    return tuple(parts)


def resolve_version(available: list[str], selector: str) -> str:
    is_numeric = bool(NUMERIC_SELECTOR_RE.fullmatch(selector))
    if selector in available and (not is_numeric or selector.count(".") >= 2):
        return selector
    if not available:
        raise DownloadError("the package has no indexed versions")

    if selector == "latest":
        candidates = available
    elif is_numeric:
        prefix = selector + "."
        candidates = [v for v in available if v == selector or v.startswith(prefix)]
    else:
        candidates = []

    if not candidates:
        tail = ", ".join(sorted(available, key=natural_version_key)[-12:])
        raise DownloadError(
            f"version selector {selector!r} matched nothing; "
            f"newest indexed versions: {tail}"
        )
    stable = [v for v in candidates if not PRERELEASE_RE.search(v)]
    return max(stable or candidates, key=natural_version_key)


def nixhub_store_path(system: object, context: str, output_name: str | None) -> str:
    if not isinstance(system, dict):
        raise DownloadError(f"NixHub has no metadata for {context}")
    outputs = system.get("outputs")
    if not isinstance(outputs, list):
        raise DownloadError(f"NixHub returned malformed outputs for {context}")
    if output_name is None:
        selected = [
            output
            for output in outputs
            if isinstance(output, dict) and output.get("default") is True
        ]
        description = "default outputs"
    else:
        selected = [
            output
            for output in outputs
            if isinstance(output, dict) and output.get("name") == output_name
        ]
        description = f"outputs named {output_name!r}"
    if len(selected) != 1:
        raise DownloadError(
            f"NixHub returned {len(selected)} {description} for {context}; expected one"
        )
    path = selected[0].get("path")
    if not isinstance(path, str):
        raise DownloadError(f"NixHub returned an output without a path for {context}")
    basename = path.removeprefix("/nix/store/")
    if path != f"/nix/store/{basename}" or "/" in basename:
        raise DownloadError(
            f"NixHub returned an invalid store path for {context}: {path!r}"
        )
    digest, separator, name = basename.partition("-")
    if not separator or not name or not DIGEST_RE.fullmatch(digest):
        raise DownloadError(
            f"NixHub returned an invalid store path for {context}: {path!r}"
        )
    return path


def package_paths_from_metadata(
    metadata: dict, package: str, platform: str, output_name: str | None
) -> dict[str, str]:
    """Validate metadata and select one store path per usable version."""
    releases = metadata.get("releases")
    if metadata.get("name") != package or not isinstance(releases, list):
        raise DownloadError(f"NixHub returned malformed releases for {package!r}")

    paths = {}
    for release in releases:
        if not isinstance(release, dict) or not isinstance(release.get("version"), str):
            continue
        version = release["version"]
        systems = release.get("platforms")
        if not isinstance(systems, list):
            continue
        matching = [
            system
            for system in systems
            if isinstance(system, dict) and system.get("system") == platform
        ]
        if len(matching) != 1:
            continue
        try:
            path = nixhub_store_path(
                matching[0], f"{package} {version} on {platform}", output_name
            )
        except DownloadError:
            continue
        if version in paths and paths[version] != path:
            raise DownloadError(
                f"NixHub returned conflicting {platform} paths for {package} {version}"
            )
        paths[version] = path

    if not paths:
        raise DownloadError(
            f"NixHub has no installable versions of {package!r} for {platform}"
        )
    return paths


def fetch_package_paths(
    package: str,
    platform: str,
    output_name: str | None,
    *,
    fetch: Callable[[str], dict] | None = None,
) -> dict[str, str]:
    """Fetch package metadata, leaving its interpretation to a pure function."""
    url = nixhub_url("pkg", name=package)
    if fetch is None:
        fetch = fetch_json
    return package_paths_from_metadata(fetch(url), package, platform, output_name)


def list_package_versions(
    package: str, platform: str, output_name: str | None = None
) -> list[str]:
    paths = fetch_package_paths(package, platform, output_name)
    return sorted(paths, key=natural_version_key)


def resolve_package(
    package: str,
    selector: str,
    platform: str,
    output_name: str | None = None,
) -> tuple[str, str]:
    paths = fetch_package_paths(package, platform, output_name)
    version = resolve_version(list(paths), selector)
    digest = paths[version].removeprefix("/nix/store/").split("-", 1)[0]
    return version, digest
