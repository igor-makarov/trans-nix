import contextlib
import hashlib
import io
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from trans_nix import commands, errors, hashes, relocation, roots, settings
from trans_nix import nar as nar_format
from trans_nix import versions as versioning


def blob(data):
    return struct.pack("<Q", len(data)) + data + b"\0" * ((-len(data)) % 8)


def regular_nar(data, executable=False):
    fields = [blob(b"nix-archive-1"), blob(b"("), blob(b"type"), blob(b"regular")]
    if executable:
        fields.extend((blob(b"executable"), blob(b"")))
    fields.extend((blob(b"contents"), blob(data), blob(b")")))
    return b"".join(fields)


def nar_hash(data):
    digest = hashlib.sha256(data).digest()
    return "sha256:" + hashes.nix_base32(digest)


class VersionTests(unittest.TestCase):
    @staticmethod
    def system(platform="x86_64-linux", *, default=True, path=None):
        return {
            "system": platform,
            "outputs": [
                {
                    "name": "out",
                    "path": path or "/nix/store/" + "1" * 32 + "-demo",
                    "default": default,
                }
            ],
        }

    def test_numeric_and_latest_selectors(self):
        versions = [
            "23.9.0",
            "24.1.0-rc1",
            "24.1.0",
            "24.14.0",
            "25.0.0-beta1",
        ]
        self.assertEqual(versioning.resolve_version(versions, "24"), "24.14.0")
        self.assertEqual(versioning.resolve_version(versions, "latest"), "24.14.0")
        self.assertEqual(versioning.resolve_version(versions, "24.1.0"), "24.1.0")

    def test_version_listing_filters_by_platform_and_default_output(self):
        metadata = {
            "name": "demo",
            "releases": [
                {"version": "2.0.0", "platforms": [self.system()]},
                {"version": "1.10.0", "platforms": [self.system()]},
                {"version": "1.2.0", "platforms": [self.system()]},
                {
                    "version": "3.0.0",
                    "platforms": [self.system("aarch64-linux")],
                },
                {
                    "version": "broken",
                    "platforms": [self.system(default=False)],
                },
                {"version": "malformed", "platforms": "not-a-list"},
            ],
        }
        with mock.patch.object(
            versioning, "fetch_json", return_value=metadata
        ) as fetch:
            self.assertEqual(
                versioning.list_package_versions("demo", "x86_64-linux"),
                ["1.2.0", "1.10.0", "2.0.0"],
            )
        fetch.assert_called_once_with("https://search.devbox.sh/v2/pkg?name=demo")

    def test_resolution_uses_package_platform_default_without_second_request(self):
        metadata = {
            "name": "demo",
            "releases": [
                {
                    "version": "24.14.0",
                    "platforms": [self.system("aarch64-darwin")],
                },
                {
                    "version": "24.14.1",
                    "platforms": [
                        self.system(
                            "aarch64-darwin",
                            path="/nix/store/" + "2" * 32 + "-demo-24.14.1",
                        )
                    ],
                },
            ],
        }
        with mock.patch.object(
            versioning, "fetch_json", return_value=metadata
        ) as fetch:
            self.assertEqual(
                versioning.resolve_package("demo", "24.14", "aarch64-darwin"),
                ("24.14.1", "2" * 32),
            )
        fetch.assert_called_once_with("https://search.devbox.sh/v2/pkg?name=demo")

    def test_resolution_rejects_missing_platform(self):
        metadata = {
            "name": "demo",
            "releases": [
                {"version": "1.0", "platforms": [self.system("x86_64-linux")]}
            ],
        }
        with (
            mock.patch.object(versioning, "fetch_json", return_value=metadata),
            self.assertRaisesRegex(errors.DownloadError, "no installable versions"),
        ):
            versioning.resolve_package("demo", "1", "aarch64-linux")

    def test_default_output_must_be_unique(self):
        system = self.system()
        system["outputs"].append(dict(system["outputs"][0]))
        with self.assertRaisesRegex(errors.DownloadError, "2 default outputs"):
            versioning.nixhub_store_path(system, "demo", None)

    def test_named_output_is_selected(self):
        system = self.system()
        system["outputs"].append(
            {
                "name": "lib",
                "path": "/nix/store/" + "2" * 32 + "-demo-lib",
            }
        )
        self.assertEqual(
            versioning.nixhub_store_path(system, "demo", "lib"),
            "/nix/store/" + "2" * 32 + "-demo-lib",
        )

    def test_default_output_store_path_is_validated(self):
        system = self.system(path="/tmp/not-the-store")
        with self.assertRaisesRegex(errors.DownloadError, "invalid store path"):
            versioning.nixhub_store_path(system, "demo", None)


class RelocationTests(unittest.TestCase):
    def test_mirrored_root_uses_fixed_width_hex_counters(self):
        root_basename = "0" * 32 + "-nodejs-24.14.0"
        dependencies = [
            "1" * 32 + "-glibc-2.40",
            "2" * 32 + "-zlib-1.3.1",
        ]
        infos = {name: {} for name in [root_basename, *reversed(dependencies)]}
        root = Path("/Users/igor/.tn/nodejs/24.14.0")

        exact, destinations, width = relocation.build_relocations(
            root_basename, infos, root
        )

        self.assertEqual(len(os.fsencode(root)), 30)
        self.assertEqual(width, 8)
        self.assertEqual(destinations[dependencies[0]], ".tn/00000000-glibc-2.40")
        self.assertEqual(destinations[dependencies[1]], ".tn/00000001-zlib-1.3.1")
        self.assertEqual(destinations[root_basename], ".")
        for old, new in exact.items():
            self.assertEqual(len(old), len(new))
        self.assertTrue(
            exact[os.fsencode("/nix/store/" + root_basename)].startswith(
                os.fsencode(root)
            )
        )

    def test_counter_capacity_is_checked_before_download(self):
        root_basename = "0" * 32 + "-root"
        dependencies = [f"{i:032d}-dep" for i in range(1, 18)]
        infos = {name: {} for name in [root_basename, *dependencies]}
        root = Path("/" + "r" * 36)  # 37 bytes: one hexadecimal counter digit
        with self.assertRaisesRegex(errors.DownloadError, "closure has 17"):
            relocation.build_relocations(root_basename, infos, root)

    def test_root_references_are_padded_with_separators(self):
        rewritten = relocation.padded_root_path(b"/short/root", 20)
        self.assertEqual(len(rewritten), 20)
        self.assertEqual(rewritten.rstrip(b"/"), b"/short/root")

    def test_noncanonical_name_uses_hash_and_canonical_destination(self):
        digest = b"1" * 32
        canonical = b"/nix/store/" + digest + b"-glib-2.88.3"
        destination = b"/tmp/r/.tn/" + b"0" * 32 + b"-glib-2.88.3"
        alternate = b"/nix/store/" + digest + b"-glib-glib-2.88.3"
        suffix = b"/libg_base64_decode_inplace"
        self.assertEqual(len(canonical), len(destination))

        rewritten, count = relocation.rewrite_store_paths(
            alternate + suffix, {canonical: destination}
        )

        self.assertEqual(count, 1)
        self.assertEqual(len(rewritten), len(alternate + suffix))
        self.assertEqual(rewritten.removesuffix(suffix).rstrip(b"/"), destination)
        self.assertTrue(rewritten.endswith(suffix))
        self.assertNotIn(b"/nix/store", rewritten)

    def test_hash_fallback_rejects_a_destination_that_cannot_fit(self):
        digest = b"1" * 32
        canonical = b"/nix/store/" + digest + b"-long-package-name"
        destination = b"/tmp/r/.tn/" + b"0" * 32 + b"-long-package-name"
        short_alias = b"/nix/store/" + digest + b"-x"
        self.assertEqual(len(canonical), len(destination))

        with self.assertRaisesRegex(errors.DownloadError, "too long"):
            relocation.rewrite_store_paths(short_alias, {canonical: destination})

    def test_streamed_nar_extraction_rewrites_binary_data(self):
        old = b"/nix/store/" + b"1" * 32 + b"-dependency"
        new = b"/tmp/root/.tn/00000000000000000000000000000-dependency"
        self.assertEqual(len(old), len(new))
        payload = b"prefix\0" + old + b"\0suffix"
        nar = regular_nar(payload, executable=True)

        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "program"
            extractor = nar_format.NarExtractor(
                io.BytesIO(nar), nar_hash(nar), {old: new}
            )
            digest, size = extractor.extract(destination)
            self.assertEqual(size, len(nar))
            self.assertEqual(digest, hashlib.sha256(nar).digest())
            self.assertEqual(destination.read_bytes(), payload.replace(old, new))
            self.assertTrue(os.access(destination, os.X_OK))
            self.assertEqual(extractor.stats["exactRewrites"], 1)

    def test_reference_outside_closure_is_rejected(self):
        old = b"/nix/store/" + b"1" * 32 + b"-outside"
        with self.assertRaisesRegex(errors.DownloadError, "outside the closure"):
            relocation.rewrite_store_paths(old, {})

    def test_zstd_uses_python_314_standard_library(self):
        from compression import zstd

        payload = b"nix-archive-1" * 100
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "payload.zst"
            with zstd.open(archive, "wb") as output:
                output.write(payload)
            with nar_format.decompressed(archive, "zstd") as stream:
                self.assertEqual(stream.read(), payload)


class InstallRootTests(unittest.TestCase):
    def test_relocated_root_mirrors_short_storage_slug_and_version(self):
        with mock.patch.dict(os.environ, {"HOME": "/home/tester"}):
            self.assertEqual(
                roots.relocated_root("nodejs", "24.14.0"),
                Path("/home/tester/.tn/nodejs/24.14.0"),
            )

    def test_short_storage_slug_can_differ_from_nixhub_package(self):
        root = Path("/home/test/.tn/weasyprint/69.0")
        manifest = {
            "format": 4,
            "package": "python314Packages.weasyprint",
            "shortStorageSlug": "weasyprint",
            "version": "69.0",
            "platform": "aarch64-linux",
            "root": "1" * 32 + "-python3.14-weasyprint-69.0",
            "installRoot": str(root),
        }
        self.assertTrue(
            roots.manifest_matches(
                manifest,
                "python314Packages.weasyprint",
                "weasyprint",
                "69.0",
                "aarch64-linux",
                manifest["root"],
                root,
            )
        )

    def test_short_storage_slug_that_makes_root_too_long_is_rejected(self):
        with mock.patch.dict(os.environ, {"HOME": "/home/tester"}):
            root = roots.relocated_root("x" * 30, "1.0")
        with self.assertRaisesRegex(errors.DownloadError, "maximum is 37 bytes"):
            roots.validate_relocated_root_length(root)

    def test_install_link_replaces_mise_empty_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            target = base / "root"
            target.mkdir()
            link = base / "mise" / "nodejs" / "24.14.0"
            link.mkdir(parents=True)

            roots.install_link(link, target, force=False)

            self.assertTrue(link.is_symlink())
            self.assertEqual(Path(os.readlink(link)), target)

    def test_matching_root_is_reused_and_linked(self):
        root_basename = "1" * 32 + "-nodejs-24.14.0"
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            root = home / ".tn" / "nodejs" / "24.14.0"
            root.mkdir(parents=True)
            manifest = {
                "format": 4,
                "package": "nodejs",
                "shortStorageSlug": "nodejs",
                "version": "24.14.0",
                "platform": "aarch64-darwin",
                "root": root_basename,
                "installRoot": str(root),
            }
            (root / settings.MANIFEST_NAME).write_text(json.dumps(manifest))
            args = mock.Mock(
                package="nodejs",
                short_storage_slug=None,
                version="24",
                platform="aarch64-darwin",
                nix_package_output=None,
                jobs=4,
                install_to_path=home / "mise" / "24.14.0",
                force=False,
            )
            with (
                mock.patch.dict(os.environ, {"HOME": str(home)}),
                mock.patch.object(
                    commands,
                    "resolve_package",
                    return_value=("24.14.0", "1" * 32),
                ),
                mock.patch.object(commands, "validate_relocated_root_length"),
                mock.patch.object(
                    commands,
                    "discover_closure",
                    return_value=(root_basename, {root_basename: {}}),
                ),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(commands.run_install(args), 0)
            self.assertTrue(args.install_to_path.is_symlink())
            self.assertEqual(Path(os.readlink(args.install_to_path)), root)

    def test_manifest_match_includes_platform_and_source_root(self):
        root = Path("/home/test/.tn/nodejs/24.14.0")
        manifest = {
            "format": 4,
            "package": "nodejs",
            "shortStorageSlug": "nodejs",
            "version": "24.14.0",
            "platform": "aarch64-linux",
            "root": "1" * 32 + "-nodejs-24.14.0",
            "installRoot": str(root),
        }
        self.assertTrue(
            roots.manifest_matches(
                manifest,
                "nodejs",
                "nodejs",
                "24.14.0",
                "aarch64-linux",
                manifest["root"],
                root,
            )
        )
        changed = json.loads(json.dumps(manifest))
        changed["platform"] = "x86_64-linux"
        self.assertFalse(
            roots.manifest_matches(
                changed,
                "nodejs",
                "nodejs",
                "24.14.0",
                "aarch64-linux",
                manifest["root"],
                root,
            )
        )


if __name__ == "__main__":
    unittest.main()
