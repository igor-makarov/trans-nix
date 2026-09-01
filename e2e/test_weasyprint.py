from __future__ import annotations

import os
from pathlib import Path

from support import Environment


def main() -> None:
    test = Environment()
    test.configure(
        f"""
        [settings]
        experimental = true

        [tool_alias]
        weasyprint = "trans-nix:weasyprint[nix-package='python314Packages.weasyprint']"

        [tools]
        python = "{test.python_version}"
        weasyprint = "latest"
        """
    )
    test.link_plugin()
    # Alias resolution asks the backend for versions before mise can apply the
    # plugin dependency ordering, so bootstrap the declared Python dependency.
    test.run(test.mise, "install", f"python@{test.python_version}")
    test.run(test.mise, "reshim")
    test.install_and_reshim()

    version = test.mise_output("latest", "weasyprint")
    if not version:
        raise AssertionError("latest WeasyPrint selector returned no version")
    alias_path = test.verify_weasyprint("weasyprint", version)

    html = test.project_dir / "weasyprint.html"
    pdf = test.project_dir / "weasyprint.pdf"
    html.write_text("<h1>trans-nix</h1>\n")
    test.run(alias_path / "bin/weasyprint", html, pdf)
    if pdf.read_bytes()[:5] != b"%PDF-":
        raise AssertionError("WeasyPrint did not produce a PDF")

    test.configure(
        f"""
        [settings]
        experimental = true

        [tools]
        python = "{test.python_version}"
        "trans-nix:weasyprint[nix-package='python314Packages.weasyprint']" = "latest"
        """
    )
    test.run(test.mise, "install")
    direct_path = Path(test.mise_output("where", f"trans-nix:weasyprint@{version}"))
    expected = test.home / ".tn" / "weasyprint" / version
    if not direct_path.is_symlink() or Path(os.readlink(direct_path)) != expected:
        raise AssertionError(f"direct WeasyPrint install does not link to {expected}")
    if direct_path == alias_path:
        raise AssertionError(
            "alias and direct syntax unexpectedly used the same mise path"
        )
    output = test.run(direct_path / "bin/weasyprint", "--version", capture=True)
    if version not in output:
        raise AssertionError(f"expected WeasyPrint {version}, got {output}")

    print(
        f"rendered WeasyPrint {version} with Python 3.14 using alias and direct syntax"
    )


if __name__ == "__main__":
    main()
