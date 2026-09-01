import argparse
import contextlib
import io
import json
import unittest
from pathlib import Path
from unittest import mock

from hypothesis import given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st

from trans_nix import arguments, commands, errors, settings

PBT = hypothesis_settings(max_examples=50, deadline=None)
SEGMENTS = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._+-",
    min_size=1,
    max_size=30,
).filter(lambda value: value not in (".", "..") and not value.startswith("-"))
VERSIONS = st.lists(
    st.text(alphabet="0123456789.abcdefghijklmnopqrstuvwxyz-", min_size=1, max_size=20),
    max_size=20,
    unique=True,
)


class ArgumentProperties(unittest.TestCase):
    @PBT
    @given(
        package=SEGMENTS,
        platform=st.sampled_from(sorted(settings.SUPPORTED_PLATFORMS)),
        output=st.one_of(st.none(), SEGMENTS),
        as_json=st.booleans(),
    )
    def test_list_versions_arguments_round_trip(
        self, package, platform, output, as_json
    ):
        argv = ["list-versions", package, platform]
        if output is not None:
            argv.extend(("--nix-package-output", output))
        if as_json:
            argv.append("--json")

        parsed = arguments.parse_args(argv)

        self.assertEqual(parsed.command, "list-versions")
        self.assertEqual(parsed.package, package)
        self.assertEqual(parsed.platform, platform)
        self.assertEqual(parsed.nix_package_output, output)
        self.assertEqual(parsed.json, as_json)

    @PBT
    @given(package=SEGMENTS, version=SEGMENTS, jobs=st.integers(min_value=1))
    def test_install_arguments_preserve_paths_and_positive_jobs(
        self, package, version, jobs
    ):
        parsed = arguments.parse_args(
            [
                "install",
                package,
                version,
                "x86_64-linux",
                "/tmp/install-here",
                "--jobs",
                str(jobs),
            ]
        )

        self.assertEqual(parsed.install_to_path, Path("/tmp/install-here"))
        self.assertEqual(parsed.jobs, jobs)


class CommandProperties(unittest.TestCase):
    @PBT
    @given(versions=VERSIONS, as_json=st.booleans())
    def test_list_versions_prints_exactly_the_adapter_result(self, versions, as_json):
        args = argparse.Namespace(
            package="demo",
            platform="x86_64-linux",
            nix_package_output=None,
            json=as_json,
        )
        output = io.StringIO()
        with (
            mock.patch.object(commands, "list_package_versions", return_value=versions),
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(commands.run_list_versions(args), 0)

        if as_json:
            self.assertEqual(json.loads(output.getvalue()), versions)
        else:
            self.assertEqual(output.getvalue(), "\n".join(versions) + "\n")

    @PBT
    @given(jobs=st.integers(max_value=0))
    def test_install_rejects_nonpositive_worker_counts_before_io(self, jobs):
        args = argparse.Namespace(
            package="demo",
            short_storage_slug=None,
            version="latest",
            platform="x86_64-linux",
            nix_package_output=None,
            jobs=jobs,
            install_to_path=Path("/unused"),
            force=False,
        )
        with (
            mock.patch.object(commands, "resolve_package") as resolve,
            self.assertRaisesRegex(errors.DownloadError, "jobs must be at least 1"),
        ):
            commands.run_install(args)
        resolve.assert_not_called()
