#!/usr/bin/env python3
"""Render the canonical full-project JSON report as concise Chinese Markdown."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _cell(value: object) -> str:
    return (
        str(value if value is not None else "—").replace("|", "\\|").replace("\n", " ")
    )


def _test_totals(stages: list[dict[str, Any]]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for stage in stages:
        for command in stage.get("commands", []):
            for name, count in command.get("testSummary", {}).items():
                if name == "testFilesPassed":
                    continue
                if isinstance(count, int):
                    totals[name] = totals.get(name, 0) + count
    return totals


def render_full_project_report(
    report: dict[str, Any], *, source_report: Path | None = None
) -> str:
    if report.get("schemaVersion") != "full-project-test-report.v1":
        raise ValueError("FULL_PROJECT_REPORT_SCHEMA_UNSUPPORTED")
    stages = report.get("stages")
    if not isinstance(stages, list):
        raise ValueError("FULL_PROJECT_REPORT_STAGES_INVALID")
    totals = _test_totals(stages)
    versions = report.get("baseline", {}).get("versions", {})
    lines = [
        "# Groundwater V6 全量测试报告",
        "",
        f"- 总状态：`{_cell(report.get('status'))}`",
        f"- 产品：`{_cell(report.get('product'))}`",
        f"- Run ID：`{_cell(report.get('runId'))}`",
        f"- 开始：`{_cell(report.get('startedAt'))}`",
        f"- 完成：`{_cell(report.get('finishedAt'))}`",
    ]
    if source_report is not None:
        lines.append(f"- 机器报告：`{source_report}`")
    lines.extend(
        (
            "",
            "## 汇总",
            "",
            f"- 阶段：{len(stages)}；通过：{sum(stage.get('status') == 'PASS' for stage in stages)}；失败：{sum(stage.get('status') == 'FAIL' for stage in stages)}；阻断：{sum(stage.get('status') == 'BLOCKED' for stage in stages)}；未运行：{sum(stage.get('status') == 'NOT_RUN' for stage in stages)}。",
            "- 测试计数："
            + (
                "，".join(f"{name}={count}" for name, count in sorted(totals.items()))
                if totals
                else "无可汇总的测试计数"
            )
            + "。",
            f"- 工程状态：`{_cell(report.get('engineeringStatus'))}`；科学状态：`{_cell(report.get('scientificStatus'))}`；Agent 体验状态：`{_cell(report.get('agentExperienceStatus'))}`。",
            "",
            "## 环境版本",
            "",
            "| 组件 | 版本 |",
            "| --- | --- |",
        )
    )
    for name, version in sorted(versions.items()):
        lines.append(f"| {_cell(name)} | `{_cell(version)}` |")
    lines.extend(
        (
            "",
            "## 阶段结果",
            "",
            "| 阶段 | 状态 | 命令数 | 用时（秒） | 测试摘要 |",
            "| --- | --- | ---: | ---: | --- |",
        )
    )
    for stage in stages:
        commands = stage.get("commands", [])
        duration = sum(float(command.get("durationSeconds", 0)) for command in commands)
        stage_totals = _test_totals([stage])
        summary = (
            "，".join(f"{name}={count}" for name, count in sorted(stage_totals.items()))
            or "—"
        )
        lines.append(
            f"| {_cell(stage.get('name'))} | `{_cell(stage.get('status'))}` | "
            f"{len(commands)} | {duration:.3f} | {_cell(summary)} |"
        )
    failures = [stage for stage in stages if stage.get("status") != "PASS"]
    lines.extend(("", "## 失败、阻断与未运行项", ""))
    if not failures:
        lines.append("无。所有声明阶段均通过。")
    else:
        for stage in failures:
            failure = stage.get("failure", {})
            failure_class = failure.get("failureClass", "UNSPECIFIED")
            diagnostics = sorted(
                {
                    code
                    for command in stage.get("commands", [])
                    for code in command.get("diagnosticCodes", [])
                }
            )
            suffix = f"；诊断码：{', '.join(diagnostics)}" if diagnostics else ""
            lines.append(
                f"- `{_cell(stage.get('name'))}`：`{_cell(stage.get('status'))}`；"
                f"类别 `{_cell(failure_class)}`{suffix}。"
            )
    snapshot = report.get("evidenceSnapshot", {})
    lines.extend(
        (
            "",
            "## 证据与隔离",
            "",
            f"- 证据快照：`{_cell(snapshot.get('status', 'NOT_ASSESSED'))}`；文件数：{len(snapshot.get('files', {})) if isinstance(snapshot.get('files'), dict) else 0}。",
            f"- 运行隔离：`{_cell(report.get('isolation', {}).get('policy', 'NOT_ASSESSED'))}`。",
            f"- 现有部署是否被修改：`{_cell(report.get('isolation', {}).get('existingDeploymentMutation', 'NOT_ASSESSED'))}`。",
            "",
            "## 结论边界",
            "",
            "本报告证明的是所列工程检查在记录环境中的执行结果。`scientificStatus` 或 `agentExperienceStatus` 为 `NOT_ASSESSED` 时，不得由工程测试通过推断科学有效性、真实 Provider 表现或第三方服务可用性。",
            "",
        )
    )
    return "\n".join(lines)


def write_markdown_report(source: Path, output: Path | None = None) -> Path:
    source = source.resolve()
    destination = (output or source.with_suffix(".md")).resolve()
    try:
        report = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("FULL_PROJECT_REPORT_READ_FAILED") from error
    if not isinstance(report, dict):
        raise ValueError("FULL_PROJECT_REPORT_ROOT_INVALID")
    markdown = render_full_project_report(report, source_report=source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(markdown, encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(destination)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(write_markdown_report(args.report, args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
