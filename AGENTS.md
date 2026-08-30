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
mise run test:e2e    # native platform; downloads and executes nodejs@24.14 and @latest
mise run test:linux  # same Linux Docker image used by CI
mise run test:all    # native plus Docker verification
```

CI covers:

- native `aarch64-darwin` on `macos-latest`;
- Docker `x86_64-linux` on `ubuntu-latest`;
- Docker `aarch64-linux` on `ubuntu-24.04-arm`.

The E2E suite must start without Nix or `/nix/store`, resolve distinct versions for `nodejs@24.14` and `nodejs@latest`, install them through the backend plugin, verify the mise paths are symlinks into `$HOME/.tn`, and execute each installed Node.js binary. The Docker image must also start without a system Python so it exercises mise's declared Python dependency and Python 3.14's Zstandard support.
