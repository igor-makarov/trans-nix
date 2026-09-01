import base64
import hashlib
import unittest
from pathlib import Path

from hypothesis import given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st

from trans_nix import hashes, relocation, settings

PBT = hypothesis_settings(max_examples=75, deadline=None)
NIX32 = settings.NIX32_ALPHABET
STORE_NAME_ALPHABET = (
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+._?=-"
)
DIGEST_BYTES = st.text(alphabet=NIX32, min_size=32, max_size=32).map(str.encode)
STORE_NAMES = st.text(
    alphabet=STORE_NAME_ALPHABET,
    min_size=1,
    max_size=40,
).map(str.encode)
STORE_PATH_SUFFIXES = st.one_of(
    st.just(b""),
    st.tuples(
        st.sampled_from(sorted(set(range(256)) - set(STORE_NAME_ALPHABET.encode()))),
        st.binary(max_size=29),
    ).map(lambda parts: bytes((parts[0],)) + parts[1]),
)


def reference_nix_base32(raw: bytes) -> str:
    number = int.from_bytes(raw, "little")
    groups = (len(raw) * 8 + 4) // 5
    return "".join(
        NIX32[(number >> (group * 5)) & 0x1F] for group in range(groups - 1, -1, -1)
    )


class HashProperties(unittest.TestCase):
    @PBT
    @given(raw=st.binary(max_size=512))
    def test_nix_base32_matches_little_endian_reference(self, raw):
        encoded = hashes.nix_base32(raw)
        self.assertEqual(encoded, reference_nix_base32(raw))
        self.assertEqual(len(encoded), (len(raw) * 8 + 4) // 5)
        self.assertLessEqual(set(encoded), set(NIX32))

    @PBT
    @given(payload=st.binary(max_size=4096))
    def test_hash_verification_accepts_every_supported_encoding(self, payload):
        digest = hashlib.sha256(payload).digest()
        encodings = (
            hashes.nix_base32(digest),
            digest.hex(),
            base64.b64encode(digest).decode(),
        )
        for encoded in encodings:
            hashes.check_hash("sha256:" + encoded, digest, "payload")


class RelocationProperties(unittest.TestCase):
    @PBT
    @given(
        digest=DIGEST_BYTES,
        name=STORE_NAMES,
        prefix=st.binary(max_size=30),
        suffix=STORE_PATH_SUFFIXES,
    )
    def test_exact_rewrites_preserve_length_and_surrounding_bytes(
        self, digest, name, prefix, suffix
    ):
        old = b"/nix/store/" + digest + b"-" + name
        new = b"/new/store/" + digest + b"-" + name

        rewritten, count = relocation.rewrite_store_paths(
            prefix + old + suffix, {old: new}
        )

        self.assertEqual(count, 1)
        self.assertEqual(len(rewritten), len(prefix + old + suffix))
        self.assertEqual(rewritten, prefix + new + suffix)

    @PBT
    @given(path=st.binary(min_size=1, max_size=100), extra=st.integers(0, 100))
    def test_root_padding_preserves_the_path_and_reaches_target_length(
        self, path, extra
    ):
        padded = relocation.padded_root_path(path, len(path) + extra)
        self.assertEqual(len(padded), len(path) + extra)
        self.assertEqual(padded.rstrip(b"/"), path.rstrip(b"/"))

    @PBT
    @given(
        digest=DIGEST_BYTES,
        name=STORE_NAMES,
        left=st.binary(max_size=20),
        right=st.binary(max_size=20),
        overlap=st.integers(min_value=0, max_value=100),
    )
    def test_safe_stream_boundary_never_bisects_a_store_reference(
        self, digest, name, left, right, overlap
    ):
        data = left + b"/nix/store/" + digest + b"-" + name + right
        boundary = relocation.safe_rewrite_limit(data, overlap)
        self.assertGreaterEqual(boundary, 0)
        self.assertLessEqual(boundary, len(data))
        for match in settings.STORE_PATH_BYTES_RE.finditer(data):
            self.assertFalse(match.start() < boundary < match.end())

    @PBT
    @given(
        dependencies=st.lists(
            st.tuples(DIGEST_BYTES, STORE_NAMES),
            max_size=12,
            unique_by=lambda pair: pair[0],
        )
    )
    def test_relocation_plan_is_length_preserving_and_deterministic(self, dependencies):
        root = "0" * 32 + "-root"
        names = [digest.decode() + "-" + name.decode() for digest, name in dependencies]
        infos = {name: {} for name in [root, *names]}
        root_path = Path("/tmp/r")

        first = relocation.build_relocations(root, infos, root_path)
        second = relocation.build_relocations(
            root, dict(reversed(list(infos.items()))), root_path
        )

        self.assertEqual(first, second)
        exact, destinations, _ = first
        self.assertEqual(set(destinations), set(infos))
        self.assertTrue(all(len(old) == len(new) for old, new in exact.items()))
