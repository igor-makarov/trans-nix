from __future__ import annotations

import errno
import json
import os
import platform as host_platform
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


class Environment:
    def __init__(self) -> None:
        configured_root = os.environ.get("TRANS_NIX_TEST_ROOT")
        if configured_root:
            self.root = Path(configured_root)
            self.root.mkdir(parents=True, exist_ok=True)
        else:
            self.root = Path(tempfile.mkdtemp(prefix="e", dir="/tmp"))

        self.repo = Path(
            os.environ.get("TRANS_NIX_REPO", Path(__file__).resolve().parents[1])
        ).resolve()
        self.home = self.root
        self.config_dir = self.home / "config"
        self.project_dir = self.home / "project"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.project_dir.mkdir(parents=True, exist_ok=True)

        self.python_version = os.environ.get("TRANS_NIX_PYTHON_VERSION", "3.14")
        python_seed = os.environ.get("TRANS_NIX_PYTHON_SEED")
        if python_seed:
            versions = list(Path(python_seed).iterdir())
            if len(versions) != 1:
                raise AssertionError(
                    f"expected one seeded Python version, found {len(versions)}"
                )
            seeded_python = versions[0]
            self.python_version = seeded_python.name
            install_parent = self.home / "mise" / "installs" / "python"
            install_parent.mkdir(parents=True, exist_ok=True)
            (install_parent / self.python_version).symlink_to(
                seeded_python.resolve(), target_is_directory=True
            )

        self.mise = os.environ.get("TRANS_NIX_MISE") or shutil.which("mise")
        if not self.mise:
            raise AssertionError("mise is not available")

        self.platform = detect_platform()
        base_path = os.environ.get("TRANS_NIX_BASE_PATH", os.environ["PATH"])
        self.env = os.environ.copy()
        self.env.update(
            {
                "HOME": str(self.home),
                "MISE_DATA_DIR": str(self.home / "mise"),
                "MISE_CONFIG_DIR": str(self.config_dir),
                "MISE_CACHE_DIR": str(self.home / "cache"),
                "MISE_STATE_DIR": str(self.home / "state"),
                "MISE_PYTHON_GITHUB_ATTESTATIONS": "false",
                "MISE_USE_VERSIONS_HOST": "0",
                "MISE_YES": "1",
                "PATH": f"{self.home / 'mise' / 'shims'}{os.pathsep}{base_path}",
                "PYTHONDONTWRITEBYTECODE": "1",
                "TMPDIR": str(self.root / "tmp"),
            }
        )
        self.env.pop("VIRTUAL_ENV", None)
        Path(self.env["TMPDIR"]).mkdir(parents=True, exist_ok=True)

        self._verify_host_preconditions()
        self._verify_seatbelt_write_isolation()

    @property
    def config_path(self) -> Path:
        return self.config_dir / "config.toml"

    def _verify_host_preconditions(self) -> None:
        if shutil.which("nix", path=self.env["PATH"]) or Path("/nix/store").exists():
            raise AssertionError("E2E environment must not provide Nix or /nix/store")
        if os.environ.get("TRANS_NIX_EXPECT_NO_PYTHON") == "1" and shutil.which(
            "python3", path=self.env["PATH"]
        ):
            raise AssertionError(
                "Docker E2E unexpectedly has python3 on PATH before mise installs dependencies"
            )

    def _verify_seatbelt_write_isolation(self) -> None:
        if os.environ.get("TRANS_NIX_EXPECT_SEATBELT") != "1":
            return
        probe = self.repo / ".seatbelt-write-probe"
        try:
            probe.touch(exist_ok=False)
        except OSError as error:
            if error.errno not in (errno.EACCES, errno.EPERM):
                raise
        else:
            probe.unlink()
            raise AssertionError(
                "Seatbelt unexpectedly allowed writes to the repository"
            )

    def configure(self, contents: str) -> None:
        self.config_path.write_text(contents.strip() + "\n")

    def link_plugin(self) -> None:
        self.run(self.mise, "plugin", "link", "--force", "trans-nix", self.repo)

    def run(self, *command: os.PathLike[str] | str, capture: bool = False) -> str:
        args = [os.fspath(part) for part in command]
        result = subprocess.run(
            args,
            cwd=self.project_dir,
            env=self.env,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            check=False,
        )
        if result.returncode:
            if capture:
                print(result.stdout, end="")
                print(result.stderr, end="", file=os.sys.stderr)
            raise subprocess.CalledProcessError(result.returncode, args)
        return result.stdout.strip() if capture else ""

    def mise_output(self, *args: str) -> str:
        return self.run(self.mise, *args, capture=True)

    def install_and_reshim(self) -> None:
        self.run(self.mise, "install")
        self.run(self.mise, "reshim")

    def verify_install(
        self,
        tool: str,
        slug: str,
        version: str,
        *,
        package: str | None = None,
        nix_package_output: str | None = None,
    ) -> tuple[Path, dict[str, Any]]:
        install_path = Path(self.mise_output("where", f"{tool}@{version}"))
        expected = self.home / ".tn" / slug / version
        if not install_path.is_symlink():
            raise AssertionError(f"mise install path is not a symlink: {install_path}")
        if Path(os.readlink(install_path)) != expected:
            raise AssertionError(f"{install_path} does not link to {expected}")

        manifest_path = expected / ".nix-closure-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        if manifest["format"] != 4:
            raise AssertionError(f"unexpected manifest format: {manifest['format']}")
        if manifest["platform"] != self.platform:
            raise AssertionError(
                f"expected platform {self.platform}, got {manifest['platform']}"
            )
        if manifest["shortStorageSlug"] != slug:
            raise AssertionError(
                f"expected storage slug {slug}, got {manifest['shortStorageSlug']}"
            )
        if manifest["rewriteStats"]["exactRewrites"] <= 0:
            raise AssertionError("installation performed no exact rewrites")
        if not (expected / ".tn").is_dir():
            raise AssertionError(f"translated closure is missing: {expected / '.tn'}")
        if package is not None and manifest["package"] != package:
            raise AssertionError(
                f"expected package {package}, got {manifest['package']}"
            )
        if nix_package_output is not None and (
            manifest["nixPackageOutput"] != nix_package_output
        ):
            raise AssertionError(
                f"expected output {nix_package_output}, "
                f"got {manifest['nixPackageOutput']}"
            )
        return install_path, manifest

    def verify_node(self, selector: str, version: str) -> None:
        install_path, _ = self.verify_install("trans-nix:nodejs", "nodejs", version)
        output = self.run(install_path / "bin/node", "--version", capture=True)
        if not (output == f"v{version}" or output.startswith(f"v{version}-")):
            raise AssertionError(f"expected Node {version}, got {output}")
        self.run(
            install_path / "bin/node",
            "-e",
            "console.log(process.platform, process.arch)",
        )
        print(f"verified nodejs@{selector} -> {version}")

    def verify_weasyprint(self, tool: str, version: str) -> Path:
        install_path, _ = self.verify_install(
            tool,
            "weasyprint",
            version,
            package="python314Packages.weasyprint",
        )
        output = self.run(install_path / "bin/weasyprint", "--version", capture=True)
        if version not in output:
            raise AssertionError(f"expected WeasyPrint {version}, got {output}")
        info = self.run(install_path / "bin/weasyprint", "--info", capture=True)
        if not re.search(r"Python version: 3\.14\.", info):
            raise AssertionError(f"WeasyPrint does not use Python 3.14:\n{info}")
        return install_path


def detect_platform() -> str:
    system = host_platform.system()
    machine = host_platform.machine()
    platforms = {
        ("Linux", "x86_64"): "x86_64-linux",
        ("Linux", "aarch64"): "aarch64-linux",
        ("Linux", "arm64"): "aarch64-linux",
        ("Darwin", "arm64"): "aarch64-darwin",
    }
    try:
        return platforms[(system, machine)]
    except KeyError as error:
        raise AssertionError(f"unsupported E2E host: {system}-{machine}") from error
