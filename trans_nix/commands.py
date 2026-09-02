"""Command handlers and top-level dispatch."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path

from .arguments import parse_args
from .closure import discover_closure, materialize_closure
from .errors import DownloadError, NarError
from .filesystem import path_lexists, remove_path
from .presentation import eprint, human_size
from .roots import (
    install_link,
    load_manifest,
    manifest_matches,
    relocated_root,
    validate_install_link,
    validate_platform,
    validate_relocated_root_length,
    validate_segment,
)
from .versions import list_package_versions, resolve_package


def run_list_versions(args: argparse.Namespace) -> int:
    validate_segment(args.package, "package name")
    platform = validate_platform(args.platform)
    output_name = (
        validate_segment(args.nix_package_output, "Nix package output")
        if args.nix_package_output is not None
        else None
    )
    versions = list_package_versions(args.package, platform, output_name)
    if args.json:
        print(json.dumps(versions, separators=(",", ":")))
    else:
        print("\n".join(versions))
    return 0


def run_install(args: argparse.Namespace) -> int:
    validate_segment(args.package, "package name")
    short_storage_slug = validate_segment(
        args.short_storage_slug or args.package, "short storage slug"
    )
    platform = validate_platform(args.platform)
    output_name = (
        validate_segment(args.nix_package_output, "Nix package output")
        if args.nix_package_output is not None
        else None
    )
    if args.jobs < 1:
        raise DownloadError("--jobs must be at least 1")

    install_to_path = Path(os.path.abspath(os.path.expanduser(args.install_to_path)))
    if install_to_path == Path("/"):
        raise DownloadError("refusing to install to the filesystem root")

    version, digest = resolve_package(args.package, args.version, platform, output_name)
    root_path = relocated_root(short_storage_slug, version)
    validate_relocated_root_length(root_path)
    validate_install_link(install_to_path, root_path, args.force)
    root_path.parent.mkdir(parents=True, exist_ok=True)
    output_label = output_name or "default"
    eprint(
        f"resolved  {args.package} {args.version!r} -> {version} "
        f"({platform}, output {output_label})"
    )
    eprint(f"scanning closure metadata with {args.jobs} workers")
    root_basename, infos = discover_closure(digest, args.jobs)

    if path_lexists(root_path):
        manifest = load_manifest(root_path)
        if manifest_matches(
            manifest,
            args.package,
            short_storage_slug,
            version,
            platform,
            root_basename,
            root_path,
            output_name,
        ):
            install_link(install_to_path, root_path, args.force)
            print(install_to_path)
            eprint(f"reused: {root_path}")
            return 0
        if not args.force:
            raise DownloadError(
                f"relocated root exists but its manifest does not match: {root_path}; "
                "pass --force to replace it"
            )

    count, downloaded_bytes, stats = materialize_closure(
        args.package,
        short_storage_slug,
        version,
        platform,
        output_name,
        root_basename,
        infos,
        root_path,
        force=args.force,
        jobs=args.jobs,
    )
    install_link(install_to_path, root_path, args.force)

    print(install_to_path)
    eprint(
        f"complete: {count} paths, {human_size(downloaded_bytes)} downloaded, "
        f"{stats['exactRewrites']} store-path rewrites, "
        f"{stats['machosResigned']} Mach-O files re-signed"
    )
    return 0


def run_remove(args: argparse.Namespace) -> int:
    platform = validate_platform(args.platform)
    short_storage_slug = validate_segment(
        args.short_storage_slug or args.package, "short storage slug"
    )
    output_name = (
        validate_segment(args.nix_package_output, "Nix package output")
        if args.nix_package_output is not None
        else None
    )
    root_path = relocated_root(short_storage_slug, args.version)
    if not path_lexists(root_path):
        raise DownloadError(f"relocated root does not exist: {root_path}")
    manifest = load_manifest(root_path)
    matches = bool(
        manifest
        and manifest.get("format") == 4
        and manifest.get("package") == args.package
        and manifest.get("shortStorageSlug") == short_storage_slug
        and manifest.get("version") == args.version
        and manifest.get("platform") == platform
        and manifest.get("nixPackageOutput") == output_name
        and manifest.get("installRoot") == os.fspath(root_path)
    )
    if not matches and not args.force:
        raise DownloadError(
            f"refusing to remove a root with a mismatched manifest: {root_path}; "
            "pass --force to remove it"
        )
    remove_path(root_path)
    for parent in (root_path.parent, root_path.parent.parent):
        try:
            parent.rmdir()
        except OSError:
            break
    print(root_path)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "list-versions":
        return run_list_versions(args)
    if args.command == "install":
        return run_install(args)
    if args.command == "remove":
        return run_remove(args)
    raise AssertionError(f"unknown command: {args.command}")


def entrypoint() -> None:
    try:
        raise SystemExit(main())
    except (DownloadError, NarError, OSError, ValueError) as exc:
        eprint(f"error: {exc}")
        raise SystemExit(1) from None
