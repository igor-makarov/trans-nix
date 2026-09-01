import os
import unittest
from pathlib import Path
from unittest import mock

from hypothesis import given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st

from trans_nix import errors, roots, settings

PBT = hypothesis_settings(max_examples=60, deadline=None)
SAFE_SEGMENTS = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._+-",
    min_size=1,
    max_size=40,
).filter(lambda value: value not in (".", ".."))


class RootProperties(unittest.TestCase):
    @PBT
    @given(value=SAFE_SEGMENTS)
    def test_safe_segments_are_accepted_unchanged(self, value):
        self.assertEqual(roots.validate_segment(value, "segment"), value)

    @PBT
    @given(
        prefix=st.text(max_size=20),
        suffix=st.text(max_size=20),
        separator=st.sampled_from(("/", "\0")),
    )
    def test_path_separators_and_nuls_are_never_safe_segments(
        self, prefix, suffix, separator
    ):
        value = prefix + separator + suffix
        with self.assertRaises(errors.DownloadError):
            roots.validate_segment(value, "segment")

    @PBT
    @given(slug=SAFE_SEGMENTS, version=SAFE_SEGMENTS)
    def test_relocated_roots_are_always_beneath_home(self, slug, version):
        with mock.patch.dict(os.environ, {"HOME": "/home/property"}):
            result = roots.relocated_root(slug, version)
        self.assertEqual(
            result,
            Path("/home/property") / settings.RELOCATED_DIR_NAME / slug / version,
        )

    @PBT
    @given(length=st.integers(min_value=1, max_value=80))
    def test_relocated_root_byte_limit_is_exact(self, length):
        path = Path("/" + "x" * (length - 1))
        if length <= settings.MAX_RELOCATED_ROOT_BYTES:
            roots.validate_relocated_root_length(path)
        else:
            with self.assertRaisesRegex(errors.DownloadError, "too long"):
                roots.validate_relocated_root_length(path)

    @PBT
    @given(
        changed_key=st.sampled_from(
            (
                "format",
                "package",
                "shortStorageSlug",
                "version",
                "platform",
                "root",
                "installRoot",
            )
        )
    )
    def test_any_identity_field_change_invalidates_a_manifest(self, changed_key):
        root = Path("/home/test/.tn/demo/1.0.0")
        manifest = {
            "format": 4,
            "package": "demo",
            "shortStorageSlug": "demo",
            "version": "1.0.0",
            "platform": "x86_64-linux",
            "nixPackageOutput": None,
            "root": "0" * 32 + "-demo",
            "installRoot": str(root),
        }
        self.assertTrue(
            roots.manifest_matches(
                manifest,
                "demo",
                "demo",
                "1.0.0",
                "x86_64-linux",
                manifest["root"],
                root,
            )
        )
        manifest[changed_key] = "different"
        self.assertFalse(
            roots.manifest_matches(
                manifest,
                "demo",
                "demo",
                "1.0.0",
                "x86_64-linux",
                "0" * 32 + "-demo",
                root,
            )
        )
