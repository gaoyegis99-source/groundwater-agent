#!/usr/bin/env bash
set -euo pipefail

if ! command -v rg >/dev/null 2>&1; then
  echo 'ripgrep (rg) is required for boundary checks' >&2
  exit 127
fi

python -m scripts.checks.check_core_imports
python -m scripts.checks.check_naming
python -m scripts.checks.check_retired_versions
python -m scripts.checks.check_configuration_boundaries

current_roots=(
  kernel
  services/data
  services/executor
  services/gateway
  services/knowledge-next
  products
  domains
)

# The pinned upstream syscall name map_shadow_stack is not compatibility logic.
# Only its checksum-verified JSON profile is excluded; service code remains scanned.
if rg -n 'AGENT_MODE\b|agent_mode\b|workflowExecution|WORKFLOW_STORAGE_MODE|MIRROR' \
  "${current_roots[@]}" --glob '!**/docs/**' \
  --glob '!services/data/deploy/seccomp.json'; then
  echo 'forbidden compatibility logic found' >&2
  exit 1
fi

# CSS box-shadow/text-shadow are presentation, not shadow runtime/state logic.
if rg -n '\bshadow\b' "${current_roots[@]}" --glob '!**/docs/**' --glob '!**/*.css' \
  --glob '!services/data/deploy/seccomp.json'; then
  echo 'forbidden compatibility logic found' >&2
  exit 1
fi

if rg -n '/api/v6(?:/|\b)' "${current_roots[@]}" \
  --glob '!**/tests/**'; then
  echo 'runtime v6 API found' >&2
  exit 1
fi

if rg -n '/api/v7(?:/|\b)' "${current_roots[@]}" \
  --glob '!**/tests/**'; then
  echo 'retired v7 runtime surface found' >&2
  exit 1
fi

if rg -n '\b(search_resources|inspect_resource|inspect_resource_table|list_resources|search_knowledge|read_domain_knowledge|list_knowledge_documents|list_knowledge_chunks)\b' \
  tests/e2e/compose-stub \
  tests/contracts/test_agent_evaluator.py \
  tests/contracts/test_fixed_provider_wire.py; then
  echo 'retired v8 observation tool found in current acceptance harness' >&2
  exit 1
fi

if test -e services/Moperators; then
  echo 'retired Moperators service tree found' >&2
  exit 1
fi

if rg -n -i 'fastapi|sqlalchemy|postgres|redis|minio|outbox|moperators\.api|moperators\.admin|entry.?point.*scan' \
  packages/operator-pack --glob '!**/tests/**' --glob '!**/README.md'; then
  echo 'operator pack contains a retired service or dynamic discovery dependency' >&2
  exit 1
fi

if rg -n 'products[./]' \
  kernel services/data services/executor services/gateway services/knowledge-next; then
  echo 'Kernel or capability service depends on a product' >&2
  exit 1
fi

neutral_roots=(
  packages/lightweight-agent-core/src
  kernel/src
  kernel/module.yaml
  services/data/src
  services/data/module.yaml
  services/executor/src
  services/executor/module.yaml
  services/gateway
  services/knowledge-next/src
  services/knowledge-next/module.yaml
  platform/assembler
  packages/protocol-contracts/src
  packages/workflow-compiler/src
  scripts/development
)
if rg -n -i '\bgroundwater\b' "${neutral_roots[@]}"; then
  echo 'Product-neutral runtime or tooling depends on the Groundwater product' >&2
  exit 1
fi

if rg -n -i 'groundwater|地下水|腰站堡|yaozhanbao|development_fast|标准训练档|开发快速档|训练和回测' web/src \
  --glob '!**/*.test.*' \
  --glob '!**/test-*' \
  --glob '!**/generated/**'; then
  echo 'Product-neutral Web runtime contains product-owned text or identity' >&2
  exit 1
fi

if rg -n -i 'ragflow|knowledge-snapshot|KNOWLEDGE_(?:SNAPSHOT|DOCUMENTS)_PATH|_compile_knowledge' \
  services/knowledge-next platform/assembler products domains; then
  echo 'retired Knowledge document parsing surface found' >&2
  exit 1
fi

if rg -n 'psycopg|sqlalchemy|pika|boto3|minio' domains \
  --glob '!**/tests/**'; then
  echo 'Domain package directly references Core infrastructure' >&2
  exit 1
fi
