from __future__ import annotations

from support import Environment


def main() -> None:
    test = Environment()
    test.configure(
        f"""
        [settings]
        experimental = true

        [tools]
        python = "{test.python_version}"
        "trans-nix:nodejs" = "latest"
        """
    )
    test.link_plugin()
    test.install_and_reshim()

    version = test.mise_output("latest", "trans-nix:nodejs")
    if not version:
        raise AssertionError("latest Node.js selector returned no version")
    if version == "24.11.1":
        raise AssertionError("latest Node.js selector did not differ from 24.11")
    test.verify_node("latest", version)


if __name__ == "__main__":
    main()
