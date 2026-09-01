import bz2
import gzip
import hashlib
import io
import lzma
import os
import struct
import tempfile
import unittest
from pathlib import Path

from hypothesis import given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st
from macholib.MachO import MachO

from trans_nix import hashes
from trans_nix import nar as nar_format

PBT = hypothesis_settings(max_examples=35, deadline=None)
NOOP_STORE_PATH = b"/nix/store/" + b"0" * 32 + b"-pbt-sentinel"
NOOP_EXACT = {NOOP_STORE_PATH: NOOP_STORE_PATH}
THIN_MAGICS = {
    b"\xfe\xed\xfa\xce": (">", False),
    b"\xce\xfa\xed\xfe": ("<", False),
    b"\xfe\xed\xfa\xcf": (">", True),
    b"\xcf\xfa\xed\xfe": ("<", True),
}
FAT_MAGICS = {b"\xca\xfe\xba\xbe": False, b"\xca\xfe\xba\xbf": True}


def blob(data: bytes) -> bytes:
    return struct.pack("<Q", len(data)) + data + b"\0" * ((-len(data)) % 8)


def regular_nar(data: bytes, executable: bool) -> bytes:
    fields = [blob(b"nix-archive-1"), blob(b"("), blob(b"type"), blob(b"regular")]
    if executable:
        fields.extend((blob(b"executable"), blob(b"")))
    fields.extend((blob(b"contents"), blob(data), blob(b")")))
    return b"".join(fields)


def hash_spec(data: bytes) -> str:
    return "sha256:" + hashes.nix_base32(hashlib.sha256(data).digest())


def thin_macho(magic: bytes, trailing: bytes = b"") -> bytes:
    endian, is_64_bit = THIN_MAGICS[magic]
    header = struct.pack(
        endian + "iiIIII",
        0x0100000C,  # CPU_TYPE_ARM64
        0,
        2,  # MH_EXECUTE
        0,
        0,
        0,
    )
    if is_64_bit:
        header += struct.pack(endian + "I", 0)
    return magic + header + trailing


def fat_macho(fat_magic: bytes, thin: bytes) -> bytes:
    is_64_bit = FAT_MAGICS[fat_magic]
    if is_64_bit:
        offset = 8 + 32
        architecture = struct.pack(">iiQQII", 0x0100000C, 0, offset, len(thin), 0, 0)
    else:
        offset = 8 + 20
        architecture = struct.pack(">iiIII", 0x0100000C, 0, offset, len(thin), 0)
    return fat_magic + struct.pack(">I", 1) + architecture + thin


def macholib_is_macho(path: Path) -> bool:
    try:
        return bool(MachO(path, allow_unknown_load_commands=True).headers)
    except EOFError, OSError, ValueError, struct.error:
        return False


class NarProperties(unittest.TestCase):
    @PBT
    @given(payload=st.binary(max_size=8192), executable=st.booleans())
    def test_regular_nar_round_trip_preserves_arbitrary_payloads(
        self, payload, executable
    ):
        encoded = regular_nar(payload, executable)
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "payload"
            extractor = nar_format.NarExtractor(
                io.BytesIO(encoded), hash_spec(encoded), NOOP_EXACT
            )
            digest, size = extractor.extract(destination)

            self.assertEqual(destination.read_bytes(), payload)
            self.assertEqual(size, len(encoded))
            self.assertEqual(digest, hashlib.sha256(encoded).digest())
            self.assertEqual(bool(os.stat(destination).st_mode & 0o100), executable)

    @PBT
    @given(payload=st.binary(max_size=8192))
    def test_empty_relocation_map_is_rejected_explicitly(self, payload):
        encoded = regular_nar(payload, executable=False)
        with self.assertRaisesRegex(ValueError, "relocation map must not be empty"):
            nar_format.NarExtractor(io.BytesIO(encoded), hash_spec(encoded), {})

    @PBT
    @given(
        payload=st.binary(max_size=4096),
        reads=st.lists(st.integers(0, 1000), max_size=20),
    )
    def test_hashing_reader_accounts_for_exactly_the_bytes_read(self, payload, reads):
        reader = nar_format.HashingReader(io.BytesIO(payload), "sha256:anything")
        consumed = b"".join(reader.read(size) for size in reads)
        consumed += reader.read()

        self.assertEqual(consumed, payload)
        self.assertEqual(reader.size, len(payload))
        self.assertEqual(reader.hasher.digest(), hashlib.sha256(payload).digest())

    @PBT
    @given(
        name=st.binary(min_size=1, max_size=100).filter(
            lambda value: (
                value not in (b".", b"..") and b"/" not in value and b"\0" not in value
            )
        )
    )
    def test_safe_child_names_remain_direct_children(self, name):
        parent = Path("/mock/root")
        child = nar_format.NarExtractor.child_path(parent, name)
        self.assertEqual(child.parent, parent)

    @PBT
    @given(
        magic=st.sampled_from(sorted(THIN_MAGICS)),
        trailing=st.binary(max_size=256),
    )
    def test_thin_macho_detection_matches_macholib(self, magic, trailing):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "thin"
            path.write_bytes(thin_macho(magic, trailing))
            self.assertTrue(macholib_is_macho(path))
            self.assertEqual(nar_format.is_macho(path), macholib_is_macho(path))

    @PBT
    @given(
        fat_magic=st.sampled_from(sorted(FAT_MAGICS)),
        thin_magic=st.sampled_from(sorted(THIN_MAGICS)),
        trailing=st.binary(max_size=128),
    )
    def test_fat_macho_detection_matches_macholib(
        self, fat_magic, thin_magic, trailing
    ):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fat"
            path.write_bytes(fat_macho(fat_magic, thin_macho(thin_magic, trailing)))
            self.assertTrue(macholib_is_macho(path))
            self.assertEqual(nar_format.is_macho(path), macholib_is_macho(path))

    @PBT
    @given(
        magic=st.sampled_from(sorted(THIN_MAGICS)),
        truncated_header=st.binary(max_size=23),
    )
    def test_truncated_thin_candidates_match_macholib(self, magic, truncated_header):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "truncated-thin"
            path.write_bytes(magic + truncated_header)
            self.assertFalse(macholib_is_macho(path))
            self.assertEqual(nar_format.is_macho(path), macholib_is_macho(path))

    @PBT
    @given(
        magic=st.sampled_from(sorted(FAT_MAGICS)),
        table_prefix=st.binary(max_size=19),
    )
    def test_truncated_fat_candidates_match_macholib(self, magic, table_prefix):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "truncated-fat"
            path.write_bytes(magic + struct.pack(">I", 1) + table_prefix)
            self.assertFalse(macholib_is_macho(path))
            self.assertEqual(nar_format.is_macho(path), macholib_is_macho(path))

    @PBT
    @given(
        minor=st.integers(min_value=0, max_value=0),
        major=st.integers(min_value=45, max_value=100),
        payload=st.binary(max_size=128),
    )
    def test_java_class_magic_is_not_macho(self, minor, major, payload):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "Example.class"
            path.write_bytes(
                b"\xca\xfe\xba\xbe" + struct.pack(">HH", minor, major) + payload
            )
            self.assertFalse(macholib_is_macho(path))
            self.assertEqual(nar_format.is_macho(path), macholib_is_macho(path))

    @PBT
    @given(
        payload=st.binary(max_size=8192),
        compression=st.sampled_from(("none", "gzip", "bz2", "xz")),
    )
    def test_supported_compressions_round_trip(self, payload, compression):
        compressors = {
            "none": lambda value: value,
            "gzip": gzip.compress,
            "bz2": bz2.compress,
            "xz": lzma.compress,
        }
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "archive"
            archive.write_bytes(compressors[compression](payload))
            with nar_format.decompressed(archive, compression) as stream:
                self.assertEqual(stream.read(), payload)
