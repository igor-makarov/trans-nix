from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
E2E_DIR = Path(__file__).resolve().parent
TESTS = sorted(E2E_DIR.glob("test_*.py"))
COLD_TEST = "test_cold.py"


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    print(f"+ {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=ROOT, env=env, check=True)


@contextmanager
def measure(label: str) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - started
        print(f"TIMING {label}: {elapsed:.2f}s", flush=True)


def native() -> None:
    if platform.system() != "Darwin":
        raise SystemExit("native E2E isolation requires macOS Seatbelt; use test:docker")
    sandbox_exec = shutil.which("sandbox-exec")
    mise = shutil.which("mise")
    if not sandbox_exec or not mise:
        raise SystemExit("native E2E requires sandbox-exec and mise")

    with measure("prebuild"):
        sandbox_roots = Path("/tmp/pi")
        sandbox_roots.mkdir(parents=True, exist_ok=True)
        git = subprocess.run(
            ["xcrun", "--find", "git"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        seeded_python = Path(
            subprocess.run(
                [mise, "where", "python@3.14"],
                check=True,
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip()
        )
        if not seeded_python.is_dir():
            raise SystemExit(f"mise Python seed is missing: {seeded_python}")

    for test in TESTS:
        with measure(f"test {test.name}"):
            root = Path(tempfile.mkdtemp(prefix="", dir=sandbox_roots))
            try:
                (root / "tmp").mkdir()
                (root / "bin").mkdir()
                (root / "bin/git").symlink_to(git)
                if test.name != COLD_TEST:
                    install_parent = root / "mise" / "installs" / "python"
                    install_parent.mkdir(parents=True)
                    run(
                        [
                            "/bin/cp",
                            "-cR",
                            str(seeded_python),
                            str(install_parent / seeded_python.name),
                        ]
                    )
                sandbox_root = root.resolve()
                profile = root / "seatbelt.sb"
                profile.write_text(
                    "\n".join(
                        [
                            "(version 1)",
                            "(allow default)",
                            "(deny file-write*)",
                            f"(allow file-write* (subpath {json.dumps(str(sandbox_root))}))",
                            '(allow file-write* (subpath "/dev"))',
                            "",
                        ]
                    )
                )
                env = os.environ.copy()
                env.update(
                    {
                        "TRANS_NIX_TEST_ROOT": str(root),
                        "TRANS_NIX_REPO": str(ROOT),
                        "TRANS_NIX_MISE": mise,
                        "TRANS_NIX_EXPECT_SEATBELT": "1",
                        "TRANS_NIX_PYTHON_VERSION": (
                            "3.14" if test.name == COLD_TEST else seeded_python.name
                        ),
                        "TRANS_NIX_BASE_PATH": (
                            f"{root / 'bin'}:/usr/bin:/bin:/usr/sbin:/sbin"
                        ),
                        "PYTHONDONTWRITEBYTECODE": "1",
                        "TMPDIR": str(root / "tmp"),
                    }
                )
                print(f"\n=== {test.name} (Seatbelt root: {root}) ===", flush=True)
                run(
                    [
                        sandbox_exec,
                        "-f",
                        str(profile),
                        sys.executable,
                        "-B",
                        str(test),
                    ],
                    env=env,
                )
            finally:
                shutil.rmtree(root, ignore_errors=True)


def docker() -> None:
    docker_bin = shutil.which("docker")
    if not docker_bin:
        raise SystemExit("Linux E2E requires Docker")
    architecture = platform.machine().lower()
    cold_image = f"trans-nix-e2e:cold-{architecture}"
    warm_image = f"trans-nix-e2e:warm-{architecture}"
    with measure("prebuild"):
        run(
            [
                docker_bin,
                "build",
                "--pull",
                "--no-cache",
                "--target",
                "cold",
                "--file",
                str(E2E_DIR / "Dockerfile"),
                "--tag",
                cold_image,
                str(ROOT),
            ]
        )
        run(
            [
                docker_bin,
                "build",
                "--target",
                "warm",
                "--file",
                str(E2E_DIR / "Dockerfile"),
                "--tag",
                warm_image,
                str(ROOT),
            ]
        )
    for test in TESTS:
        with measure(f"test {test.name}"):
            image = cold_image if test.name == COLD_TEST else warm_image
            print(
                f"\n=== {test.name} (ephemeral {image} container) ===",
                flush=True,
            )
            run(
                [
                    docker_bin,
                    "run",
                    "--rm",
                    image,
                    f"/work/e2e/{test.name}",
                ]
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("native", "docker"))
    args = parser.parse_args()
    if not TESTS:
        raise SystemExit("no E2E tests found")
    with measure("total"):
        if args.mode == "native":
            native()
        else:
            docker()


if __name__ == "__main__":
    main()
