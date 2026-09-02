from __future__ import annotations

import json
import os
import platform as host_platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from cache_proxy import CachingProxy
from hypothesis import HealthCheck, given, note, settings
from hypothesis import strategies as st
from hypothesis.database import DirectoryBasedExampleDatabase
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    rule,
    run_state_machine_as_test,
)

PACKAGES = (
    "hello",
    "coreutils",
    "tree",
    "zlib",
    "gnugrep",
    "sqlite",
    "findutils",
    "gnused",
    "file",
)
STABLE_MARKER = re.compile(
    r"(?:^|[._+\-])(alpha|beta|pre|preview|rc|dev)\d*", re.IGNORECASE
)
PATH_TOKENS = ("a", "two words", "é", "目录", ".dot", "a-b_c")
SLUG_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789 .-_"
OTHER_PLATFORM = {
    "aarch64-darwin": "aarch64-linux",
    "aarch64-linux": "x86_64-linux",
    "x86_64-linux": "aarch64-linux",
}


@dataclass(frozen=True)
class VersionChoice:
    package: str
    selector: str
    version: str


@dataclass(frozen=True)
class DestinationSpec:
    style: str
    slot: int
    token: str
    nested: bool


@dataclass(frozen=True)
class RootIdentity:
    package: str
    slug: str
    version: str
    platform: str


@dataclass(frozen=True)
class InventoryEntry:
    kind: str
    mode: int
    size: int
    inode: int
    mtime_ns: int
    target: str | None


def native_platform() -> str:
    key = (host_platform.system(), host_platform.machine())
    platforms = {
        ("Darwin", "arm64"): "aarch64-darwin",
        ("Linux", "aarch64"): "aarch64-linux",
        ("Linux", "arm64"): "aarch64-linux",
        ("Linux", "x86_64"): "x86_64-linux",
    }
    try:
        return platforms[key]
    except KeyError as error:
        raise SystemExit(f"unsupported E2E platform: {key}") from error


def remove_node(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def inventory(root: Path) -> dict[str, InventoryEntry]:
    result: dict[str, InventoryEntry] = {}
    if not root.exists():
        return result
    pending = [root]
    while pending:
        parent = pending.pop()
        for path in sorted(parent.iterdir(), key=lambda item: os.fsencode(item.name)):
            info = path.lstat()
            relative = os.fspath(path.relative_to(root))
            mode = stat.S_IMODE(info.st_mode)
            target: str | None = None
            if stat.S_ISLNK(info.st_mode):
                kind = "symlink"
                target = os.readlink(path)
            elif stat.S_ISDIR(info.st_mode):
                kind = "directory"
                pending.append(path)
            elif stat.S_ISREG(info.st_mode):
                kind = "file"
            else:
                kind = "other"
            result[relative] = InventoryEntry(
                kind, mode, info.st_size, info.st_ino, info.st_mtime_ns, target
            )
    return result


def subtree_inventory(path: Path) -> dict[str, InventoryEntry]:
    if not path.is_dir():
        return {}
    return inventory(path)


def same_link(path: Path, target: Path) -> bool:
    if not path.is_symlink():
        return False
    current = Path(os.readlink(path))
    if not current.is_absolute():
        current = path.parent / current
    return os.path.normpath(os.path.abspath(current)) == os.path.normpath(
        os.path.abspath(target)
    )


class Harness:
    def __init__(self, root: Path, proxy: CachingProxy) -> None:
        self.root = root
        self.mutable = root / "state"
        self.home = self.mutable / "h"
        self.work = self.mutable / "w"
        self.repo = Path(os.environ.get("TRANS_NIX_REPO", Path(__file__).parents[1]))
        self.cli = self.repo / "bin" / "trans-nix"
        self.platform = native_platform()
        self.proxy = proxy
        self.base_env = os.environ.copy()
        self.base_env.update(
            {
                "HOME": str(self.home),
                "PYTHONDONTWRITEBYTECODE": "1",
                "TRANS_NIX_CACHE_BASE": proxy.cache_base,
                "TRANS_NIX_NIXHUB_BASE": proxy.nixhub_base,
            }
        )
        self.discovered: dict[str, list[str]] = {}

    def reset(self) -> None:
        remove_node(self.mutable)
        self.home.mkdir(parents=True)
        self.work.mkdir()
        (self.home / "tmp").mkdir()
        if inventory(self.mutable) != {
            "h": inventory(self.mutable)["h"],
            "h/tmp": inventory(self.mutable)["h/tmp"],
            "w": inventory(self.mutable)["w"],
        }:
            raise AssertionError("per-example filesystem reset left unexpected state")

    def command(
        self, arguments: list[str], *, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = self.base_env.copy()
        env["TMPDIR"] = str(self.home / "tmp")
        return subprocess.run(
            [sys.executable, "-B", str(self.cli), *arguments],
            cwd=cwd or self.work,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def discover(self) -> list[VersionChoice]:
        self.reset()
        choices: list[VersionChoice] = []
        errors: list[str] = []
        for package in PACKAGES:
            result = self.command(["list-versions", package, self.platform, "--json"])
            if result.returncode:
                errors.append(
                    f"metadata prewarm failed for {package} on {self.platform}: "
                    f"{result.stderr.strip()}"
                )
                continue
            versions = json.loads(result.stdout)
            stable = [
                version for version in versions if not STABLE_MARKER.search(version)
            ]
            if len(stable) < 3:
                errors.append(
                    f"expected three stable {package} versions on {self.platform}, "
                    f"found {len(stable)}"
                )
                continue
            self.discovered[package] = stable
            selected: list[str] = []
            for version in reversed(stable):
                numeric_selector = bool(re.fullmatch(r"\d+(?:\.\d+)*", version))
                exact = not numeric_selector or version.count(".") >= 2
                matches = [
                    candidate
                    for candidate in stable
                    if candidate == version or candidate.startswith(version + ".")
                ]
                if exact or matches[-1] == version:
                    selected.append(version)
                if len(selected) == 3:
                    break
            selected.reverse()
            if len(selected) != 3:
                errors.append(
                    f"could not select three directly installable {package} versions "
                    f"on {self.platform}"
                )
                continue
            choices.extend(
                VersionChoice(package, version, version) for version in selected
            )
            choices.append(VersionChoice(package, "latest", stable[-1]))
            for version in selected:
                numeric = re.match(r"\d+(?:\.\d+)*", version)
                if not numeric:
                    continue
                components = numeric.group().split(".")
                for length in range(1, len(components)):
                    selector = ".".join(components[:length])
                    matches = [
                        candidate
                        for candidate in stable
                        if candidate == selector or candidate.startswith(selector + ".")
                    ]
                    if matches and matches[-1] == version:
                        choices.append(VersionChoice(package, selector, version))
        if errors:
            raise SystemExit("package corpus validation failed:\n" + "\n".join(errors))
        return list(dict.fromkeys(choices))


class World:
    def __init__(self, harness: Harness) -> None:
        self.harness = harness
        harness.reset()
        self.roots: dict[Path, RootIdentity] = {}
        self.links: dict[Path, Path] = {}
        self.user_empty_slugs: set[Path] = set()
        self.trace: list[dict[str, Any]] = []

    def record(self, action: str, **values: Any) -> None:
        self.trace.append({"action": action, **values})

    def fail(self, error: BaseException) -> None:
        replay = {
            "platform": self.harness.platform,
            "versions": self.harness.discovered,
            "actions": self.trace,
        }
        note("REPLAY " + json.dumps(replay, ensure_ascii=False, sort_keys=True))
        raise error

    def fit_slug(self, raw: str, version: str) -> str:
        raw = raw.strip(".") or "s"
        prefix = os.fsencode(self.harness.home / ".tn")
        available = 37 - len(prefix) - len(os.fsencode(version)) - 2
        if available < 1:
            raise AssertionError(f"version cannot fit relocated root: {version}")
        return raw.encode("ascii")[:available].decode() or "s"

    def root_path(self, choice: VersionChoice, slug: str) -> Path:
        return self.harness.home / ".tn" / slug / choice.version

    def destination(self, spec: DestinationSpec) -> tuple[str, Path, Path]:
        parts = [spec.token]
        if spec.nested:
            parts.insert(0, f"nested {spec.slot}")
        if spec.style == "absolute":
            path = self.harness.work / "absolute" / str(spec.slot)
            for part in parts:
                path /= part
            return str(path), path, self.harness.work
        if spec.style == "relative":
            relative = Path("relative") / str(spec.slot)
            for part in parts:
                relative /= part
            return str(relative), self.harness.work / relative, self.harness.work
        if spec.style == "home":
            suffix = Path("home") / str(spec.slot)
            for part in parts:
                suffix /= part
            return f"~/{suffix}", self.harness.home / suffix, self.harness.work
        real = self.harness.work / "real" / str(spec.slot)
        alias = self.harness.work / "alias" / str(spec.slot)
        real.mkdir(parents=True, exist_ok=True)
        alias.parent.mkdir(parents=True, exist_ok=True)
        if not alias.is_symlink():
            remove_node(alias)
            alias.symlink_to(real, target_is_directory=True)
        path = alias
        actual = real
        for part in parts:
            path /= part
            actual /= part
        return str(path), actual, self.harness.work

    def root_snapshots(self) -> dict[Path, dict[str, InventoryEntry]]:
        return {path: subtree_inventory(path) for path in self.roots}

    def assert_invariants(self) -> None:
        try:
            tn = self.harness.home / ".tn"
            actual: set[Path] = set()
            if tn.is_dir():
                for slug_dir in tn.iterdir():
                    if not slug_dir.is_dir() or slug_dir.is_symlink():
                        raise AssertionError(f"unexpected managed entry: {slug_dir}")
                    children = list(slug_dir.iterdir())
                    if not children and slug_dir not in self.user_empty_slugs:
                        raise AssertionError(f"empty managed slug leaked: {slug_dir}")
                    for root in children:
                        if not root.is_dir() or root.is_symlink():
                            raise AssertionError(f"unexpected managed root: {root}")
                        actual.add(root)
            if actual != set(self.roots):
                raise AssertionError(
                    f"managed roots differ: expected {set(self.roots)}, got {actual}"
                )
            for root, identity in self.roots.items():
                manifest = json.loads((root / ".nix-closure-manifest.json").read_text())
                expected = {
                    "format": 4,
                    "package": identity.package,
                    "shortStorageSlug": identity.slug,
                    "version": identity.version,
                    "platform": identity.platform,
                    "nixPackageOutput": None,
                    "installRoot": str(root),
                }
                for key, value in expected.items():
                    if manifest.get(key) != value:
                        raise AssertionError(
                            f"manifest mismatch at {root}: {key}={manifest.get(key)!r}, "
                            f"expected {value!r}"
                        )
            for link, target in self.links.items():
                if not same_link(link, target):
                    raise AssertionError(
                        f"destination link changed: {link} -> {target}"
                    )
            temporary_markers = (".build-", ".old-", ".link-")
            for relative in inventory(self.harness.mutable):
                if any(marker in Path(relative).name for marker in temporary_markers):
                    raise AssertionError(f"temporary artifact leaked: {relative}")
        except Exception as error:  # noqa: BLE001 - always emit a replay trace
            self.fail(error)

    def install(
        self,
        choice: VersionChoice,
        raw_slug: str,
        destination_spec: DestinationSpec,
        force: bool,
    ) -> None:
        slug = self.fit_slug(raw_slug, choice.version)
        argument, destination, cwd = self.destination(destination_spec)
        root = self.root_path(choice, slug)
        identity = RootIdentity(
            choice.package, slug, choice.version, self.harness.platform
        )
        before = inventory(self.harness.mutable)
        root_before = self.root_snapshots()
        existing = self.roots.get(root)
        root_ok = existing is None or existing == identity or force
        identical_install = existing == identity and same_link(destination, root)
        destination_ok = (
            not os.path.lexists(destination)
            or same_link(destination, root)
            or (
                destination.is_dir()
                and not destination.is_symlink()
                and not any(destination.iterdir())
            )
            or force
        )
        expected_success = root_ok and destination_ok
        arguments = [
            "install",
            choice.package,
            choice.selector,
            self.harness.platform,
            argument,
            f"--short-storage-slug={slug}",
        ]
        if force:
            arguments.append("--force")
        self.record(
            "install",
            choice=asdict(choice),
            slug=slug,
            destination=asdict(destination_spec),
            argument=argument,
            force=force,
            expectedSuccess=expected_success,
        )
        result = self.harness.command(arguments, cwd=cwd)
        try:
            if (result.returncode == 0) != expected_success:
                raise AssertionError(
                    f"install exit {result.returncode}, expected success={expected_success}:\n"
                    f"stdout={result.stdout}\nstderr={result.stderr}"
                )
            if not expected_success:
                after = inventory(self.harness.mutable)
                if after != before:
                    raise AssertionError("rejected install changed filesystem state")
                self.assert_invariants()
                return
            for other, snapshot in root_before.items():
                if other != root and subtree_inventory(other) != snapshot:
                    raise AssertionError(f"install changed unrelated root: {other}")
            if existing == identity and subtree_inventory(root) != root_before[root]:
                raise AssertionError("identical install rebuilt its translated root")
            if identical_install and inventory(self.harness.mutable) != before:
                raise AssertionError("identical install changed filesystem metadata")
            self.roots[root] = identity
            self.user_empty_slugs.discard(root.parent)
            self.links[destination] = root
            self.assert_invariants()
        except Exception as error:  # noqa: BLE001 - always emit a replay trace
            self.fail(error)

    def remove(
        self, choice: VersionChoice, raw_slug: str, force: bool, mismatch: bool
    ) -> None:
        slug = self.fit_slug(raw_slug, choice.version)
        root = self.root_path(choice, slug)
        existing = self.roots.get(root)
        platform = (
            OTHER_PLATFORM[self.harness.platform] if mismatch else self.harness.platform
        )
        requested = RootIdentity(choice.package, slug, choice.version, platform)
        expected_success = existing is not None and (existing == requested or force)
        before = inventory(self.harness.mutable)
        root_before = self.root_snapshots()
        arguments = [
            "remove",
            choice.package,
            choice.version,
            platform,
            f"--short-storage-slug={slug}",
        ]
        if force:
            arguments.append("--force")
        self.record(
            "remove",
            choice=asdict(choice),
            slug=slug,
            platform=platform,
            force=force,
            mismatch=mismatch,
            expectedSuccess=expected_success,
        )
        result = self.harness.command(arguments)
        try:
            if (result.returncode == 0) != expected_success:
                raise AssertionError(
                    f"remove exit {result.returncode}, expected success={expected_success}:\n"
                    f"stdout={result.stdout}\nstderr={result.stderr}"
                )
            if not expected_success:
                if inventory(self.harness.mutable) != before:
                    raise AssertionError("rejected remove changed filesystem state")
            else:
                self.roots.pop(root)
                self.user_empty_slugs.discard(root.parent)
                if os.path.lexists(root):
                    raise AssertionError(f"remove retained managed root: {root}")
                for other, snapshot in root_before.items():
                    if other != root and subtree_inventory(other) != snapshot:
                        raise AssertionError(f"remove changed unrelated root: {other}")
            self.assert_invariants()
        except Exception as error:  # noqa: BLE001 - always emit a replay trace
            self.fail(error)

    def create_destination_conflict(self, spec: DestinationSpec, kind: str) -> None:
        argument, destination, _ = self.destination(spec)
        remove_node(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if kind == "file":
            destination.write_text("user data")
        elif kind == "empty-directory":
            destination.mkdir()
        elif kind == "directory":
            destination.mkdir()
            (destination / "user-file").write_text("user data")
        elif kind == "dangling-symlink":
            destination.symlink_to(destination.parent / "missing")
        else:
            destination.symlink_to(self.harness.work / "wrong-root")
        self.links.pop(destination, None)
        self.record(
            "create-destination-conflict",
            destination=asdict(spec),
            argument=argument,
            kind=kind,
        )
        self.assert_invariants()

    def delete_destination(self, spec: DestinationSpec) -> None:
        argument, destination, _ = self.destination(spec)
        remove_node(destination)
        self.links.pop(destination, None)
        self.record(
            "user-delete-destination", destination=asdict(spec), argument=argument
        )
        self.assert_invariants()

    def delete_root(self, choice: VersionChoice, raw_slug: str) -> None:
        slug = self.fit_slug(raw_slug, choice.version)
        root = self.root_path(choice, slug)
        remove_node(root)
        self.roots.pop(root, None)
        if root.parent.is_dir() and not any(root.parent.iterdir()):
            self.user_empty_slugs.add(root.parent)
        self.record("user-delete-root", choice=asdict(choice), slug=slug)
        self.assert_invariants()


def main() -> None:
    configured_root = os.environ.get("TRANS_NIX_TEST_ROOT")
    owned_root = configured_root is None
    root = (
        Path(configured_root)
        if configured_root is not None
        else Path(tempfile.mkdtemp(prefix="e", dir="/tmp"))
    )
    examples = int(os.environ.get("TRANS_NIX_PBT_EXAMPLES", "25"))
    steps = int(os.environ.get("TRANS_NIX_PBT_STEPS", "10"))
    try:
        with CachingProxy(root / "proxy-cache") as proxy:
            harness = Harness(root, proxy)
            choices = harness.discover()
            choice_strategy = st.sampled_from(choices)
            slug_strategy = st.text(SLUG_ALPHABET, min_size=1, max_size=6)
            destination_strategy = st.builds(
                DestinationSpec,
                style=st.sampled_from(
                    ("absolute", "relative", "home", "symlink-parent")
                ),
                slot=st.integers(0, 3),
                token=st.sampled_from(PATH_TOKENS),
                nested=st.booleans(),
            )
            database = DirectoryBasedExampleDatabase(root / "hypothesis")
            common_settings = settings(
                max_examples=examples,
                deadline=None,
                database=database,
                suppress_health_check=(HealthCheck.too_slow,),
            )

            @common_settings
            @given(choice_strategy, slug_strategy, destination_strategy)
            def install_is_idempotent(
                choice: VersionChoice, slug: str, destination: DestinationSpec
            ) -> None:
                world = World(harness)
                world.install(choice, slug, destination, False)
                world.install(choice, slug, destination, False)

            @common_settings
            @given(choice_strategy, slug_strategy, destination_strategy)
            def rejected_destination_conflict_is_atomic(
                choice: VersionChoice, slug: str, destination: DestinationSpec
            ) -> None:
                world = World(harness)
                world.create_destination_conflict(destination, "file")
                world.install(choice, slug, destination, False)

            @common_settings
            @given(choice_strategy, slug_strategy, destination_strategy)
            def remove_cleans_managed_root(
                choice: VersionChoice, slug: str, destination: DestinationSpec
            ) -> None:
                world = World(harness)
                world.install(choice, slug, destination, False)
                world.remove(choice, slug, False, False)

            class CliStateMachine(RuleBasedStateMachine):
                def __init__(self) -> None:
                    super().__init__()
                    self.world = World(harness)

                @rule(
                    choice=choice_strategy,
                    slug=slug_strategy,
                    destination=destination_strategy,
                    force=st.booleans(),
                )
                def install(
                    self,
                    choice: VersionChoice,
                    slug: str,
                    destination: DestinationSpec,
                    force: bool,
                ) -> None:
                    self.world.install(choice, slug, destination, force)

                @rule(
                    choice=choice_strategy,
                    slug=slug_strategy,
                    force=st.booleans(),
                    mismatch=st.booleans(),
                )
                def remove(
                    self,
                    choice: VersionChoice,
                    slug: str,
                    force: bool,
                    mismatch: bool,
                ) -> None:
                    self.world.remove(choice, slug, force, mismatch)

                @rule(
                    destination=destination_strategy,
                    kind=st.sampled_from(
                        (
                            "file",
                            "empty-directory",
                            "directory",
                            "dangling-symlink",
                            "wrong-symlink",
                        )
                    ),
                )
                def create_destination_conflict(
                    self, destination: DestinationSpec, kind: str
                ) -> None:
                    self.world.create_destination_conflict(destination, kind)

                @rule(destination=destination_strategy)
                def user_delete_destination(self, destination: DestinationSpec) -> None:
                    self.world.delete_destination(destination)

                @rule(choice=choice_strategy, slug=slug_strategy)
                def user_delete_root(self, choice: VersionChoice, slug: str) -> None:
                    self.world.delete_root(choice, slug)

                @invariant()
                def filesystem_invariants(self) -> None:
                    self.world.assert_invariants()

            print(
                f"stateful CLI PBT: {len(PACKAGES)} packages, {len(choices)} selectors, "
                f"{examples} examples x {steps} steps on {harness.platform}",
                flush=True,
            )
            install_is_idempotent()
            rejected_destination_conflict_is_atomic()
            remove_cleans_managed_root()
            run_state_machine_as_test(
                CliStateMachine,
                settings=settings(
                    max_examples=examples,
                    stateful_step_count=steps,
                    deadline=None,
                    database=database,
                    suppress_health_check=(HealthCheck.too_slow,),
                ),
            )
    finally:
        if owned_root:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
