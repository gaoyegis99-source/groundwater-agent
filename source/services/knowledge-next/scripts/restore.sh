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
# The official PostgreSQL entrypoint briefly starts a temporary server while
# initializing a new data directory. Its healthcheck can pass during that
# window, immediately before the temporary server shuts down. Wait until PID
# 1 has exec'd the final postgres process so pg_restore cannot race that
# hand-off.
"${compose[@]}" exec -T knowledge-db sh -c '
set -eu
attempt=0
while [ "$attempt" -lt 240 ]; do
  if [ "$(cat /proc/1/comm)" = postgres ] \
    && pg_isready -U knowledge -d knowledge >/dev/null 2>&1; then
    exit 0
  fi
  attempt=$((attempt + 1))
  sleep 0.25
done
echo "Final PostgreSQL server did not become ready" >&2
exit 1
'
"${compose[@]}" exec -T knowledge-db pg_restore --exit-on-error -U knowledge -d knowledge < "$backup"
"${compose[@]}" run --rm --no-deps migrate
echo "Restored into $project. Start API/Worker with a free KNOWLEDGE_PORT and the original project/model configuration."
