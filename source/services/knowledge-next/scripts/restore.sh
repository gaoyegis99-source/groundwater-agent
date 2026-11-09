#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
project_root=$(cd "$root/../../.." && pwd)
backup=${1:?Usage: restore.sh BACKUP.dump NEW_COMPOSE_PROJECT}
project=${2:?A new project is required}
[[ "$project" =~ ^[a-z0-9][a-z0-9_-]+$ ]] || exit 1
instance_root=${PLATFORM_INSTANCE_DATA_ROOT:?required}
database_root="$instance_root/knowledge/database"
[[ -d "$database_root" && ! -L "$database_root" ]] || {
  echo 'Restore database bind root is missing or invalid' >&2; exit 1;
}
if find "$database_root" -mindepth 1 -print -quit | read -r _; then
  echo 'Refusing to restore into a non-empty database bind root' >&2; exit 1
fi
sha256sum -c "$backup.sha256"
compose=("$project_root/bin/compose" -p "$project" -f "$root/compose.yaml")
if [[ -n "${KNOWLEDGE_COMPOSE_OVERRIDE:-}" ]]; then
  compose+=(-f "$KNOWLEDGE_COMPOSE_OVERRIDE")
fi
"${compose[@]}" up -d --wait knowledge-db
"${compose[@]}" exec -T knowledge-db pg_restore --exit-on-error -U knowledge -d knowledge < "$backup"
"${compose[@]}" run --rm --no-deps migrate
echo "Restored into $project. Start API/Worker with a free KNOWLEDGE_PORT and the original project/model configuration."
