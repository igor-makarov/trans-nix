from __future__ import annotations

from support import Environment


def main() -> None:
    version = "1.57.1"
    test = Environment()
    test.configure(
        f"""
        [settings]
        experimental = true

        [tools]
        python = "{test.python_version}"
        "trans-nix:pango" = {{ version = "{version}", nix-package-output = "out" }}
        """
    )
    test.link_plugin()
    test.install_and_reshim()

    install_path, _ = test.verify_install(
        "trans-nix:pango",
        "pango",
        version,
        nix_package_output="out",
    )
    libraries = list((install_path / "lib").glob("libpango-1.0*"))
    if not libraries:
        raise AssertionError("translated Pango library is missing")

    python_path = test.mise_output("where", "python@3.14")
    test.run(
        f"{python_path}/bin/python3",
        "-c",
        """
import ctypes
import sys

pango = ctypes.CDLL(sys.argv[1])
pango.pango_version_string.restype = ctypes.c_char_p
actual = pango.pango_version_string().decode()
if actual != sys.argv[2]:
    raise SystemExit(f"expected Pango {sys.argv[2]}, got {actual}")
print(f"loaded Pango {actual}")
        """,
        libraries[0],
        version,
    )


if __name__ == "__main__":
    main()
