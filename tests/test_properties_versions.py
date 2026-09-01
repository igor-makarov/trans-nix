import unittest

from hypothesis import given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st

from trans_nix import errors, versions

PBT = hypothesis_settings(max_examples=60, deadline=None)
VERSION_TUPLES = st.tuples(st.integers(0, 99), st.integers(0, 99), st.integers(0, 99))


class VersionProperties(unittest.TestCase):
    @PBT
    @given(parts=st.lists(VERSION_TUPLES, min_size=1, max_size=30, unique=True))
    def test_natural_sort_matches_numeric_component_order(self, parts):
        rendered = [".".join(map(str, part)) for part in parts]
        actual = sorted(rendered, key=versions.natural_version_key)
        expected = [".".join(map(str, part)) for part in sorted(parts)]
        self.assertEqual(actual, expected)

    @PBT
    @given(
        major=st.integers(0, 99),
        tails=st.lists(
            st.tuples(st.integers(0, 99), st.integers(0, 99)),
            min_size=1,
            max_size=30,
            unique=True,
        ),
    )
    def test_numeric_prefix_selects_the_greatest_matching_stable_version(
        self, major, tails
    ):
        available = [f"{major}.{minor}.{patch}" for minor, patch in tails]
        available.extend(("999.0.0", f"{major}.999.0-rc1"))
        expected_tail = max(tails)
        self.assertEqual(
            versions.resolve_version(available, str(major)),
            f"{major}.{expected_tail[0]}.{expected_tail[1]}",
        )

    @PBT
    @given(stable=VERSION_TUPLES, prerelease=VERSION_TUPLES)
    def test_latest_prefers_any_stable_release_over_prereleases(
        self, stable, prerelease
    ):
        stable_text = ".".join(map(str, stable))
        prerelease_text = ".".join(map(str, prerelease)) + "-rc1"
        self.assertEqual(
            versions.resolve_version([stable_text, prerelease_text], "latest"),
            stable_text,
        )

    @PBT
    @given(version_parts=st.lists(VERSION_TUPLES, min_size=1, max_size=20, unique=True))
    def test_metadata_adapter_keeps_one_valid_path_per_version(self, version_parts):
        releases = []
        expected = {}
        for index, parts in enumerate(version_parts, start=1):
            version = ".".join(map(str, parts))
            path = f"/nix/store/{index:032d}-demo-{version}"
            expected[version] = path
            releases.append(
                {
                    "version": version,
                    "platforms": [
                        {
                            "system": "x86_64-linux",
                            "outputs": [{"default": True, "name": "out", "path": path}],
                        }
                    ],
                }
            )
        metadata = {"name": "demo", "releases": releases}
        self.assertEqual(
            versions.package_paths_from_metadata(
                metadata, "demo", "x86_64-linux", None
            ),
            expected,
        )

    @PBT
    @given(
        selector=st.text(min_size=1, max_size=20).filter(
            lambda value: value != "latest"
        )
    )
    def test_unmatched_nonnumeric_selectors_are_rejected(self, selector):
        from trans_nix.settings import NUMERIC_SELECTOR_RE

        if NUMERIC_SELECTOR_RE.fullmatch(selector):
            return
        with self.assertRaises(errors.DownloadError):
            versions.resolve_version(["1.0.0", "2.0.0"], selector)
