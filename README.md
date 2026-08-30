# trans-nix

`trans-nix` is a [mise backend plugin](https://mise.jdx.dev/backend-plugin-development.html) that translates the embedded `/nix/store` paths in nixpkgs closures to a relocatable, user-owned layout—without installing or invoking Nix.

It resolves package versions and platform-specific default outputs through [NixHub](https://www.jetify.com/docs/nixhub/), downloads complete closures directly from `cache.nixos.org`, verifies archive and NAR hashes, and rewrites their store paths as it extracts them beneath `$HOME/.tn`.

It does **not**:

- install or execute `nix`;
- evaluate derivations, flakes, or nixpkgs;
- require `/nix/store`;
- use a mount namespace, `proot`, or `CAP_SYS_USER_NS`.

## Supported platforms

The plugin supports these Nix systems:

- `x86_64-linux`
- `aarch64-linux`
- `aarch64-darwin`

Intel macOS is not supported and is rejected.

## Installation

```sh
mise plugin install trans-nix https://github.com/igor-makarov/trans-nix
```

Configure the managed Python explicitly:

```toml
[tools]
python = "3.14"
"trans-nix:nodejs" = "24"
```

Then install and activate normally:

```sh
mise install
node --version
```

The plugin declares `python` as a mise dependency, so a configured Python is ordered before trans-nix and added to its hook environment. No per-tool `depends` option is needed. Plugin metadata does not implicitly select a Python version, so configure Python 3.14+ as shown above. Python 3.14 provides the standard-library Zstandard decompressor needed by modern cache archives. All Lua hooks delegate to the internal Python CLI.

### Configuration

```toml
[plugins]
trans-nix = "https://github.com/igor-makarov/trans-nix"

[tool_alias]
weasyprint = "trans-nix:weasyprint[package='python314Packages.weasyprint']"

[tools]
python = "3.14"
"trans-nix:nodejs" = { version = "24", jobs = 16 }
"trans-nix:pango" = { version = "1.57.1", output = "out" }
weasyprint = "latest"
```

The same package override can be used without a tool alias:

```toml
[tools]
"trans-nix:weasyprint[package='python314Packages.weasyprint']" = "latest"
```

Supported tool options:

- `jobs` — positive integer controlling parallel narinfo fetches, downloads, extraction, rewriting, and signing; default `16`.
- `force` — replace an existing relocated root or mise link when its manifest does not match; default `false`.
- `output` — select a named NixHub output instead of the output marked as default. For example, Pango’s default is `bin`, while its shared libraries are in `out`.
- `package` — query a different NixHub package than the backend tool name. This supports short mise aliases for long nixpkgs attribute paths.
- `install_prefix` — directory name beneath `$HOME/.tn`; defaults to the backend tool name, such as `weasyprint` in the alias above.

## Relocated layout

A resolved `nodejs@24.11.1` is stored as:

```text
$HOME/.tn/nodejs/24.11.1/
├── bin/
├── lib/
├── .tn/
│   ├── 00000000-glibc-2.40/
│   ├── 00000001-zlib-1.3.1/
│   └── ...
└── .nix-closure-manifest.json
```

The mise installation path is a symlink to that persistent root:

```text
~/.local/share/mise/installs/.../24.11.1
  -> ~/.tn/nodejs/24.11.1
```

Dependencies are sorted by complete source store basename and assigned deterministic, zero-padded hexadecimal counters. The counter width is computed before any archive is downloaded:

```text
counter width = 38 - byte_length($HOME/.tn/<install-prefix>/<resolved-version>)
capacity      = 16 ** counter_width
```

For example, `/Users/igor/.tn/nodejs/24.11.1` is 30 bytes, producing eight-digit counters. The absolute root may be at most 37 bytes; a longer `install_prefix` is rejected before closure discovery. Installation also fails if the resulting counter cannot represent the closure.

Every replacement has exactly the same byte length as its original store path:

```text
/nix/store/<hash>-glibc-2.40
→ $HOME/.tn/nodejs/24.11.1/.tn/00000000-glibc-2.40
```

References to the root package are rewritten to the root directory and padded with redundant trailing `/` separators. Rewriting occurs while each NAR payload is streamed, including binary files, scripts, compiled data, and symlink targets. Modified Mach-O binaries are ad-hoc re-signed on macOS.

The root package may not already contain a `.tn` entry because that name is reserved for its relocated dependencies.

## Persistent roots and removal

`mise uninstall` removes mise's symlink, but backend plugins do not currently receive an uninstall hook. Relocated roots intentionally remain as a persistent cache.

Remove one explicitly with the internal CLI boundary:

```sh
/path/to/plugin/bin/trans-nix remove nodejs 24.11.1 \
  --platform aarch64-darwin
```

This can leave dangling mise links, so normally run it after `mise uninstall`.

## CLI boundary

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

## Integrity and security

For every closure member, trans-nix verifies:

- downloaded archive size and `FileHash`;
- decompressed NAR size and `NarHash`;
- narinfo store digest and reference basenames;
- that every encountered store reference belongs to the discovered closure;
- exact byte-length preservation for every rewrite.

Binary-cache signatures are not independently verified. The hashes come from narinfo served by `cache.nixos.org`, so HTTPS trust in that endpoint remains part of the security model.

## Acknowledgments

The path-translation mechanism was inspired by [Ninlives/relocatable.nix](https://github.com/Ninlives/relocatable.nix).

## License

MIT
