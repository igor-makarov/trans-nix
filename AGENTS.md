# Development guide

Do not use `mise exec` or `mise x` in scripts. Development commands rely on normal mise activation and shims.

Set up the development tools with:

```sh
mise install
mise reshim
```

Use these checks:

```sh
mise run lint
mise run test:unit
mise run test:e2e    # native platform integration suite
mise run test:docker # same Linux Docker image used by CI
mise run test:all    # native plus Docker verification
```

CI covers:

- native `aarch64-darwin` on `macos-latest`;
- Docker `x86_64-linux` on `ubuntu-latest`;
- Docker `aarch64-linux` on `ubuntu-24.04-arm`.

The E2E suite must start without Nix or `/nix/store`, cover distinct version selectors and both executable and library-only closures, and install everything through the backend plugin. It must verify that mise paths are symlinks into `$HOME/.tn`, execute translated binaries, and confirm translated libraries exist. The Docker image must start without a system Python so it exercises mise's declared Python dependency and Python 3.14's Zstandard support.
