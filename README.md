# trans-nix

`trans-nix` is a [mise backend plugin](https://mise.jdx.dev/backend-plugin-development.html) that works by translating the embedded `/nix/store` paths in nixpkgs closures to a relocatable, user-owned layout—without installing or invoking Nix.

It resolves packages through [NixHub](https://www.jetify.com/docs/nixhub/), downloads them from `cache.nixos.org` and rewrites their store paths as it extracts them into `$HOME/.tn`.

**It does not** install or execute `nix`.

**It does not** evaluate derivations, flakes, or nixpkgs.

**It does not** require access to `/nix/store`.

## Supported platforms

- `x86_64-linux`
- `aarch64-linux`
- `aarch64-darwin`

## Installation

```toml
[tools]
# the plugin requires Python 3.14+
python = "latest"

[plugins]
trans-nix = "https://github.com/igor-makarov/trans-nix#1.1.0"
```

### Configuration

```toml
[plugins]
trans-nix = "https://github.com/igor-makarov/trans-nix"

[tool_alias]
# long package name tool alias
weasyprint = "trans-nix:weasyprint[nix-package='python314Packages.weasyprint']"

[tools]
python = "latest"
# simple version
"trans-nix:nodejs" = "24"
# specific derivation output
"trans-nix:pango" = { version = "1.57.1", nix-package-output = "out" }
# use tool alias above
weasyprint = "latest"
# long package name without a tool alias
"trans-nix:weasyprint[nix-package='python314Packages.weasyprint']" = "latest"
```

Supported tool options:

- `nix-package` — query a different NixHub package than the backend tool name. This supports short mise aliases for long nixpkgs attribute paths.
- `nix-package-output` — select a named NixHub output instead of the output marked as default. For example, Pango’s default is `bin`, while its shared libraries are in `out`.
- `short-storage-slug` — directory name beneath `$HOME/.tn`; defaults to the backend tool name, such as `weasyprint` in the alias above.

The backend uses the CLI's default worker count and always permits replacement when mise requests an installation. Mise is responsible for deciding whether an installation is fresh.

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
counter width = 38 - byte_length($HOME/.tn/<short-storage-slug>/<resolved-version>)
capacity      = 16 ** counter_width
```

For example, `/Users/igor/.tn/nodejs/24.11.1` is 30 bytes, producing eight-digit counters. The absolute root may be at most 37 bytes; a longer `short-storage-slug` is rejected before closure discovery. Installation also fails if the resulting counter cannot represent the closure.

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
/path/to/plugin/bin/trans-nix remove nodejs 24.11.1 aarch64-darwin
```

This can leave dangling mise links, so normally run it after `mise uninstall`.

The internal command is documented in [CLI.md](CLI.md).

## Integrity and security

For every closure member, trans-nix verifies:

- decompressed NAR size and `NarHash`;
- narinfo store digest and reference basenames;
- that every encountered store reference belongs to the discovered closure;
- exact byte-length preservation for every rewrite.

The compressed `FileSize` and `FileHash` are not validity checks because a cache may recompress an unchanged NAR without updating those transport fields. Binary-cache signatures are not independently verified. The NAR hashes come from narinfo served by `cache.nixos.org`, so HTTPS trust in that endpoint remains part of the security model.

## Acknowledgments

The path-translation mechanism was inspired by [Ninlives/relocatable.nix](https://github.com/Ninlives/relocatable.nix).

## License

MIT
