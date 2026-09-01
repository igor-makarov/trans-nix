import unittest

from hypothesis import given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st

from trans_nix import presentation, settings

PBT = hypothesis_settings(max_examples=75, deadline=None)


class PresentationProperties(unittest.TestCase):
    @PBT
    @given(
        unit_index=st.integers(min_value=0, max_value=4),
        value=st.integers(min_value=1, max_value=1023),
    )
    def test_exact_binary_unit_multiples_have_canonical_labels(self, unit_index, value):
        units = ("B", "KiB", "MiB", "GiB", "TiB")
        size = value * 1024**unit_index
        expected = (
            f"{value} B"
            if unit_index == 0
            else f"{float(value):.2f} {units[unit_index]}"
        )
        self.assertEqual(presentation.human_size(size), expected)


class SettingProperties(unittest.TestCase):
    @PBT
    @given(digest=st.text(alphabet=settings.NIX32_ALPHABET, min_size=32, max_size=32))
    def test_digest_regex_accepts_exactly_sized_nix_digests(self, digest):
        self.assertIsNotNone(settings.DIGEST_RE.fullmatch(digest))
        self.assertIsNone(settings.DIGEST_RE.fullmatch(digest + "0"))
        self.assertIsNone(settings.DIGEST_RE.fullmatch("e" + digest[1:]))

    @PBT
    @given(magic=st.sampled_from(sorted(settings.MACHO_MAGICS)))
    def test_every_configured_macho_magic_is_four_bytes(self, magic):
        self.assertEqual(len(magic), 4)

    @PBT
    @given(
        digest=st.text(alphabet=settings.NIX32_ALPHABET, min_size=32, max_size=32).map(
            str.encode
        ),
        name=st.text(
            alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+._?=-",
            min_size=1,
            max_size=40,
        ).map(str.encode),
    )
    def test_store_path_regex_captures_digest_and_name(self, digest, name):
        path = b"/nix/store/" + digest + b"-" + name
        match = settings.STORE_PATH_BYTES_RE.fullmatch(path)
        self.assertIsNotNone(match)
        self.assertEqual(match.groups(), (digest, name))
