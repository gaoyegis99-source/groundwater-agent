#!/usr/bin/env python3
"""Report body-free ContextCompiler metrics from a Kernel SQLite v34 store."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import sqlite3


SCHEMA_VERSION = 34


def build_report(database: Path) -> dict[str, object]:
    uri = f"file:{database.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT version FROM schema_version").fetchall()
        if len(rows) != 1 or rows[0]["version"] != SCHEMA_VERSION:
            raise ValueError("KERNEL_SCHEMA_VERSION_UNSUPPORTED")
        views = connection.execute(
            """SELECT policy_version,purpose,status,estimated_input_tokens,
                      actual_input_tokens,manifest_json
               FROM context_views ORDER BY created_at,context_view_id"""
        ).fetchall()

    groups: dict[tuple[str, str], dict[str, object]] = {}
    block_groups: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"blocks": 0, "tokens": 0}
    )
    retrieval_count = 0
    compactable_tokens = 0
    reduced_tokens = 0
    for row in views:
        key = (row["policy_version"], row["purpose"])
        group = groups.setdefault(
            key,
            {
                "policy_version": key[0],
                "purpose": key[1],
                "views": 0,
                "estimated_input_tokens": 0,
                "actual_input_tokens": 0,
                "actual_samples": 0,
                "absolute_estimation_error_tokens": 0,
                "statuses": {},
            },
        )
        group["views"] += 1
        group["estimated_input_tokens"] += row["estimated_input_tokens"]
        status_counts = group["statuses"]
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
        if row["actual_input_tokens"] is not None:
            actual = row["actual_input_tokens"]
            group["actual_input_tokens"] += actual
            group["actual_samples"] += 1
            group["absolute_estimation_error_tokens"] += abs(
                row["estimated_input_tokens"] - actual
            )
        manifest = json.loads(row["manifest_json"])
        for block in manifest["blocks"]:
            block_key = (block["kind"], block["disposition"])
            block_groups[block_key]["blocks"] += 1
            block_groups[block_key]["tokens"] += block["token_count"]
            if block["disposition"] == "RETRIEVABLE":
                retrieval_count += 1
            if block["retention"] == "BUDGETED":
                compactable_tokens += block["token_count"]
                if block["disposition"] in {"COMPACTED", "OMITTED", "WOULD_COMPACT"}:
                    reduced_tokens += block["token_count"]

    return {
        "schemaVersion": "context-metrics.v1",
        "database_schema_version": SCHEMA_VERSION,
        "view_count": len(views),
        "groups": sorted(
            groups.values(), key=lambda item: (item["policy_version"], item["purpose"])
        ),
        "blocks": [
            {
                "kind": key[0],
                "disposition": key[1],
                **value,
            }
            for key, value in sorted(block_groups.items())
        ],
        "retrieval_count": retrieval_count,
        "compaction": {
            "budgeted_tokens": compactable_tokens,
            "reduced_tokens": reduced_tokens,
            "reduction_ratio": (
                0.0
                if compactable_tokens == 0
                else round(reduced_tokens / compactable_tokens, 6)
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    print(json.dumps(build_report(args.database), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
