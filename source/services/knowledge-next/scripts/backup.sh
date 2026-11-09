#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
project_root=$(cd "$root/../../.." && pwd)
project=${KNOWLEDGE_COMPOSE_PROJECT:-platform-knowledge-next-v3}
destination=${1:?Usage: backup.sh NEW_FILE.dump}
test ! -e "$destination" || { echo 'Destination already exists' >&2; exit 1; }
compose=("$project_root/bin/compose" -p "$project" -f "$root/compose.yaml")
if [[ -n "${KNOWLEDGE_COMPOSE_OVERRIDE:-}" ]]; then
  compose+=(-f "$KNOWLEDGE_COMPOSE_OVERRIDE")
fi
mapfile -t running < <("${compose[@]}" ps --services --status running | rg '^(api|worker)$' || true)
resume() { if ((${#running[@]})); then "${compose[@]}" restart "${running[@]}"; fi; }
trap resume EXIT
if ((${#running[@]})); then "${compose[@]}" stop "${running[@]}"; fi
"${compose[@]}" exec -T knowledge-db pg_dump -U knowledge -d knowledge -Fc > "$destination.partial"
mv "$destination.partial" "$destination"
sha256sum "$destination" > "$destination.sha256"
