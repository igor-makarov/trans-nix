"""Construction and parsing of the command-line grammar."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    """Build the parser without consulting process-global argument state."""
    parser = argparse.ArgumentParser(
        prog="trans-nix",
        description=(
            "Install relocatable nixpkgs binary-cache closures without invoking Nix."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    versions = subparsers.add_parser(
        "list-versions", help="list live indexed versions in ascending order"
    )
    versions.add_argument("package", help="nixpkgs attribute name, e.g. nodejs")
    versions.add_argument("platform", help="indexed Nix system")
    versions.add_argument(
        "--nix-package-output",
        help="named output instead of the NixHub default",
    )
    versions.add_argument("--json", action="store_true", help="emit one JSON array")

    install = subparsers.add_parser(
        "install", help="resolve, download, relocate, and link a package closure"
    )
    install.add_argument("package", help="nixpkgs attribute name, e.g. nodejs")
    install.add_argument("version", help="exact version, numeric prefix, or latest")
    install.add_argument("platform", help="indexed Nix system")
    install.add_argument(
        "install_to_path",
        metavar="install-to-path",
        type=Path,
        help="installation symlink path to create",
    )
    install.add_argument(
        "--short-storage-slug",
        help="directory name beneath $HOME/.tn (default: package name)",
    )
    install.add_argument(
        "--nix-package-output",
        help="named output instead of the NixHub default",
    )
    install.add_argument(
        "--force", action="store_true", help="replace mismatched roots or links"
    )
    install.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=16,
        help="parallel metadata and processing workers (default: %(default)s)",
    )

    remove = subparsers.add_parser(
        "remove", help="remove a persistent relocated package root"
    )
    remove.add_argument("package", help="nixpkgs attribute name")
    remove.add_argument("version", help="exact resolved version")
    remove.add_argument("platform", help="manifest Nix system")
    remove.add_argument(
        "--short-storage-slug",
        help="directory name beneath $HOME/.tn (default: package name)",
    )
    remove.add_argument(
        "--nix-package-output", help="named output recorded in the manifest"
    )
    remove.add_argument(
        "--force", action="store_true", help="remove even if the manifest mismatches"
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)
