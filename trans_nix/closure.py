"""Binary-cache closure discovery, processing, and assembly."""

from __future__ import annotations

import concurrent.futures
import json
import os
import tempfile
import time
import urllib.parse
from pathlib import Path

from .cache import download_archive, fetch_narinfo
from .errors import DownloadError
from .filesystem import path_lexists, remove_path
from .nar import extract_archive
from .presentation import eprint, human_size
from .relocation import (
    build_relocations,
    reference_parts,
    store_basename,
)
from .settings import CACHE_BASE, MANIFEST_NAME, RELOCATED_DIR_NAME


def discover_closure(
    root_digest: str, jobs: int
) -> tuple[str, dict[str, dict[str, str]]]:
    """Crawl narinfos concurrently before assigning relocation names."""
    scheduled = {root_digest}
    infos: dict[str, dict[str, str]] = {}
    root_basename = ""
    completed = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as executor:
        pending: dict[
            concurrent.futures.Future[dict[str, str]], tuple[str, str | None]
        ] = {executor.submit(fetch_narinfo, root_digest): (root_digest, None)}
        while pending:
            done, _ = concurrent.futures.wait(
                pending, return_when=concurrent.futures.FIRST_COMPLETED
            )
            for future in done:
                digest, expected_reference = pending.pop(future)
                narinfo = future.result()
                basename = store_basename(narinfo["StorePath"])
                if basename[:32] != digest:
                    raise DownloadError(
                        f"narinfo store digest mismatch: requested {digest}, got {basename}"
                    )
                if expected_reference is not None and basename != expected_reference:
                    raise DownloadError(
                        f"narinfo reference mismatch: expected {expected_reference}, "
                        f"got {basename}"
                    )
                if digest == root_digest:
                    root_basename = basename
                infos[basename] = narinfo
                completed += 1
                eprint(f"[meta {completed:>3}] {basename}")

                for reference in narinfo.get("References", "").split():
                    ref_digest, ref_basename = reference_parts(reference)
                    if ref_digest in scheduled:
                        continue
                    scheduled.add(ref_digest)
                    pending[executor.submit(fetch_narinfo, ref_digest)] = (
                        ref_digest,
                        ref_basename,
                    )

    if not root_basename:
        raise DownloadError("root narinfo was not discovered")
    return root_basename, infos


def install_staging(staging: Path, output: Path, force: bool) -> None:
    if not path_lexists(output):
        os.replace(staging, output)
        return
    if not force:
        raise DownloadError(
            f"output already exists: {output}; pass --force to replace it"
        )

    backup = output.parent / f".{output.name}.old-{os.getpid()}"
    remove_path(backup)
    os.replace(output, backup)
    try:
        os.replace(staging, output)
    except Exception:
        os.replace(backup, output)
        raise
    remove_path(backup)


def process_closure_path(
    basename: str,
    narinfo: dict[str, str],
    processed_dir: Path,
    archive_dir: Path,
    exact: dict[bytes, bytes],
    platform: str,
) -> tuple[str, int, dict[str, int], float]:
    """Download, extract, rewrite, and sign one closure member."""
    started = time.monotonic()
    archive = archive_dir / f"{basename[:32]}.nar"
    destination = processed_dir / basename
    archive_url = urllib.parse.urljoin(CACHE_BASE + "/", narinfo["URL"])
    try:
        downloaded_bytes = download_archive(archive_url, archive)
        stats = extract_archive(archive, destination, narinfo, exact, platform)
    finally:
        archive.unlink(missing_ok=True)
    return basename, downloaded_bytes, stats, time.monotonic() - started


def combine_stats(total: dict[str, int], part: dict[str, int]) -> None:
    for key, value in part.items():
        total[key] = total.get(key, 0) + value


def materialize_closure(
    package: str,
    short_storage_slug: str,
    version: str,
    platform: str,
    output_name: str | None,
    root_basename: str,
    infos: dict[str, dict[str, str]],
    root_path: Path,
    *,
    force: bool,
    jobs: int,
) -> tuple[int, int, dict[str, int]]:
    exact, destinations, counter_width = build_relocations(
        root_basename, infos, root_path
    )
    ordered = [root_basename, *sorted(set(infos) - {root_basename})]
    downloaded_bytes = 0
    stats: dict[str, int] = {}

    with tempfile.TemporaryDirectory(
        prefix=f".{root_path.name}.build-", dir=root_path.parent
    ) as work_name:
        work = Path(work_name)
        staging = work / "root"
        archive_dir = work / "archives"
        processed_dir = work / "processed"
        archive_dir.mkdir()
        processed_dir.mkdir()

        worker_count = min(jobs, len(ordered))
        eprint(
            f"processing {len(ordered)} closure paths with {worker_count} workers "
            "(download -> extract -> rewrite -> sign)"
        )
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=worker_count
        ) as executor:
            futures = {
                executor.submit(
                    process_closure_path,
                    basename,
                    infos[basename],
                    processed_dir,
                    archive_dir,
                    exact,
                    platform,
                ): basename
                for basename in ordered
            }
            completed = 0
            try:
                for future in concurrent.futures.as_completed(futures):
                    basename, size, part_stats, elapsed = future.result()
                    downloaded_bytes += size
                    combine_stats(stats, part_stats)
                    completed += 1
                    eprint(
                        f"[done {completed:>3}/{len(ordered)}] {basename} "
                        f"({human_size(size)}, {part_stats['exactRewrites']} rewrites, "
                        f"{part_stats['machosResigned']} signed, {elapsed:.1f}s)"
                    )
            except Exception:
                for future in futures:
                    future.cancel()
                raise

        eprint("assembling relocated closure")
        processed_root = processed_dir / root_basename
        if not processed_root.is_dir():
            raise DownloadError("the root package is not a directory")
        os.replace(processed_root, staging)

        dependencies = ordered[1:]
        if dependencies:
            reserved = staging / RELOCATED_DIR_NAME
            if path_lexists(reserved):
                raise DownloadError(
                    f"the root package already contains reserved path {RELOCATED_DIR_NAME}"
                )
            reserved.mkdir()
            for basename in dependencies:
                destination = staging / destinations[basename]
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(processed_dir / basename, destination)

        manifest = {
            "format": 4,
            "package": package,
            "shortStorageSlug": short_storage_slug,
            "version": version,
            "platform": platform,
            "nixPackageOutput": output_name,
            "root": root_basename,
            "installRoot": os.fspath(root_path),
            "jobs": worker_count,
            "counterWidth": counter_width,
            "paths": {
                basename: {
                    "destination": destinations[basename],
                    "sourceNarHash": infos[basename]["NarHash"],
                }
                for basename in ordered
            },
            "rewriteStats": stats,
        }
        (staging / MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )
        eprint("installing relocated closure")
        install_staging(staging, root_path, force)

    return len(ordered), downloaded_bytes, stats
