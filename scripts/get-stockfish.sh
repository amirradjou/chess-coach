#!/usr/bin/env bash
# Download the official Stockfish Linux build into ./.stockfish/stockfish (no root needed).
# Usage: scripts/get-stockfish.sh [release-tag]   e.g. scripts/get-stockfish.sh sf_19
set -euo pipefail

version="${1:-latest}"
arch="$(uname -m)"
case "$arch" in
  x86_64) arch="x86-64" ;;
  aarch64 | arm64) arch="arm64" ;;
  *)
    echo "Unsupported architecture: $arch" >&2
    exit 1
    ;;
esac
# The "universal" build picks the best instruction set (AVX2/AVX-512/...) at runtime.
asset="stockfish-linux-${arch}-universal.tar.gz"

if [[ "$version" == "latest" ]]; then
  url="https://github.com/official-stockfish/Stockfish/releases/latest/download/${asset}"
else
  url="https://github.com/official-stockfish/Stockfish/releases/download/${version}/${asset}"
fi

dest_dir="$(cd "$(dirname "$0")/.." && pwd)/.stockfish"
mkdir -p "$dest_dir"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "Downloading ${url}"
curl -fsSL "$url" -o "$tmp/$asset"
tar -xzf "$tmp/$asset" -C "$tmp"
bin="$(find "$tmp" -type f -name "stockfish*" ! -name "*.tar.gz" -perm -u+x | head -n 1)"
if [[ -z "$bin" ]]; then
  echo "Could not find the engine binary inside the archive" >&2
  exit 1
fi
install -m 0755 "$bin" "$dest_dir/stockfish"
echo "Installed $(echo uci | "$dest_dir/stockfish" | head -n 1) -> $dest_dir/stockfish"
