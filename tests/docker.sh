#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
image="trans-nix-test:$(uname -m)"

docker build --pull --no-cache --file "$repo_dir/tests/Dockerfile" --tag "$image" "$repo_dir"
docker run --rm "$image"
