"""CLI argument parsing for isolated Compose acceptance."""

from __future__ import annotations

import argparse
from pathlib import Path

from scripts.maintenance.project_environment import validation_cache_root

from .environment import ROOT, _http_base_url, _private_ipv4_subnet
from .live_reply_quality import LiveReplyQualityConfig
from .scenarios import _run_groundwater_knowledge
from tests.e2e.groundwater.porous_live import PorousAcceptance


def main() -> None:
    output_root = validation_cache_root(ROOT)
    parser = argparse.ArgumentParser(
        description="Run an isolated assembled-product Compose acceptance gate"
    )
    parser.add_argument("--product", required=True)
    parser.add_argument(
        "--transfer-case",
        type=Path,
        help="Prepared INPUT manifest for the transfer-learning case",
    )
    parser.add_argument("--transfer-evidence", type=Path, default=output_root / "transfer-evidence")
    parser.add_argument(
        "--native-worker-image",
        help="Use an already smoke-verified native Worker image",
    )
    parser.add_argument(
        "--qgis-worker-image", help="Use an already smoke-verified QGIS Worker image"
    )
    parser.add_argument(
        "--embedding-base-url",
        type=_http_base_url,
        help=(
            "Use this Embedding provider while Agent and Intent Router remain on "
            "the deterministic test provider"
        ),
    )
    parser.add_argument(
        "--embedding-report",
        type=Path,
        default=output_root / "real-embedding-current.json",
        help=(
            "Write the real 297-question retrieval and exact-binding assessment; "
            "used only with a full --embedding-base-url run"
        ),
    )
    parser.add_argument(
        "--api-version",
        choices=("v12",),
        default="v12",
        help="This entrypoint only accepts the isolated v12 topology.",
    )
    parser.add_argument(
        "--network-subnet",
        type=_private_ipv4_subnet,
        help=(
            "Use an explicit private subnet for the temporary Compose network; "
            "useful when Docker's automatic address pools are exhausted."
        ),
    )
    subset = parser.add_mutually_exclusive_group()
    subset.add_argument(
        "--webui-only",
        action="store_true",
        help="Run only the isolated browser gate, not the complete execution regression",
    )
    subset.add_argument(
        "--interaction-only",
        action="store_true",
        help="Run the isolated WebUI, installed TLI and SDK handoff and recovery gate",
    )
    subset.add_argument(
        "--tasks-only",
        action="store_true",
        help="Run only isolated task collaboration and Kernel restart acceptance",
    )
    subset.add_argument(
        "--six-model-only",
        action="store_true",
        help="Run only the six assembled model lifecycles",
    )
    subset.add_argument(
        "--native-real-cases",
        type=Path,
        help="Run the verified real-data run directory and classification control through V12",
    )
    subset.add_argument(
        "--sustainability-only",
        action="store_true",
        help="Run the sustainability lifecycle against the deployed local HTTPS model",
    )
    parser.add_argument(
        "--sustainability-case", type=Path,
        help="Synthetic case manifest for --sustainability-only",
    )
    subset.add_argument(
        "--porous-simulation-case", type=Path,
        help="Run the isolated porous candidate with the staged rectangular project ZIP",
    )
    parser.add_argument("--porous-service-image", help="Locally built porous candidate image")
    parser.add_argument("--porous-fixtures", type=Path, help="Exercise all porous workflows with these fixtures")
    parser.add_argument("--porous-assessment-bridge", type=Path, help="Run assessment with the local service and supplied synthetic evidence")
    parser.add_argument("--porous-report", type=Path, default=output_root / "porous-simulation.json")
    subset.add_argument(
        "--reply-quality-only",
        action="store_true",
        help="Run the frozen 24-case real-Provider reply-quality gate",
    )
    parser.add_argument("--reply-quality-provider", choices=("deepseek", "minimax"))
    parser.add_argument("--reply-quality-protocol", choices=("responses", "chat_completions"))
    parser.add_argument("--reply-quality-base-url", type=_http_base_url)
    parser.add_argument("--reply-quality-model")
    parser.add_argument(
        "--reply-quality-key-env",
        help="Name of the environment variable holding the temporary Provider key",
    )
    parser.add_argument("--reply-quality-report", type=Path)
    parser.add_argument("--webui-report", type=Path, default=output_root / "webui-evidence")
    args = parser.parse_args()
    if args.interaction_only and (
        args.product != "groundwater"
        or args.webui_report.resolve().is_relative_to(ROOT.parent)
        or args.embedding_base_url is not None
    ):
        parser.error("--interaction-only requires groundwater, local test providers and a report outside the checkout")
    if args.sustainability_case is not None and (
        not args.sustainability_only
        or not args.sustainability_case.is_file()
        or args.transfer_evidence.resolve().is_relative_to(ROOT)
    ):
        parser.error("--sustainability-case requires --sustainability-only and evidence outside source")
    if args.native_real_cases is not None and (
        args.product != "groundwater"
        or not (args.native_real_cases / "summary.json").is_file()
        or args.transfer_evidence.resolve().is_relative_to(ROOT)
    ):
        parser.error("native real cases require a verified run directory and evidence outside source")
    quality_values = {
        "provider": args.reply_quality_provider,
        "protocol": args.reply_quality_protocol,
        "base_url": args.reply_quality_base_url,
        "model": args.reply_quality_model,
        "key_env": args.reply_quality_key_env,
        "report": args.reply_quality_report,
    }
    if args.reply_quality_only:
        missing = [name for name, value in quality_values.items() if value is None]
        if missing:
            parser.error("reply-quality mode requires: " + ", ".join(missing))
        if args.product != "groundwater" or args.embedding_base_url is None:
            parser.error(
                "reply-quality mode requires --product groundwater and "
                "--embedding-base-url"
            )
        if args.reply_quality_report.resolve().is_relative_to(ROOT):
            parser.error("reply-quality report must be outside the repository")
        reply_quality = LiveReplyQualityConfig(**quality_values)
    else:
        if any(value is not None for value in quality_values.values()):
            parser.error("reply-quality options require --reply-quality-only")
        reply_quality = None
    porous = None
    if args.porous_simulation_case is not None:
        if args.product != "groundwater" or not args.porous_service_image:
            parser.error("porous simulation requires --product groundwater and --porous-service-image")
        if not args.porous_simulation_case.is_file() or args.porous_report.resolve().is_relative_to(ROOT):
            parser.error("porous simulation requires a staged fixture and a report outside source")
        if args.porous_assessment_bridge is not None and (
            args.porous_fixtures is None or not args.porous_assessment_bridge.is_file()
        ):
            parser.error("the assessment bridge requires full porous fixtures and assessment evidence")
        porous = PorousAcceptance(
            args.porous_simulation_case, args.porous_service_image, args.porous_report,
            args.porous_fixtures, args.porous_assessment_bridge,
        )
    _run_groundwater_knowledge(
        args.transfer_case,
        args.transfer_evidence,
        args.native_worker_image,
        args.qgis_worker_image,
        product_id=args.product,
        webui_only=args.webui_only,
        interaction_only=args.interaction_only,
        webui_report=args.webui_report,
        tasks_only=args.tasks_only,
        six_model_only=args.six_model_only,
        native_real_cases=args.native_real_cases,
        sustainability_only=args.sustainability_only,
        sustainability_case=args.sustainability_case,
        porous=porous,
        embedding_base_url=args.embedding_base_url,
        embedding_report=args.embedding_report,
        network_subnet=args.network_subnet,
        reply_quality=reply_quality,
    )
