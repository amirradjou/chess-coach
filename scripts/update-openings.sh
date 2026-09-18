#!/usr/bin/env bash
# Refresh the vendored opening table from lichess-org/chess-openings (CC0).
set -euo pipefail
dest="$(cd "$(dirname "$0")/.." && pwd)/src/chess_coach/data/openings.tsv"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
{
  echo "# Source: https://github.com/lichess-org/chess-openings (CC0-1.0). Regenerate: scripts/update-openings.sh"
  printf 'eco\tname\tpgn\n'
  for volume in a b c d e; do
    curl -fsSL "https://raw.githubusercontent.com/lichess-org/chess-openings/master/${volume}.tsv" |
      tail -n +2
  done
} > "$tmp/openings.tsv"
mv "$tmp/openings.tsv" "$dest"
echo "Wrote $(wc -l < "$dest") lines to $dest"
