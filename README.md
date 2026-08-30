# trans-nix

`trans-nix` is a [mise backend plugin](https://mise.jdx.dev/backend-plugin-development.html) that translates the embedded `/nix/store` paths in nixpkgs closures to a relocatable, user-owned layout—without installing or invoking Nix.

It resolves package versions from [nixpkgs-multiverse](https://github.com/fzakaria/nixpkgs-multiverse), downloads complete closures directly from `cache.nixos.org`, verifies archive and NAR hashes, and rewrites their store paths as it extracts them beneath `$HOME/.tn`.

It does **not**:

- install or execute `nix`;
- evaluate derivations, flakes, or nixpkgs;
- require `/nix/store`;
- use a mount namespace, `proot`, or `CAP_SYS_USER_NS`.

## Supported platforms

The plugin supports every platform currently published by the nixmultiverse site index:

- `x86_64-linux`
- `aarch64-linux`
- `aarch64-darwin`

Intel macOS is not indexed and is rejected.

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

The plugin declares `python` as a mise dependency, so a configured Python is ordered before trans-nix and added to its hook environment. No per-tool `depends` option is needed. Plugin metadata does not implicitly select a Python version, so configure Python 3.14+ as shown above. Python 3.14 provides the standard-library Zstandard decompressor needed by modern cache archives. Version listing uses mise's built-in Lua HTTP client so it can run before Python has been installed.

### Configuration

```toml
[plugins]
trans-nix = "https://github.com/igor-makarov/trans-nix"

[tools]
python = "3.14"
"trans-nix:nodejs" = { version = "24", jobs = 16 }
```

Supported tool options:

- `jobs` — positive integer controlling parallel narinfo fetches, downloads, extraction, rewriting, and signing; default `16`.
- `force` — replace an existing relocated root or mise link when its manifest does not match; default `false`.

## Relocated layout

A resolved `nodejs@24.14.0` is stored as:

```text
$HOME/.tn/nodejs/24.14.0/
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
~/.local/share/mise/installs/.../24.14.0
  -> ~/.tn/nodejs/24.14.0
```

Dependencies are sorted by complete source store basename and assigned deterministic, zero-padded hexadecimal counters. The counter width is computed before any archive is downloaded:

```text
counter width = 38 - byte_length($HOME/.tn/<package>/<resolved-version>)
capacity      = 16 ** counter_width
```

For example, `/Users/igor/.tn/nodejs/24.14.0` is 30 bytes, producing eight-digit counters. If the absolute root is too long or its counter cannot represent the closure, installation fails during metadata discovery.

Every replacement has exactly the same byte length as its original store path:

```text
/nix/store/<hash>-glibc-2.40
→ $HOME/.tn/nodejs/24.14.0/.tn/00000000-glibc-2.40
```

References to the root package are rewritten to the root directory and padded with redundant trailing `/` separators. Rewriting occurs while each NAR payload is streamed, including binary files, scripts, compiled data, and symlink targets. Modified Mach-O binaries are ad-hoc re-signed on macOS.

The root package may not already contain a `.tn` entry because that name is reserved for its relocated dependencies.

## Persistent roots and removal

`mise uninstall` removes mise's symlink, but backend plugins do not currently receive an uninstall hook. Relocated roots intentionally remain as a persistent cache.

Remove one explicitly with the internal CLI boundary:

```sh
/path/to/plugin/bin/trans-nix remove nodejs 24.14.0 \
  --platform aarch64-darwin
```

This can leave dangling mise links, so normally run it after `mise uninstall`.

## CLI boundary

The Python implementation is encapsulated in `bin/trans-nix`. It is primarily the boundary used by the Lua plugin hooks, not a separately installed global command.

```sh
# Ascending live versions
bin/trans-nix list-versions nodejs --platform aarch64-darwin
bin/trans-nix list-versions nodejs --platform aarch64-darwin --json

# Install beneath $HOME/.tn and create the requested link
bin/trans-nix install nodejs 24 /absolute/path/to/mise-install \
  --platform aarch64-darwin --jobs 16

# Replace a mismatched existing root or link
bin/trans-nix install nodejs latest /absolute/path/to/mise-install \
  --platform aarch64-darwin --force
```

`VERSION` may be exact, a numeric prefix such as `24` or `24.14`, or `latest`. `--platform` is always explicit. Direct CLI use requires Python 3.14+.

For fixture testing, the CLI's endpoints can be overridden with `TRANS_NIX_SITE_BASE` and `TRANS_NIX_CACHE_BASE`.

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
