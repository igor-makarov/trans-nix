from __future__ import annotations

from support import Environment


def main() -> None:
    version = "2.12.2"
    test = Environment()
    test.configure(
        f"""
        [settings]
        experimental = true

        [tools]
        python = "3.14"
        "trans-nix:hello" = "{version}"
        """
    )
    test.link_plugin()

    # No Python is pre-seeded for this test. A combined install verifies that
    # PLUGIN.depends installs and exposes Python before the backend hook runs.
    test.install_and_reshim()

    install_path, _ = test.verify_install(
        "trans-nix:hello",
        "hello",
        version,
        package="hello",
    )
    output = test.run(install_path / "bin/hello", capture=True)
    if output != "Hello, world!":
        raise AssertionError(f"unexpected hello output: {output}")
    print(f"installed hello@{version} after its declared Python dependency")


if __name__ == "__main__":
    main()
