import io
import unittest
from pathlib import Path
from unittest import mock

from hypothesis import given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st

from trans_nix import cache, closure, errors, settings

PBT = hypothesis_settings(max_examples=40, deadline=None)
NIX32 = settings.NIX32_ALPHABET
DIGESTS = st.text(alphabet=NIX32, min_size=32, max_size=32)
FIELD_VALUES = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126), max_size=80
)
REQUIRED_FIELDS = (
    "StorePath",
    "URL",
    "Compression",
    "NarHash",
    "NarSize",
)


class NonClosingBytesIO(io.BytesIO):
    def close(self):
        pass


class CacheProperties(unittest.TestCase):
    @PBT
    @given(values=st.lists(FIELD_VALUES, min_size=5, max_size=5))
    def test_narinfo_parser_preserves_all_required_field_values(self, values):
        expected = dict(zip(REQUIRED_FIELDS, values, strict=True))
        encoded = "\n".join(
            f"{key}: {value}" for key, value in expected.items()
        ).encode()

        self.assertEqual(cache.parse_narinfo(encoded, "mock://narinfo"), expected)

    @PBT
    @given(missing=st.sampled_from(REQUIRED_FIELDS))
    def test_narinfo_parser_reports_every_required_field(self, missing):
        encoded = "\n".join(
            f"{field}: value" for field in REQUIRED_FIELDS if field != missing
        ).encode()
        with self.assertRaisesRegex(errors.DownloadError, missing):
            cache.parse_narinfo(encoded, "mock://narinfo")

    @PBT
    @given(payload=st.binary(max_size=64 * 1024), chunk_size=st.integers(1, 8192))
    def test_archive_download_writes_and_counts_the_stream(self, payload, chunk_size):
        destination = mock.Mock(spec=Path)
        output = NonClosingBytesIO()
        destination.open.return_value = output
        response = io.BytesIO(payload)
        original_read = response.read
        response.read = lambda size=-1: original_read(min(size, chunk_size))

        with mock.patch.object(cache, "request", return_value=response):
            self.assertEqual(
                cache.download_archive("mock://archive", destination), len(payload)
            )
        self.assertEqual(output.getvalue(), payload)


class ClosureProperties(unittest.TestCase):
    @PBT
    @given(
        left=st.dictionaries(
            st.text(min_size=1, max_size=8), st.integers(), max_size=8
        ),
        right=st.dictionaries(
            st.text(min_size=1, max_size=8), st.integers(), max_size=8
        ),
    )
    def test_combining_stats_is_pointwise_addition(self, left, right):
        expected = {
            key: left.get(key, 0) + right.get(key, 0)
            for key in left.keys() | right.keys()
        }
        actual = dict(left)
        closure.combine_stats(actual, right)
        self.assertEqual(actual, expected)

    @PBT
    @given(
        dependency_digests=st.lists(
            DIGESTS.filter(lambda digest: digest != "0" * 32),
            max_size=8,
            unique=True,
        )
    )
    def test_closure_discovery_visits_each_referenced_digest_once(
        self, dependency_digests
    ):
        root_digest = "0" * 32
        root_basename = root_digest + "-root"
        dependency_basenames = [digest + "-dep" for digest in dependency_digests]
        infos = {
            root_digest: {
                "StorePath": "/nix/store/" + root_basename,
                "References": " ".join(dependency_basenames),
            },
            **{
                digest: {"StorePath": "/nix/store/" + basename}
                for digest, basename in zip(
                    dependency_digests, dependency_basenames, strict=True
                )
            },
        }

        with (
            mock.patch.object(
                closure, "fetch_narinfo", side_effect=infos.__getitem__
            ) as fetch,
            mock.patch.object(closure, "eprint"),
        ):
            actual_root, actual_infos = closure.discover_closure(root_digest, jobs=3)

        self.assertEqual(actual_root, root_basename)
        self.assertEqual(set(actual_infos), {root_basename, *dependency_basenames})
        self.assertEqual(
            sorted(call.args[0] for call in fetch.call_args_list), sorted(infos)
        )

    @PBT
    @given(force=st.booleans())
    def test_staging_install_only_replaces_an_existing_output_when_forced(self, force):
        staging = Path("/mock/staging")
        output = Path("/mock/output")
        with (
            mock.patch.object(closure, "path_lexists", return_value=True),
            mock.patch.object(closure, "remove_path") as remove,
            mock.patch.object(closure.os, "replace") as replace,
        ):
            if not force:
                with self.assertRaisesRegex(errors.DownloadError, "already exists"):
                    closure.install_staging(staging, output, force=False)
                replace.assert_not_called()
            else:
                closure.install_staging(staging, output, force=True)
                self.assertEqual(replace.call_count, 2)
                self.assertEqual(replace.call_args_list[-1], mock.call(staging, output))
                remove.assert_called()
