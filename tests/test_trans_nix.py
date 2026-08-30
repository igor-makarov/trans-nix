import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
LOADER = importlib.machinery.SourceFileLoader("trans_nix", str(ROOT / "bin/trans-nix"))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
trans_nix = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(trans_nix)


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
    return "sha256:" + trans_nix.nix_base32(digest)


class VersionTests(unittest.TestCase):
    def test_numeric_and_latest_selectors(self):
        versions = {
            "23.9.0": {},
            "24.1.0-rc1": {},
            "24.1.0": {},
            "24.14.0": {},
            "25.0.0-beta1": {},
        }
        self.assertEqual(trans_nix.resolve_version(versions, "24"), "24.14.0")
        self.assertEqual(trans_nix.resolve_version(versions, "latest"), "24.14.0")
        self.assertEqual(trans_nix.resolve_version(versions, "24.1.0"), "24.1.0")

    def test_version_listing_filters_dead_and_malformed_entries(self):
        digest = "1" * 32
        versions = {
            "2.0.0": {"d": digest},
            "1.10.0": {"d": digest},
            "1.2.0": {"d": digest},
            "3.0.0": {"d": digest, "ok": 0},
            "broken": {},
        }
        with mock.patch.object(
            trans_nix, "fetch_package_versions", return_value=versions
        ):
            self.assertEqual(
                trans_nix.list_package_versions("demo", "x86_64-linux"),
                ["1.2.0", "1.10.0", "2.0.0"],
            )


class RelocationTests(unittest.TestCase):
    def test_mirrored_root_uses_fixed_width_hex_counters(self):
        root_basename = "0" * 32 + "-nodejs-24.14.0"
        dependencies = [
            "1" * 32 + "-glibc-2.40",
            "2" * 32 + "-zlib-1.3.1",
        ]
        infos = {name: {} for name in [root_basename, *reversed(dependencies)]}
        root = Path("/Users/igor/.tn/nodejs/24.14.0")

        exact, destinations, width = trans_nix.build_relocations(
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
        with self.assertRaisesRegex(trans_nix.DownloadError, "closure has 17"):
            trans_nix.build_relocations(root_basename, infos, root)

    def test_root_references_are_padded_with_separators(self):
        rewritten = trans_nix.padded_root_path(b"/short/root", 20)
        self.assertEqual(len(rewritten), 20)
        self.assertEqual(rewritten.rstrip(b"/"), b"/short/root")

    def test_streamed_nar_extraction_rewrites_binary_data(self):
        old = b"/nix/store/" + b"1" * 32 + b"-dependency"
        new = b"/tmp/root/.tn/00000000000000000000000000000-dependency"
        self.assertEqual(len(old), len(new))
        payload = b"prefix\0" + old + b"\0suffix"
        nar = regular_nar(payload, executable=True)

        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "program"
            extractor = trans_nix.NarExtractor(
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
        with self.assertRaisesRegex(trans_nix.DownloadError, "outside the closure"):
            trans_nix.rewrite_store_paths(old, {})

    def test_zstd_uses_python_314_standard_library(self):
        from compression import zstd

        payload = b"nix-archive-1" * 100
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "payload.zst"
            with zstd.open(archive, "wb") as output:
                output.write(payload)
            with trans_nix.decompressed(archive, "zstd") as stream:
                self.assertEqual(stream.read(), payload)


class InstallRootTests(unittest.TestCase):
    def test_relocated_root_mirrors_package_and_version(self):
        with mock.patch.dict(os.environ, {"HOME": "/home/tester"}):
            self.assertEqual(
                trans_nix.relocated_root("nodejs", "24.14.0"),
                Path("/home/tester/.tn/nodejs/24.14.0"),
            )

    def test_install_link_replaces_mise_empty_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            target = base / "root"
            target.mkdir()
            link = base / "mise" / "nodejs" / "24.14.0"
            link.mkdir(parents=True)

            trans_nix.install_link(link, target, force=False)

            self.assertTrue(link.is_symlink())
            self.assertEqual(Path(os.readlink(link)), target)

    def test_matching_root_is_reused_and_linked(self):
        root_basename = "1" * 32 + "-nodejs-24.14.0"
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            root = home / ".tn" / "nodejs" / "24.14.0"
            root.mkdir(parents=True)
            manifest = {
                "format": 3,
                "package": "nodejs",
                "version": "24.14.0",
                "platform": "aarch64-darwin",
                "root": root_basename,
                "installRoot": str(root),
            }
            (root / trans_nix.MANIFEST_NAME).write_text(json.dumps(manifest))
            args = mock.Mock(
                package="nodejs",
                version="24",
                platform="aarch64-darwin",
                jobs=4,
                link=home / "mise" / "24.14.0",
                force=False,
            )
            with (
                mock.patch.dict(os.environ, {"HOME": str(home)}),
                mock.patch.object(
                    trans_nix,
                    "resolve_package",
                    return_value=("24.14.0", "1" * 32),
                ),
                mock.patch.object(
                    trans_nix,
                    "discover_closure",
                    return_value=(root_basename, {root_basename: {}}),
                ),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(trans_nix.run_install(args), 0)
            self.assertTrue(args.link.is_symlink())
            self.assertEqual(Path(os.readlink(args.link)), root)

    def test_manifest_match_includes_platform_and_source_root(self):
        root = Path("/home/test/.tn/nodejs/24.14.0")
        manifest = {
            "format": 3,
            "package": "nodejs",
            "version": "24.14.0",
            "platform": "aarch64-linux",
            "root": "1" * 32 + "-nodejs-24.14.0",
            "installRoot": str(root),
        }
        self.assertTrue(
            trans_nix.manifest_matches(
                manifest,
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
            trans_nix.manifest_matches(
                changed,
                "nodejs",
                "24.14.0",
                "aarch64-linux",
                manifest["root"],
                root,
            )
        )


if __name__ == "__main__":
    unittest.main()
