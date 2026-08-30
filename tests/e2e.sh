#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
case "$(uname -s)-$(uname -m)" in
    Linux-x86_64) platform=x86_64-linux ;;
    Linux-aarch64 | Linux-arm64) platform=aarch64-linux ;;
    Darwin-arm64) platform=aarch64-darwin ;;
    *)
        echo "unsupported E2E host: $(uname -s)-$(uname -m)" >&2
        exit 1
        ;;
esac

if command -v nix >/dev/null 2>&1 || [[ -e /nix/store ]]; then
    echo "E2E environment must not provide Nix or /nix/store" >&2
    exit 1
fi
if [[ ${TRANS_NIX_EXPECT_NO_PYTHON:-0} == 1 ]] && command -v python3 >/dev/null 2>&1; then
    echo "Docker E2E unexpectedly has a system python3 before mise installs dependencies" >&2
    exit 1
fi

export HOME="/tmp/tn-h-$$"
export MISE_DATA_DIR="$HOME/m"
export MISE_CONFIG_DIR="$HOME/c"
export MISE_CACHE_DIR="$HOME/cache"
export MISE_STATE_DIR="$HOME/state"
export MISE_USE_VERSIONS_HOST=0
export MISE_YES=1
export PATH="$MISE_DATA_DIR/shims:$PATH"
trap 'rm -rf "$HOME"' EXIT
project_dir="$HOME/project"
mkdir -p "$MISE_CONFIG_DIR" "$project_dir"
cd "$project_dir"
cat >"$MISE_CONFIG_DIR/config.toml" <<'EOF'
[settings]
experimental = true

[tools]
python = "3.14"
"trans-nix:nodejs" = "24.14"
EOF

mise plugin link --force trans-nix "$repo_dir"
# This combined install verifies that PLUGIN.depends alone orders Python first
# and exposes it to the backend hook; no per-tool depends option is configured.
mise install
mise reshim
cli_versions=$("$repo_dir/bin/trans-nix" list-versions nodejs --platform "$platform" --json)
[[ $cli_versions == \[* ]]
versions=$(mise ls-remote trans-nix:nodejs)
[[ -n $versions ]]
version_24_14=$(mise latest 'trans-nix:nodejs@24.14')
version_latest=$(mise latest 'trans-nix:nodejs')
[[ $version_24_14 == 24.14.* ]]
[[ -n $version_latest ]]
[[ $version_24_14 != "$version_latest" ]]
printf 'resolved nodejs@24.14 -> %s\n' "$version_24_14"
printf 'resolved nodejs@latest -> %s\n' "$version_latest"

verify_install() {
    local selector=$1
    local version=$2
    local install_path expected output

    cat >"$MISE_CONFIG_DIR/config.toml" <<EOF
[settings]
experimental = true

[tools]
python = "3.14"
"trans-nix:nodejs" = "$selector"
EOF
    mise install "trans-nix:nodejs@$selector"
    mise reshim
    install_path=$(mise where "trans-nix:nodejs@$version")
    expected="$HOME/.tn/nodejs/$version"

    [[ -L $install_path ]]
    [[ $(readlink "$install_path") == "$expected" ]]
    [[ -f $expected/.nix-closure-manifest.json ]]
    [[ -d $expected/.tn ]]
    grep -q '"format": 3' "$expected/.nix-closure-manifest.json"
    grep -q "\"platform\": \"$platform\"" "$expected/.nix-closure-manifest.json"
    grep -Eq '"exactRewrites": [1-9][0-9]*' "$expected/.nix-closure-manifest.json"

    output=$("$install_path/bin/node" --version)
    [[ $output == "v$version" ]]
    "$install_path/bin/node" -e 'console.log(process.platform, process.arch)'
}

verify_install 24.14 "$version_24_14"
verify_install latest "$version_latest"

python_path=$(mise where python@3.14)
"$python_path/bin/python3" --version
python3 -m unittest discover -s "$repo_dir/tests" -v
printf 'trans-nix E2E passed on %s\n' "$platform"
