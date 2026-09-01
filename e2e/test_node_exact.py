from __future__ import annotations

import json

from support import Environment


def main() -> None:
    test = Environment()
    test.configure(
        f"""
        [settings]
        experimental = true

        [tools]
        python = "{test.python_version}"
        "trans-nix:nodejs" = "24.11"
        """
    )
    test.link_plugin()
    test.install_and_reshim()

    cli_versions = json.loads(
        test.run(
            test.repo / "bin/trans-nix",
            "list-versions",
            "nodejs",
            test.platform,
            "--json",
            capture=True,
        )
    )
    if not cli_versions:
        raise AssertionError("the trans-nix CLI returned no Node.js versions")

    remote_versions = test.mise_output("ls-remote", "trans-nix:nodejs")
    if not remote_versions:
        raise AssertionError("mise returned no remote Node.js versions")

    version = test.mise_output("latest", "trans-nix:nodejs@24.11")
    latest = test.mise_output("latest", "trans-nix:nodejs")
    if version != "24.11.1":
        raise AssertionError(f"expected nodejs@24.11 -> 24.11.1, got {version}")
    if version == latest:
        raise AssertionError("exact and latest Node.js selectors resolved identically")

    test.verify_node("24.11", version)


if __name__ == "__main__":
    main()
