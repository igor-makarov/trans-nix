# CLI boundary

The Python implementation is encapsulated in `bin/trans-nix`. It is primarily the boundary used by the Lua plugin hooks, not a separately installed global command.

```sh
# Ascending live versions
bin/trans-nix list-versions nodejs --platform aarch64-darwin
bin/trans-nix list-versions nodejs --platform aarch64-darwin --json
bin/trans-nix list-versions pango --platform aarch64-darwin --output out

# Install beneath $HOME/.tn and create the requested link
bin/trans-nix install nodejs 24 /absolute/path/to/mise-install \
  --platform aarch64-darwin --jobs 16
bin/trans-nix install pango 1.57.1 /absolute/path/to/pango-install \
  --platform aarch64-darwin --output out

# Replace a mismatched existing root or link
bin/trans-nix install nodejs latest /absolute/path/to/mise-install \
  --platform aarch64-darwin --force
```

`VERSION` may be exact, a numeric prefix such as `24` or `24.11`, or `latest`. `--platform` is required. `--install-prefix NAME` defaults to the package name. `--output NAME` selects that named output; without it, the NixHub default is used. Direct CLI use requires Python 3.14+.

For fixture testing, the CLI's endpoints can be overridden with `TRANS_NIX_NIXHUB_BASE` and `TRANS_NIX_CACHE_BASE`.
