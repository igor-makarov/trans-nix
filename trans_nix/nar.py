"""Streaming NAR decoding, payload rewriting, and Mach-O signing."""

from __future__ import annotations

import bz2
import contextlib
import gzip
import lzma
import os
import shutil
import struct
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

from .errors import DownloadError, NarError
from .filesystem import remove_path
from .hashes import check_hash, new_hasher
from .relocation import (
    relocation_destinations_by_digest,
    rewrite_store_paths,
    safe_rewrite_limit,
)
from .settings import MACHO_MAGICS


@contextlib.contextmanager
def decompressed(path: Path, compression: str) -> Iterator[BinaryIO]:
    compression = compression.lower()
    if compression in ("", "none"):
        with path.open("rb") as stream:
            yield stream
    elif compression == "xz":
        with lzma.open(path, "rb") as stream:
            yield stream
    elif compression in ("bzip2", "bz2"):
        with bz2.open(path, "rb") as stream:
            yield stream
    elif compression in ("gzip", "gz"):
        with gzip.open(path, "rb") as stream:
            yield stream
    elif compression in ("zstd", "zst"):
        from compression import zstd

        with zstd.open(path, "rb") as stream:
            yield stream
    else:
        raise DownloadError(f"unsupported NAR compression: {compression!r}")


class HashingReader:
    def __init__(self, source: BinaryIO, hash_spec: str):
        self.source = source
        self.hasher = new_hasher(hash_spec, "NAR")
        self.size = 0

    def read(self, size: int = -1) -> bytes:
        data = self.source.read(size)
        self.hasher.update(data)
        self.size += len(data)
        return data

    def read_exact(self, size: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            chunk = self.read(size - len(chunks))
            if not chunk:
                raise NarError(f"unexpected end of NAR (wanted {size} bytes)")
            chunks.extend(chunk)
        return bytes(chunks)


class NarExtractor:
    def __init__(self, source: BinaryIO, hash_spec: str, exact: dict[bytes, bytes]):
        if not exact:
            raise ValueError("relocation map must not be empty")
        self.reader = HashingReader(source, hash_spec)
        self.exact = exact
        self.by_digest = relocation_destinations_by_digest(exact)
        self.rewrite_overlap = max(4096, *(len(path) for path in exact))
        self.stats = {
            "exactRewrites": 0,
            "filesModified": 0,
            "symlinksModified": 0,
            "machosResigned": 0,
        }
        self.modified_machos: list[Path] = []

    def read_u64(self) -> int:
        return struct.unpack("<Q", self.reader.read_exact(8))[0]

    def read_blob(self, *, limit: int | None = None) -> bytes:
        size = self.read_u64()
        if limit is not None and size > limit:
            raise NarError(f"NAR field is unexpectedly large: {size} bytes")
        data = self.reader.read_exact(size)
        padding = (-size) % 8
        if padding:
            pad = self.reader.read_exact(padding)
            if pad != b"\0" * padding:
                raise NarError("non-zero NAR padding")
        return data

    def read_word(self) -> bytes:
        return self.read_blob(limit=4096)

    def expect(self, expected: bytes) -> None:
        actual = self.read_word()
        if actual != expected:
            raise NarError(f"expected NAR token {expected!r}, got {actual!r}")

    def copy_blob(self, output: BinaryIO) -> int:
        """Copy one NAR string while rewriting paths across chunk boundaries."""
        size = self.read_u64()
        remaining = size
        pending = b""
        rewrite_count = 0
        while remaining:
            chunk = self.reader.read_exact(min(1024 * 1024, remaining))
            remaining -= len(chunk)
            pending += chunk
            if remaining and len(pending) > self.rewrite_overlap:
                limit = safe_rewrite_limit(pending, self.rewrite_overlap)
                rewritten, count = rewrite_store_paths(
                    pending[:limit], self.exact, self.by_digest
                )
                output.write(rewritten)
                rewrite_count += count
                pending = pending[limit:]
        rewritten, count = rewrite_store_paths(pending, self.exact, self.by_digest)
        output.write(rewritten)
        rewrite_count += count

        padding = (-size) % 8
        if padding:
            pad = self.reader.read_exact(padding)
            if pad != b"\0" * padding:
                raise NarError("non-zero NAR padding")
        return rewrite_count

    @staticmethod
    def child_path(parent: Path, raw_name: bytes) -> Path:
        if (
            not raw_name
            or raw_name in (b".", b"..")
            or b"/" in raw_name
            or b"\0" in raw_name
        ):
            raise NarError(f"unsafe NAR entry name: {raw_name!r}")
        return parent / os.fsdecode(raw_name)

    def extract_node(self, destination: Path) -> None:
        self.expect(b"(")
        self.expect(b"type")
        kind = self.read_word()

        if kind == b"directory":
            destination.mkdir(mode=0o755)
            while True:
                token = self.read_word()
                if token == b")":
                    break
                if token != b"entry":
                    raise NarError(f"unexpected directory token: {token!r}")
                self.expect(b"(")
                self.expect(b"name")
                name = self.read_blob(limit=1024 * 1024)
                self.expect(b"node")
                self.extract_node(self.child_path(destination, name))
                self.expect(b")")
            os.chmod(destination, 0o755)
            return

        if kind == b"regular":
            token = self.read_word()
            executable = False
            if token == b"executable":
                if self.read_word() != b"":
                    raise NarError("invalid executable marker")
                executable = True
                token = self.read_word()
            if token != b"contents":
                raise NarError(f"unexpected regular-file token: {token!r}")
            with destination.open("xb") as output:
                rewrites = self.copy_blob(output)
            os.chmod(destination, 0o755 if executable else 0o644)
            self.expect(b")")
            if rewrites:
                self.stats["exactRewrites"] += rewrites
                self.stats["filesModified"] += 1
                if is_macho(destination):
                    self.modified_machos.append(destination)
            return

        if kind == b"symlink":
            self.expect(b"target")
            target = self.read_blob(limit=1024 * 1024)
            rewritten, rewrites = rewrite_store_paths(
                target, self.exact, self.by_digest
            )
            os.symlink(os.fsdecode(rewritten), destination)
            self.expect(b")")
            if rewrites:
                self.stats["exactRewrites"] += rewrites
                self.stats["symlinksModified"] += 1
            return

        raise NarError(f"unknown NAR node type: {kind!r}")

    def extract(self, destination: Path) -> tuple[bytes, int]:
        self.expect(b"nix-archive-1")
        self.extract_node(destination)
        trailing = self.reader.read(1)
        if trailing:
            raise NarError("trailing data after NAR root node")
        return self.reader.hasher.digest(), self.reader.size


def extract_archive(
    archive: Path,
    destination: Path,
    narinfo: dict[str, str],
    exact: dict[bytes, bytes],
    platform: str,
) -> dict[str, int]:
    """Verify and extract a NAR while rewriting its payload stream."""
    temporary = destination.parent / f".{destination.name}.tmp-{os.getpid()}"
    remove_path(temporary)
    try:
        with decompressed(archive, narinfo["Compression"]) as stream:
            extractor = NarExtractor(stream, narinfo["NarHash"], exact)
            digest, size = extractor.extract(temporary)
        expected_size = int(narinfo["NarSize"])
        if size != expected_size:
            raise DownloadError(
                f"NAR size mismatch: expected {expected_size}, extracted {size}"
            )
        check_hash(narinfo["NarHash"], digest, "NAR")
        extractor.stats["machosResigned"] = resign_machos(
            extractor.modified_machos, platform
        )
        os.replace(temporary, destination)
        return extractor.stats
    except Exception:
        remove_path(temporary)
        raise


def is_macho(path: Path) -> bool:
    """Recognize structurally plausible thin and fat Mach-O files."""
    if path.is_symlink() or not path.is_file():
        return False
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        file_size = stream.tell()
        stream.seek(0)
        magic = stream.read(4)
        if magic not in MACHO_MAGICS:
            return False

        thin_layouts = {
            b"\xfe\xed\xfa\xce": (">", 28),
            b"\xce\xfa\xed\xfe": ("<", 28),
            b"\xfe\xed\xfa\xcf": (">", 32),
            b"\xcf\xfa\xed\xfe": ("<", 32),
        }
        if magic in thin_layouts:
            endian, header_size = thin_layouts[magic]
            stream.seek(0)
            header = stream.read(header_size)
            if len(header) != header_size:
                return False
            sizeofcmds = struct.unpack_from(f"{endian}I", header, 20)[0]
            return sizeofcmds <= file_size - header_size

        is_64_bit = magic == b"\xca\xfe\xba\xbf"
        entry_format = ">iiQQII" if is_64_bit else ">iiIII"
        entry_size = struct.calcsize(entry_format)
        count_data = stream.read(4)
        if len(count_data) != 4:
            return False
        count = struct.unpack(">I", count_data)[0]
        if count == 0 or count > (file_size - 8) // entry_size:
            return False
        for _ in range(count):
            entry = stream.read(entry_size)
            if len(entry) != entry_size:
                return False
            fields = struct.unpack(entry_format, entry)
            offset, size = fields[2:4]
            if size == 0 or offset > file_size or size > file_size - offset:
                return False
        return True


def resign_machos(paths: list[Path], platform: str) -> int:
    if not paths:
        return 0
    if not platform.endswith("-darwin"):
        return 0
    codesign = shutil.which("codesign")
    if codesign is None:
        raise DownloadError(
            "rewritten Darwin Mach-O files require codesign; run this on macOS"
        )
    for path in paths:
        preserve = subprocess.run(
            [
                codesign,
                "--force",
                "--sign",
                "-",
                "--timestamp=none",
                "--preserve-metadata=identifier,entitlements,requirements,flags,runtime",
                os.fspath(path),
            ],
            capture_output=True,
            check=False,
        )
        if preserve.returncode != 0:
            fallback = subprocess.run(
                [
                    codesign,
                    "--force",
                    "--sign",
                    "-",
                    "--timestamp=none",
                    os.fspath(path),
                ],
                capture_output=True,
                check=False,
            )
            if fallback.returncode != 0:
                raise DownloadError(
                    f"codesign failed for {path}: "
                    f"{fallback.stderr.decode(errors='replace').strip()}"
                )
    return len(paths)
