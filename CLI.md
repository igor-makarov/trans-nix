# CLI boundary

`bin/trans-nix` is the stable executable used by the Lua plugin hooks, not a separately installed global command. It only bootstraps the implementation under `trans_nix/`, where parsing, command handling, transport, and transformation logic are split by concern.

```sh
# Ascending live versions
bin/trans-nix list-versions nodejs aarch64-darwin
bin/trans-nix list-versions nodejs aarch64-darwin --json
bin/trans-nix list-versions pango aarch64-darwin --nix-package-output out

# Install beneath $HOME/.tn and create the requested installation symlink
bin/trans-nix install nodejs 24 aarch64-darwin /absolute/path/to/mise-install \
  --jobs 16
bin/trans-nix install pango 1.57.1 aarch64-darwin /absolute/path/to/pango-install \
  --nix-package-output out

# Use a short storage directory beneath $HOME/.tn
bin/trans-nix install python314Packages.weasyprint latest aarch64-darwin \
  /absolute/path/to/weasyprint-install --short-storage-slug weasyprint

# Replace a mismatched existing root or installation path
bin/trans-nix install nodejs latest aarch64-darwin /absolute/path/to/mise-install \
  --force
```

`VERSION` may be exact, a numeric prefix such as `24` or `24.11`, or `latest`. `PLATFORM` is positional. `--short-storage-slug NAME` defaults to the package name. `--nix-package-output NAME` selects that named output; without it, the NixHub default is used. Direct CLI use requires Python 3.14+.

For fixture testing, the CLI's endpoints can be overridden with `TRANS_NIX_NIXHUB_BASE` and `TRANS_NIX_CACHE_BASE`.
