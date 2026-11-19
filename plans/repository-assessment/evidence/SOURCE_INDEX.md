# 源码证据索引

基线 e6d8512797a128887cf2bab41a92629ddb06b668；2026-11-14。以下 E 编号是主报告结论的定位入口。路径、符号、合同、测试和调用链分别给出。测试列表示相关测试存在，不自动表示本轮通过；实际结果只认 TEST_EVIDENCE.md 和日志。全量逐符号行号见 SYMBOLS.tsv，逐文件SHA256见 files.json。

## E01 产品装配与定位

源码：

- [source/products/groundwater/product-manifest.json](/home/mtd/liweigis/workflow/groundwater-v6/source/products/groundwater/product-manifest.json:1)
- [source/domains/groundwater/6.2.0/domain-manifest.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/domain-manifest.json:1)
- [source/domains/groundwater/6.2.0/instructions.md](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/instructions.md:1)

合同：`product-manifest.v8 / domain-manifest.v3`。

调用链：Product→Domain6.2.0→18 Model/Workflow；Assembler装配。

测试关联：

- [source/tests/contracts/test_groundwater_product.py](/home/mtd/liweigis/workflow/groundwater-v6/source/tests/contracts/test_groundwater_product.py:19) — `test_dialogue_advice_instructions_use_natural_language_options, test_data_observations_accept_exact_version_identity_and_lineage, test_groundwater_v11_domain_upgrades_models_and_preserves_workflow_identity, test_flow_field_binds_computed_head_through_snapshot_and_idw, test_coverage_dissolves_before_hull_and_uses_existing_runtimes`
- [source/tests/assembler/test_product_manifest.py](/home/mtd/liweigis/workflow/groundwater-v6/source/tests/assembler/test_product_manifest.py:39) — `test_domain_cli_checks_current_package_without_loading_legacy_services, test_default_module_scan_only_returns_current_contracts, test_retired_infrastructure_dependency_is_not_implicitly_provided, test_data_route_compiles_research_streaming_limits_at_gateway, test_product_route_prefix_is_a_strict_trust_boundary, test_data_is_a_required_v11_product_service, test_isolated_assembly_inputs_exclude_local_generated_and_dependency_trees, test_remote_workflow_operation_must_be_enabled_by_product_binding, test_groundwater_compiles_v12_research_outputs, test_groundwater_compiles_native_png_model_and_expected_parallelism`

## E02 架构约束及文档漂移

源码：

- [source/docs/architecture/architecture-contract.md](/home/mtd/liweigis/workflow/groundwater-v6/source/docs/architecture/architecture-contract.md:1)
- [source/kernel/README.md](/home/mtd/liweigis/workflow/groundwater-v6/source/kernel/README.md:1)
- [source/kernel/src/platform_kernel/schema/__init__.py](/home/mtd/liweigis/workflow/groundwater-v6/source/kernel/src/platform_kernel/schema/__init__.py:1)
- [source/kernel/src/platform_kernel/access/research_auth.py](/home/mtd/liweigis/workflow/groundwater-v6/source/kernel/src/platform_kernel/access/research_auth.py:34) — `_now, _wire_time, _parse_time, _digest, validate_username, validate_password, bootstrap_admin, create_user, list_users, patch_user`

合同：`API v12 / Kernel schema34`。

调用链：Gateway→Kernel身份；旧README仍称v33/无身份。

测试关联：

- [source/tests/contracts/test_public_api_contract.py](/home/mtd/liweigis/workflow/groundwater-v6/source/tests/contracts/test_public_api_contract.py:13) — `test_checked_in_public_contract_matches_owner_openapi, test_explicit_product_selection_matches_single_product_default, test_public_contract_has_only_gateway_paths_and_unique_operations, test_public_problem_contracts_share_the_same_required_core, test_all_service_problem_schemas_are_identical, test_data_and_knowledge_emit_the_same_public_problem_shape, test_public_http_rejects_snake_case_but_tool_arguments_keep_contract_keys`

## E03 部署和网关

源码：

- [source/platform/assembler/assembler_gateway.py](/home/mtd/liweigis/workflow/groundwater-v6/source/platform/assembler/assembler_gateway.py:11) — `_compile_routes, _nginx_config`
- [source/platform/assembler/assembler_runtime.py](/home/mtd/liweigis/workflow/groundwater-v6/source/platform/assembler/assembler_runtime.py:21) — `_worker_runtime_config, _service_healthcheck, _persistent_mount, _build, _ordinary_service, _executor_services, _data_services, _knowledge_next_services, _compose`
- [source/platform/assembler/deployment_topology.py](/home/mtd/liweigis/workflow/groundwater-v6/source/platform/assembler/deployment_topology.py:66) — `_network_bindings, _harden, apply_assembled_topology`
- [source/services/gateway/Dockerfile](/home/mtd/liweigis/workflow/groundwater-v6/source/services/gateway/Dockerfile:1)

合同：`platform.module/v9 / topology.v1`。

调用链：浏览器→nginx auth_request→Kernel；反向代理Data/Knowledge。

测试关联：

- [source/tests/assembler/test_deployment_topology.py](/home/mtd/liweigis/workflow/groundwater-v6/source/tests/assembler/test_deployment_topology.py:18) — `test_assembled_topology_has_only_declared_network_roles, test_topology_applies_hardening_except_registered_roles, test_independent_data_and_knowledge_compose_follow_the_topology_contract`

## E04 UI产品定义

源码：

- [评估基线中的 UI 总体设计](https://github.com/gaoyegis99-source/groundwater-agent/blob/e6d8512797a128887cf2bab41a92629ddb06b668/plans/ui-interaction-spec/00_MASTER_SPEC.md)
- [source/products/groundwater/ui/product-ui-porous.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/products/groundwater/ui/product-ui-porous.v1.json:1)
- [source/web/src/product-ui.ts](/home/mtd/liweigis/workflow/groundwater-v6/source/web/src/product-ui.ts:1)

合同：`product-ui.v1`。

调用链：Product UI→能力抽屉/示例/成果viewer。

测试关联：

- [source/tests/assembler/test_product_ui.py](/home/mtd/liweigis/workflow/groundwater-v6/source/tests/assembler/test_product_ui.py:66) — `test_product_ui_must_completely_and_strictly_cover_domain, test_product_ui_source_checksum_is_verified_before_output, test_product_ui_reference_cannot_escape_product, test_product_icon_rejects_active_or_external_svg, test_product_icon_allows_local_fragment_references, test_ui_artifacts_lock_gateway_and_no_cache_are_one_assembly`
- [source/web/src/product-ui.test.tsx](/home/mtd/liweigis/workflow/groundwater-v6/source/web/src/product-ui.test.tsx:1)

## E10 Data语义合同

源码：

- [source/services/data/src/platform_data_service/models.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/models.py:10) — `Model, Conversion, FieldMeaning, SpatialExtent, Semantics, Output, BeginPublication, Metric, Metrics, Filter`
- [source/services/data/src/platform_data_service/semantics/service.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/semantics/service.py:8) — `check_semantics, add_semantics`

合同：`Semantics / FieldMeaning / SemanticRevision`。

调用链：上传/修订→semantic_revisions→准备门禁。

测试关联：

- [source/services/data/tests/test_service.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/tests/test_service.py:610) — `test_inventory_and_structured_discovery_explain_current_catalog_matches, test_discovery_binds_quantity_and_unit_to_the_same_field, test_conversion_duplicates_aggregation_and_semantic_revision, test_preview_exact_identity_idempotency_and_cancel`

## E11 Data解析与文件质量

源码：

- [source/services/data/src/platform_data_service/ingestion/service.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/ingestion/service.py:15) — `prepare`
- [source/services/data/src/platform_data_service/ingestion/field_profile.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/ingestion/field_profile.py:19) — `_bounded_text, _display_value, _temporal_kind, _has_range, _string_type, _FieldProfile, TableFieldProfiler`
- [source/services/data/src/platform_data_service/ingestion/raster.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/ingestion/raster.py:11) — `inspect_raster, _describe`

合同：`descriptor / DataObject / conversion`。

调用链：入库扫描→原始对象/查询对象/字段profile。

测试关联：

- [source/services/data/tests/test_service.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/tests/test_service.py:610) — `test_inventory_and_structured_discovery_explain_current_catalog_matches, test_discovery_binds_quantity_and_unit_to_the_same_field, test_conversion_duplicates_aggregation_and_semantic_revision, test_preview_exact_identity_idempotency_and_cancel`

## E12 Data发现与读取

源码：

- [source/services/data/src/platform_data_service/catalog/discovery.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/catalog/discovery.py:12) — `project_discovery, _field_catalog, _matches, _score_and_reasons, _matched_fields, _unknowns, _terms, _fold, _unit_key, _field_text`
- [source/services/data/src/platform_data_service/catalog/service.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/catalog/service.py:6) — `version, describe, list_datasets, inventory, discover_datasets`
- [source/services/data/src/platform_data_service/table_inspection.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/table_inspection.py:15) — `TableInspector`

合同：`data-tools-v4 / DataVersionRefV2`。

调用链：Observation→project_discovery/describe；inspect→query轮询。

测试关联：

- [source/services/data/tests/test_service.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/tests/test_service.py:610) — `test_inventory_and_structured_discovery_explain_current_catalog_matches, test_discovery_binds_quantity_and_unit_to_the_same_field, test_conversion_duplicates_aggregation_and_semantic_revision, test_preview_exact_identity_idempotency_and_cancel`

## E13 Data质量结果台账

源码：

- [source/services/data/src/platform_data_service/quality.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/quality.py:200) — `submit_run, _derive_status, replace_binding`
- [source/services/data/src/platform_data_service/api/routers/quality.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/api/routers/quality.py:23) — `_require_operator, register_quality_routes`

合同：`QualityRun / table-validation-rules.v1`。

调用链：外部规则结果→submit_run→quality_runs/outbox；非自动专业核查。

测试关联：

- [source/services/data/tests/test_quality_control.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/tests/test_quality_control.py:93) — `test_quality_suite_binding_complete_run_alert_and_replay, test_quality_contract_rejects_invalid_and_partial_rules`

## E14 Data实验与模型注册

源码：

- [source/services/data/src/platform_data_service/mlops.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/mlops.py:13) — `_experiment_wire, create_experiment, _resolve, resolve_alias, _resolved_wire, _run_wire, record_run, get_run, _model_wire, register_model_version`
- [source/services/data/src/platform_data_service/api/routers/mlops.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/api/routers/mlops.py:25) — `_require_operator, register_mlops_routes`
- [source/services/data/src/platform_data_service/outbox_delivery.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/outbox_delivery.py:8) — `OutboxDispatcher`

合同：`Data HTTP v3 ml endpoints`。

调用链：API→experiment/run/alias→MLflow outbox；主执行路径未发现消费。

测试关联：

- [source/services/data/tests/test_mlops_lifecycle.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/tests/test_mlops_lifecycle.py:74) — `test_ml_experiment_registry_alias_resolution_and_rollback`

## E15 Data查询与预览

源码：

- [source/services/data/src/platform_data_service/query/execute.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/query/execute.py:8) — `execute`
- [source/services/data/src/platform_data_service/preview/execute.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/preview/execute.py:24) — `_scalar, _field_type, _unique_headers, _validate_xlsx, _xlsx, _quoted, _geometry, _coordinate_count, _crs, _sqlite_type`
- [source/services/data/src/platform_data_service/adapters/sandbox.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/adapters/sandbox.py:12) — `Ruleset, PathRule, restrict_query, ArgumentComparison, install_container_profile`

合同：`Query / Preview / data-http-v3`。

调用链：API→持久队列→沙箱子进程→有限结果。

测试关联：

- [source/services/data/tests/test_service.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/tests/test_service.py:610) — `test_inventory_and_structured_discovery_explain_current_catalog_matches, test_discovery_binds_quantity_and_unit_to_the_same_field, test_conversion_duplicates_aggregation_and_semantic_revision, test_preview_exact_identity_idempotency_and_cancel`

## E16 Data发布与血缘

源码：

- [source/services/data/src/platform_data_service/publication/service.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/publication/service.py:8) — `get, _subject, get_owned, public, _publication_kind, begin, attach, prepare, abort, commit`
- [source/services/data/src/platform_data_service/openlineage.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/openlineage.py:15) — `_time, _run_id, _facet, _dataset, _event, _inputs, _outputs, record_start, record_complete, record_fail`

合同：`BeginPublication / commit / DataVersionRefV2`。

调用链：Executor→prepare→commit→lineage；事务公开整组结果。

测试关联：

- [source/services/executor/tests/test_named_publication_integration.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/executor/tests/test_named_publication_integration.py:21) — `test_named_data_reference_requires_complete_exact_identity, test_two_named_outputs_publish_atomically_and_are_exactly_reusable, test_published_receipt_recovery_does_not_repeat_execution`
- [source/services/data/tests/test_openlineage_outbox.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/tests/test_openlineage_outbox.py:45) — `test_publication_writes_openlineage_start_and_complete_transactionally, test_outbox_delivery_is_idempotent_fail_open_and_reclaims_lease`

## E20 全量知识资产

源码：

- [source/domains/groundwater/6.2.0/domain-manifest.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/domain-manifest.json:1)
- [source/services/knowledge-next/src/platform_knowledge_next/models.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/knowledge-next/src/platform_knowledge_next/models.py:13) — `Model, Source, DataBinding, Unit, Document, document_content, stored_document_content, KnowledgePackageV1, Budget, RetrievalMetadataFilter`

合同：`domain-knowledge.v2 / knowledge-package.v1`。

调用链：9资产→121文档/321unit；详见KNOWLEDGE_INVENTORY。

测试关联：

- [source/tests/assembler/test_domain_packages.py](/home/mtd/liweigis/workflow/groundwater-v6/source/tests/assembler/test_domain_packages.py:79) — `test_release_is_deterministic_portable_and_contains_only_declared_files, test_two_products_reuse_one_package_without_copying_definitions, test_pinned_package_rejects_any_changed_file_before_output, test_model_default_respects_exclusive_schema_bounds_before_output, test_model_default_respects_pattern_before_output, test_model_default_must_satisfy_bound_operator_schema, test_model_default_must_satisfy_every_downstream_parameter, test_invalid_model_json_schema_fails_before_output, test_model_field_requirement_is_validated_at_assembly, test_model_semantic_requirement_is_validated_at_assembly`

## E21 检索、绑定、读取

源码：

- [source/services/knowledge-next/src/platform_knowledge_next/retrieval.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/knowledge-next/src/platform_knowledge_next/retrieval.py:20) — `binding_applies, EvidenceService`
- [source/services/knowledge-next/src/platform_knowledge_next/retrieval_budget.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/knowledge-next/src/platform_knowledge_next/retrieval_budget.py:8) — `required_context_ids, additional_context_units, context_content_chars, fits_context_budget`

合同：`EvidenceService / knowledge-tools-v5`。

调用链：查询→lexical/vector→RRF→unit→read/checksum/binding。

测试关联：

- [source/services/knowledge-next/tests/test_retrieval_budget.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/knowledge-next/tests/test_retrieval_budget.py:16) — `test_required_context_ids_resolves_transitive_cycles_once, test_additional_context_units_excludes_already_included_units, test_context_budget_accepts_exact_limits_and_rejects_each_overflow`
- [source/services/knowledge-next/tests/test_scope_retry.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/knowledge-next/tests/test_scope_retry.py:129) — `test_scope_identity_history_budget_and_receipt, test_scope_contract_rejects_ambiguous_context, test_project_rejected_before_embedding, test_combined_uses_global_modality_ranks_no_quotas, test_hnsw_candidate_under_return_uses_exact_fallback, test_hnsw_small_cohort_does_not_use_exact_fallback, test_combined_candidates_share_snapshot, test_retry_releases_worker_replays_and_recovers_after_restart, test_retry_budget_is_finite_across_restarts, test_embedding_transient_retry_recovers_without_new_idempotency_key`

## E22 知识导入与分块边界

源码：

- [source/services/knowledge-next/src/platform_knowledge_next/package_conversion.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/knowledge-next/src/platform_knowledge_next/package_conversion.py:7) — `convert_domain_package`
- [source/services/knowledge-next/src/platform_knowledge_next/ingestion.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/knowledge-next/src/platform_knowledge_next/ingestion.py:28) — `submit, request_manual_retry, utcnow, claim, renew, check_versions, Importer`
- [source/services/knowledge-next/src/platform_knowledge_next/data_adapter.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/knowledge-next/src/platform_knowledge_next/data_adapter.py:17) — `DataTransientError, retry_after, DataAdapter`

合同：`KnowledgePackageV1 / DataBinding`。

调用链：预分块包→验证/embedding→原子生效；不二次解析Data原文件。

测试关联：

- [source/services/knowledge-next/tests/test_functional_contracts.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/knowledge-next/tests/test_functional_contracts.py:28) — `test_kernel_verified_gateway_identity_and_management_roles, test_readiness_checks_database_and_embedding, test_readiness_reports_embedding_failure, test_http_catalog_pagination_and_import_listing, test_history_lifecycle_cas_receipt_and_exact_read, test_all_static_tools_and_openapi_agree, test_http_input_limits_are_atomic_and_readonly_covers_lifecycle, test_simultaneous_replay_and_commit_response_loss, test_cli_domain_conversion_and_typed_http_client_write_guard`

## E23 知识来源和主题重叠

源码：

- [source/domains/groundwater/6.2.0/knowledge/demo-knowledge-interpretation.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-interpretation.v1.json:1)
- [source/domains/groundwater/6.2.0/knowledge/model-cards.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/model-cards.v1.json:1)
- [source/domains/groundwater/6.2.0/knowledge/groundwater-statistical-methods.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/groundwater-statistical-methods.v1.json:1)

合同：`sourceRefs / locator / modelRef`。

调用链：Domain→Knowledge检索；USGS题录抽查见EXTERNAL_REFERENCES。

测试关联：

- [source/tests/contracts/test_groundwater_evaluator_diagnostics.py](/home/mtd/liweigis/workflow/groundwater-v6/source/tests/contracts/test_groundwater_evaluator_diagnostics.py:9) — `test_http_failure_reports_status_and_validation_location_without_prompt, test_failed_session_assertion_reports_state_without_agent_message, test_semantic_cases_share_pagination_inputs_without_cumulative_context, test_excluded_stale_case_does_not_prepare_resources`

## E24 受控语料与研究包

源码：

- [source/products/groundwater/research/research_corpus.py](/home/mtd/liweigis/workflow/groundwater-v6/source/products/groundwater/research/research_corpus.py:23) — `_sha256_file, _canonical_digest, CorpusTimeRange, AuthorizedCorpusSource, ControlledCorpusDocument, ControlledCorpusManifestV1, verify_external_sources, verify_import_baseline`
- [source/products/groundwater/research/research_package.py](/home/mtd/liweigis/workflow/groundwater-v6/source/products/groundwater/research/research_package.py:20) — `_digest, ResearchCitationV1, ResearchAnswerV1, GroundwaterModelIdentity, GroundwaterDomainPackageIdentity, ResearchProposalV1, build_reproducible_research_package`
- [source/docs/capabilities/groundwater-research-evidence.md](/home/mtd/liweigis/workflow/groundwater-v6/source/docs/capabilities/groundwater-research-evidence.md:1)

合同：`groundwater-controlled-corpus.v1 / ResearchAnswerV1`。

调用链：离线脚本→授权清单/研究提案；未发现默认Agent使用ResearchAnswerV1。

测试关联：

- [source/products/groundwater/tests/test_research.py](/home/mtd/liweigis/workflow/groundwater-v6/source/products/groundwater/tests/test_research.py:89) — `test_controlled_corpus_verifies_external_bytes_and_import_baseline, test_import_baseline_accepts_v6_version_response_shape, test_controlled_corpus_rejects_digest_escape_and_repo_local_sources, test_research_answer_and_package_require_exact_active_evidence`

## E30 意图路由

源码：

- [source/kernel/src/platform_kernel/intent_routing/router.py](/home/mtd/liweigis/workflow/groundwater-v6/source/kernel/src/platform_kernel/intent_routing/router.py:124) — `IntentRoutingError, IntentRouter, _usage, _utc_now, _elapsed_ms`
- [source/kernel/src/platform_kernel/intent_routing/decision.py](/home/mtd/liweigis/workflow/groundwater-v6/source/kernel/src/platform_kernel/intent_routing/decision.py:8) — `_semantic_literal_decision, _authorize_model_decision`
- [source/kernel/src/platform_kernel/intent_routing/result_mapping.py](/home/mtd/liweigis/workflow/groundwater-v6/source/kernel/src/platform_kernel/intent_routing/result_mapping.py:9) — `map_provider_result`
- [source/kernel/src/platform_kernel/agent_runtime/mode.py](/home/mtd/liweigis/workflow/groundwater-v6/source/kernel/src/platform_kernel/agent_runtime/mode.py:9) — `AgentModeCapabilities, ModeResolutionError, resolve_mode_capabilities`

合同：`IntentDecisionV4 / AgentModeCapabilities`。

调用链：消息→Router无工具判意图→Kernel权限映射→Agent。

测试关联：

- [source/tests/architecture/test_intent_router_boundaries.py](/home/mtd/liweigis/workflow/groundwater-v6/source/tests/architecture/test_intent_router_boundaries.py:12) — `test_router_delegates_context_provider_and_result_algorithms, test_router_algorithm_modules_do_not_import_the_router_facade, test_generation_transport_is_single_attempt_and_protocol_repair_is_bounded`

## E31 工具与读调用治理

源码：

- [source/kernel/src/platform_kernel/agent_runtime/tooling/factory.py](/home/mtd/liweigis/workflow/groundwater-v6/source/kernel/src/platform_kernel/agent_runtime/tooling/factory.py:23) — `build_agent_tools`
- [source/kernel/src/platform_kernel/agent_runtime/tooling/read_governance.py](/home/mtd/liweigis/workflow/groundwater-v6/source/kernel/src/platform_kernel/agent_runtime/tooling/read_governance.py:32) — `_pending_exact_evidence, _proposal_evidence_read_enabled, _control_result, _prepare_tools, _knowledge_backed_preparation_enabled, _strip_schema_titles, _govern_read_tool, _repair_current_provider_batch, _schema_repair_instruction, _dataset_search_result_digest`
- [source/kernel/src/platform_kernel/agent_runtime/governance.py](/home/mtd/liweigis/workflow/groundwater-v6/source/kernel/src/platform_kernel/agent_runtime/governance.py:12) — `RunLimits, run_limits, new_governance, validate_governance, resume_governance, governance, non_submit_enabled, read_tool_enabled, evidence_read_enabled, preparation_tool_enabled`

合同：`build_agent_tools / RunLimits`。

调用链：mode决定工具集合→去重/预算→service observation。

测试关联：

- [source/kernel/tests/test_sdk_execution_governance.py](/home/mtd/liweigis/workflow/groundwater-v6/source/kernel/tests/test_sdk_execution_governance.py:124) — `test_mode_run_limits_are_fixed, test_execution_read_budget_covers_version_and_table_inspection_per_input, test_observation_growth_blocks_next_model_request_and_keeps_usage, test_dialogue_enters_submit_only_finalization_on_eighth_request, test_exact_successful_duplicate_is_not_sent_upstream_and_forces_finalization, test_equivalent_dataset_searches_advance_execution_to_proposal, test_minimax_repairs_disabled_discovery_tool_after_proposal_transition, test_stale_read_does_not_forget_an_earlier_successful_read, test_new_checksum_after_stale_read_is_sent_upstream, test_read_tool_limit_short_circuits_thirteenth_call`
- [source/kernel/tests/test_internal_modules.py](/home/mtd/liweigis/workflow/groundwater-v6/source/kernel/tests/test_internal_modules.py:1632) — `test_preparation_distinguishes_equivalent_convertible_and_missing_field_units, test_preparation_blocks_known_unit_and_crs_mismatches, test_preparation_checks_declared_vertical_basis_without_inferring_missing_facts, test_preparation_keeps_missing_crs_timezone_and_range_unknown`

## E32 专业准备与UNKNOWN

源码：

- [source/kernel/src/platform_kernel/agent_runtime/preparation_tool.py](/home/mtd/liweigis/workflow/groundwater-v6/source/kernel/src/platform_kernel/agent_runtime/preparation_tool.py:233) — `_inspect_data_inputs, _check_semantic_requirement, _check_vertical_basis_requirement, _ready_snapshot`
- [source/kernel/src/platform_kernel/tasks/preparation.py](/home/mtd/liweigis/workflow/groundwater-v6/source/kernel/src/platform_kernel/tasks/preparation.py:14) — `_candidate_identity, _candidate_entries, save_preparation, save_preparation_connection, _with_candidate_entries, preparation_in_connection, get_preparation`

合同：`TaskPreparationV1 / semanticRequirements`。

调用链：prepare_task_inputs→_inspect_data_inputs→单位/基准/CRS检查；缺失可READY。

测试关联：

- [source/kernel/tests/test_internal_modules.py](/home/mtd/liweigis/workflow/groundwater-v6/source/kernel/tests/test_internal_modules.py:1632) — `test_preparation_distinguishes_equivalent_convertible_and_missing_field_units, test_preparation_blocks_known_unit_and_crs_mismatches, test_preparation_checks_declared_vertical_basis_without_inferring_missing_facts, test_preparation_keeps_missing_crs_timezone_and_range_unknown`

## E33 提案边界校验缺口

源码：

- [source/kernel/src/platform_kernel/agent_runtime/result_processing.py](/home/mtd/liweigis/workflow/groundwater-v6/source/kernel/src/platform_kernel/agent_runtime/result_processing.py:166) — `prepare_interruption`
- [source/kernel/src/platform_kernel/agent_runtime/run_finalization.py](/home/mtd/liweigis/workflow/groundwater-v6/source/kernel/src/platform_kernel/agent_runtime/run_finalization.py:255) — `record_interruption, finalize_completed_execution`
- [source/kernel/src/platform_kernel/agent_runtime/persistence/run_lifecycle.py](/home/mtd/liweigis/workflow/groundwater-v6/source/kernel/src/platform_kernel/agent_runtime/persistence/run_lifecycle.py:28) — `confirmation_is_current, create_run, save_waiting_result, mark_execution_queued, fail_workflow_definition_unavailable, claim_state, finish_run, fail_run, supersede_waiting_run, publish_workflow_draft`
- [source/kernel/src/platform_kernel/agent_runtime/evidence.py](/home/mtd/liweigis/workflow/groundwater-v6/source/kernel/src/platform_kernel/agent_runtime/evidence.py:21) — `TurnEvidence, knowledge_identity, exact_retrieval_identities, trusted_read_identity, read_supersedes_identity, _object, _payload, _data_reference, collect_turn_evidence`

合同：`prepare_interruption / PreparedAction / AgentPlanV2`。

调用链：本轮exact read→无显式prepare→仅DATA_IDENTITY的READY→save_waiting_result。

测试关联：

- [plans/repository-assessment/evidence/probe_preparation_boundary.py](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/evidence/probe_preparation_boundary.py:1)

## E34 上下文与指令维护

源码：

- [source/kernel/src/platform_kernel/agent_runtime/prompts.py](/home/mtd/liweigis/workflow/groundwater-v6/source/kernel/src/platform_kernel/agent_runtime/prompts.py:17) — `describe_capabilities, build_instructions, build_draft_instructions, _execution_retrieval_instruction`
- [source/kernel/src/platform_kernel/agent_runtime/context_policy.py](/home/mtd/liweigis/workflow/groundwater-v6/source/kernel/src/platform_kernel/agent_runtime/context_policy.py:30) — `ContextPolicyV1`
- [source/kernel/src/platform_kernel/agent_runtime/sdk_execution/execution_builders.py](/home/mtd/liweigis/workflow/groundwater-v6/source/kernel/src/platform_kernel/agent_runtime/sdk_execution/execution_builders.py:41) — `_build_agent, _finish_when_submitted, _build_run_config, _build_run_context, _capabilities, new_trace_id`

合同：`ContextPolicyV1 / context block authority`。

调用链：Domain指引+catalog+Task+Execution projections→Provider。

测试关联：

- [source/kernel/tests/test_agent_context_efficiency.py](/home/mtd/liweigis/workflow/groundwater-v6/source/kernel/tests/test_agent_context_efficiency.py:42) — `test_dialogue_prompt_assigns_read_only_method_comparison_and_structured_options, test_small_request_context_overhead_stays_within_five_percent, test_initial_history_reads_only_unsummarized_turns_with_identical_model_input, test_execution_context_keeps_old_outputs_without_loading_action_arguments, test_execution_fact_tools_project_prior_run_without_parameters, test_activity_projection_decodes_only_selected_runs_and_preserves_timing`

## E35 Task与Plan语义

源码：

- [source/kernel/src/platform_kernel/tasks/progress.py](/home/mtd/liweigis/workflow/groundwater-v6/source/kernel/src/platform_kernel/tasks/progress.py:21) — `_RuntimeFacts, derive_task_progress, _criterion_progress, _draft_progress, _result_progress, _runtime_facts, _task_state, _current_blocked_preparation, _continuation_blocking_reason, _step_progress`
- [source/kernel/src/platform_kernel/agent_runtime/tooling/task_tools.py](/home/mtd/liweigis/workflow/groundwater-v6/source/kernel/src/platform_kernel/agent_runtime/tooling/task_tools.py:37) — `_PlanInput, build_plan_tool, build_task_tools`
- [source/kernel/src/platform_kernel/schema/tasks.py](/home/mtd/liweigis/workflow/groundwater-v6/source/kernel/src/platform_kernel/schema/tasks.py:1)
- [source/kernel/src/platform_kernel/schema/agent.py](/home/mtd/liweigis/workflow/groundwater-v6/source/kernel/src/platform_kernel/schema/agent.py:1)

合同：`TaskProgressV1 / AgentPlanV2`。

调用链：Task criteria/results/review→派生进度；Plan绑定本轮action。

测试关联：

- [source/kernel/tests/test_internal_modules.py](/home/mtd/liweigis/workflow/groundwater-v6/source/kernel/tests/test_internal_modules.py:1632) — `test_preparation_distinguishes_equivalent_convertible_and_missing_field_units, test_preparation_blocks_known_unit_and_crs_mismatches, test_preparation_checks_declared_vertical_basis_without_inferring_missing_facts, test_preparation_keeps_missing_crs_timezone_and_range_unknown`

## E36 确认与结果续接

源码：

- [source/kernel/src/platform_kernel/interaction/confirmation.py](/home/mtd/liweigis/workflow/groundwater-v6/source/kernel/src/platform_kernel/interaction/confirmation.py:25) — `ClaimedDecision, request_decision, recover_requested, next_requested, has_requested, claim_decision, finalize_decision`
- [source/kernel/src/platform_kernel/interaction/confirmation_commands.py](/home/mtd/liweigis/workflow/groundwater-v6/source/kernel/src/platform_kernel/interaction/confirmation_commands.py:14) — `SessionProjectionPort, ConfirmationExecutor`
- [source/kernel/src/platform_kernel/agent_runtime/run_resume.py](/home/mtd/liweigis/workflow/groundwater-v6/source/kernel/src/platform_kernel/agent_runtime/run_resume.py:47) — `confirm_run, reconcile_execution, finalize_recovery, confirm_recovery, reject_run, resume_execution, resume_run, finalize_rejection`

合同：`Confirmation / frozen AgentAction`。

调用链：确认冻结身份→WorkflowRun；MiniMax/组合/案例可确定性完成通知。

测试关联：

- [source/kernel/tests/test_agent_state_integrity.py](/home/mtd/liweigis/workflow/groundwater-v6/source/kernel/tests/test_agent_state_integrity.py:87) — `test_waiting_confirmation_projects_current_session_in_same_transaction, test_approved_action_reads_workflow_fact_without_reconciliation, test_trusted_direct_confirmation_retains_dispatch_authorization, test_agent_transition_and_event_roll_back_together, test_supersede_cas_emits_one_event, test_stale_terminal_transition_cannot_change_memory_or_emit_event, test_recovery_restores_previous_phase_without_duplicate_steps, test_recovery_rejects_impossible_persisted_phase, test_final_workflow_references_are_action_and_session_scoped, test_minimax_terminal_execution_is_projected_from_persisted_facts`

## E40 算子目录及实现

源码：

- [source/packages/operator-pack/catalog/index.json](/home/mtd/liweigis/workflow/groundwater-v6/source/packages/operator-pack/catalog/index.json:1)
- [source/packages/operator-pack/src/moperators/static_implementations.py](/home/mtd/liweigis/workflow/groundwater-v6/source/packages/operator-pack/src/moperators/static_implementations.py:23) — `_native, _qgis, _reference_grid, _remote_http, execute`

合同：`operator-manifest.v1 / IMPLEMENTATIONS`。

调用链：Catalog→Assembler→Worker→版本化函数；remote独立worker。

测试关联：

- [source/packages/operator-pack/tests/test_static_pack.py](/home/mtd/liweigis/workflow/groundwater-v6/source/packages/operator-pack/tests/test_static_pack.py:22) — `test_remote_http_smoke_does_not_import_native_numeric_runtime, test_native_smokes_stage_each_operator_version_in_its_own_directory, test_native_dependencies_exclude_duckdb_without_arrow_table_api, test_catalog_lock_smokes_and_static_mapping_are_complete, test_pack_contains_no_service_control_plane, test_all_manifest_artifacts_declare_formats_for_control_return_validation, test_conditional_output_cases_are_exhaustive_and_exact, test_data_convert_parameter_cases_cover_output_format_enum, test_parameter_output_cases_reject_invalid_metadata`

## E41 统计科学边界

源码：

- [source/packages/operator-pack/src/moperators/operators/mann_kendall.py](/home/mtd/liweigis/workflow/groundwater-v6/source/packages/operator-pack/src/moperators/operators/mann_kendall.py:13) — `_s_statistic, run`
- [source/packages/operator-pack/src/moperators/operators/seasonal_mann_kendall.py](/home/mtd/liweigis/workflow/groundwater-v6/source/packages/operator-pack/src/moperators/operators/seasonal_mann_kendall.py:29) — `run, _s, _variance`
- [source/packages/operator-pack/src/moperators/operators/statistics_pettitt.py](/home/mtd/liweigis/workflow/groundwater-v6/source/packages/operator-pack/src/moperators/operators/statistics_pettitt.py:27) — `run, _not_evaluable, _average_ranks`
- [source/domains/groundwater/6.2.0/workflows/monitoring-trend.workflow.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/workflows/monitoring-trend.workflow.json:1)

合同：`monitoring-trend workflow1.1 / statistical outputs`。

调用链：10节点含季节/突变/缺测/阈值；ACF算子未接主链。

测试关联：

- [source/packages/operator-pack/tests/test_monitoring_trend_expansion.py](/home/mtd/liweigis/workflow/groundwater-v6/source/packages/operator-pack/tests/test_monitoring_trend_expansion.py:64) — `test_pettitt_synthetic_shift_reports_interpretation_and_missingness, test_seasonal_mann_kendall_requires_opt_in_and_explicit_season, test_gap_runs_reports_contiguous_runs_separately_from_completeness, test_threshold_events_are_disabled_without_an_explicit_user_threshold, test_operator_schemas_keep_legacy_calls_and_allow_explicitly_disabled_branches`
- [source/tests/contracts/test_groundwater_monitoring_trend_expansion.py](/home/mtd/liweigis/workflow/groundwater-v6/source/tests/contracts/test_groundwater_monitoring_trend_expansion.py:63) — `test_monitoring_trend_adds_four_nodes_without_breaking_legacy_contract, test_optional_scientific_branches_require_explicit_decisions, test_expanded_outputs_describe_interpretation_limits`

## E42 水头与简化流场

源码：

- [source/packages/operator-pack/src/moperators/operators/head_calculate.py](/home/mtd/liweigis/workflow/groundwater-v6/source/packages/operator-pack/src/moperators/operators/head_calculate.py:9) — `run`
- [source/packages/operator-pack/src/moperators/operators/hydraulic_gradient.py](/home/mtd/liweigis/workflow/groundwater-v6/source/packages/operator-pack/src/moperators/operators/hydraulic_gradient.py:17) — `run`
- [source/packages/operator-pack/src/moperators/operators/darcy_velocity.py](/home/mtd/liweigis/workflow/groundwater-v6/source/packages/operator-pack/src/moperators/operators/darcy_velocity.py:18) — `_same_grid, run`
- [source/domains/groundwater/6.2.0/models/flow-field.model.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/models/flow-field.model.json:1)

合同：`head=elevation-depth / flow-field3.1`。

调用链：head→snapshot→IDW→gradient→Darcy/porosity。

测试关联：

- [source/packages/operator-pack/tests/test_flow_field_interpretation.py](/home/mtd/liweigis/workflow/groundwater-v6/source/packages/operator-pack/tests/test_flow_field_interpretation.py:87) — `test_head_calculation_reports_basis_units_and_computed_status, test_gradient_summary_defines_projected_grid_direction_and_limitations, test_velocity_summary_separates_darcy_flux_from_seepage_and_observation`

## E43 克里金诊断

源码：

- [source/packages/operator-pack/src/moperators/operators/kriging.py](/home/mtd/liweigis/workflow/groundwater-v6/source/packages/operator-pack/src/moperators/operators/kriging.py:24) — `Variogram, Grid, KrigingContext, _artifact, _value, _load_rows, _null_values, _number, _read_points, _pairwise_distances`
- [source/domains/groundwater/6.2.0/models/kriging.model.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/models/kriging.model.json:1)

合同：`kriging1.0 / 5 outputs`。

调用链：点→变异函数/预测方差/LOO/IDW对照。

测试关联：

- [source/packages/operator-pack/tests/test_kriging.py](/home/mtd/liweigis/workflow/groundwater-v6/source/packages/operator-pack/tests/test_kriging.py:71) — `test_universal_kriging_reproduces_planar_drift_and_loo_metrics, test_exact_mode_has_zero_variance_at_wells_and_positive_between_them, test_fixed_seed_auto_variogram_is_reproducible, test_invalid_kriging_inputs_fail_stably, test_outputs_keep_prediction_variance_diagnostics_loo_and_summary_separate`
- [source/tests/contracts/test_groundwater_kriging_capability.py](/home/mtd/liweigis/workflow/groundwater-v6/source/tests/contracts/test_groundwater_kriging_capability.py:30) — `test_kriging_operator_contract_has_five_auditable_outputs, test_groundwater_kriging_model_is_one_node_and_binds_exact_data, test_publications_explain_variance_loo_extrapolation_and_idw_boundary, test_product_ui_exposes_method_variogram_neighbor_and_exact_controls, test_agent_guidance_and_model_card_keep_scientific_limits_visible`

## E44 规则分类与脆弱性

源码：

- [source/packages/operator-pack/src/moperators/operators/table_classify.py](/home/mtd/liweigis/workflow/groundwater-v6/source/packages/operator-pack/src/moperators/operators/table_classify.py:159) — `Classification, Bound, IndicatorRule, Binding, _load_document, _number, _bound, _sort_key, _validate_no_overlap, _parse_rules`
- [source/packages/operator-pack/src/moperators/operators/drastic.py](/home/mtd/liweigis/workflow/groundwater-v6/source/packages/operator-pack/src/moperators/operators/drastic.py:154) — `Interval, ParsedConfig, _artifact, _value, _reject_nonstandard_constant, _read_config, _number, _interval, _sort_key, _validate_disjoint`

合同：`table-classification-rules.v1 / groundwater-drastic-config.v1`。

调用链：明确规则+来源+单位→分类；七对齐栅格→DRASTIC。

测试关联：

- [source/packages/operator-pack/tests/test_table_classify.py](/home/mtd/liweigis/workflow/groundwater-v6/source/packages/operator-pack/tests/test_table_classify.py:187) — `test_classifies_open_closed_boundaries_and_two_sided_ph_rules, test_preserves_exact_integer_boundaries_beyond_float_precision, test_reports_missing_non_numeric_out_of_range_and_unverified_rules, test_open_open_internal_boundary_is_explicitly_not_evaluated, test_rejects_unit_or_basis_mismatch, test_rejects_overlapping_class_bounds_including_equal_closed_boundary, test_rule_summary_preserves_sources_bounds_and_verification_status`
- [source/packages/operator-pack/tests/test_drastic.py](/home/mtd/liweigis/workflow/groundwater-v6/source/packages/operator-pack/tests/test_drastic.py:218) — `test_constant_ratings_match_manual_weighted_sum_and_classification, test_random_rasters_match_independent_numpy_oracle, test_nodata_propagates_and_rule_gaps_remain_uncovered, test_grid_mismatch_is_rejected_without_implicit_alignment, test_invalid_or_unverified_config_fails_stably`

## E45 迁移学习

源码：

- [source/packages/operator-pack/src/moperators/operators/transfer_data.py](/home/mtd/liweigis/workflow/groundwater-v6/source/packages/operator-pack/src/moperators/operators/transfer_data.py:21) — `unpivot, _groups, split, scaler_fit, _scaler, scaler_transform, scaler_inverse, windows, _metrics, _metric_comparison`
- [source/packages/operator-pack/src/moperators/operators/gru_attention.py](/home/mtd/liweigis/workflow/groundwater-v6/source/packages/operator-pack/src/moperators/operators/gru_attention.py:22) — `_seed, _model, inspect_weights, _restore, _train, _save, pretrain, finetune, predict`
- [source/domains/groundwater/6.2.0/workflows/transfer-learning-analysis.workflow.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/workflows/transfer-learning-analysis.workflow.json:1)
- [source/domains/groundwater/6.2.0/cases/yaozhanbao-transfer-case.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/cases/yaozhanbao-transfer-case.json:1)

合同：`transfer-learning3.1 / 19inputs / 58nodes`。

调用链：时间切分→训练段scaler→GRU→单步留出回测→空间图。

测试关联：

- [source/packages/operator-pack/tests/test_transfer_learning_audit_semantics.py](/home/mtd/liweigis/workflow/groundwater-v6/source/packages/operator-pack/tests/test_transfer_learning_audit_semantics.py:21) — `test_target_split_audits_boundaries_and_scaler_excludes_holdout, test_per_well_metrics_preserve_baseline_and_null_reasons, test_imagery_coverage_records_mask_basis_threshold_and_bands, test_map_month_diagnostics_record_effective_selection_and_selected_time`

## E46 Porous专业外部能力

源码：

- [third-party-services/porous-groundwater-linux-service/porous_remote/science.py](/home/mtd/liweigis/workflow/groundwater-v6/third-party-services/porous-groundwater-linux-service/porous_remote/science.py:23) — `_water_budget, _safe_definition, _runtime_definition, compile_model, compile_definition, simulate`
- [third-party-services/porous-groundwater-linux-service/porous_remote/calibration.py](/home/mtd/liweigis/workflow/groundwater-v6/third-party-services/porous-groundwater-linux-service/porous_remote/calibration.py:17) — `_number, _configuration, _scenario, _write_case, _forward, calibrate, main`
- [third-party-services/porous-groundwater-linux-service/porous_remote/inversion.py](/home/mtd/liweigis/workflow/groundwater-v6/third-party-services/porous-groundwater-linux-service/porous_remote/inversion.py:17) — `_integer, _number, _model_config, _runtime, _device_name, _model, _preprocess, _read_npy, _training_data, _normalize`
- [third-party-services/porous-groundwater-linux-service/porous_remote/numerics.py](/home/mtd/liweigis/workflow/groundwater-v6/third-party-services/porous-groundwater-linux-service/porous_remote/numerics.py:9) — `configure_transport, read_audit`
- [third-party-services/porous-groundwater-linux-service/README.md](/home/mtd/liweigis/workflow/groundwater-v6/third-party-services/porous-groundwater-linux-service/README.md:1)

合同：`remote-operation.v1 / porous-pest-head.v1 / conservative-afc.v1`。

调用链：远程worker→service runner→外置vendor/求解器；现场科学等价UNKNOWN。

测试关联：

- [third-party-services/porous-groundwater-linux-service/tests/test_remote_contract.py](/home/mtd/liweigis/workflow/groundwater-v6/third-party-services/porous-groundwater-linux-service/tests/test_remote_contract.py:52) — `test_health_version_and_authentication, test_request_field_error_does_not_echo_input_or_enqueue, test_all_fixed_operations_upload_reconcile_replay_conflict_and_cancel, test_upload_hash_and_input_identity_are_verified, test_service_ports_match_every_v6_operator_version, test_exported_request_contract_matches_adapter_and_rejects_wrong_ports, test_result_manifest_pins_artifact_bytes_formats_and_download_paths`
- [third-party-services/porous-groundwater-linux-service/tests/test_quality.py](/home/mtd/liweigis/workflow/groundwater-v6/third-party-services/porous-groundwater-linux-service/tests/test_quality.py:8) — `test_quality_checks_all_times_and_masks_without_changing_raw, test_empty_concentration_is_not_quality_passed`

## E47 水源地专题评价

源码：

- [source/packages/operator-pack/src/moperators/operators/sustainability_preparation.py](/home/mtd/liweigis/workflow/groundwater-v6/source/packages/operator-pack/src/moperators/operators/sustainability_preparation.py:43) — `_scope, _validate_indicator, prepare, _from_budget, _load, prepare_operator, main`
- [third-party-services/groundwater-sustainability-service/app/sustainability.py](/home/mtd/liweigis/workflow/groundwater-v6/third-party-services/groundwater-sustainability-service/app/sustainability.py:10) — `DemoContext, WeightConfiguration, SustainabilityInput, SustainabilityTaskInput, IndicatorScoreRange, SustainabilityIndicatorItem, SustainabilityIndicatorGroup, SustainabilityEvaluationResult, _range, _band`

合同：`sustainability-evaluation.v1.1 / remote-operation.v1`。

调用链：资料口径核对→候选JSON→另行确认→加权评价/凭据。

测试关联：

- [source/products/groundwater/tests/test_prepare_sustainability_input.py](/home/mtd/liweigis/workflow/groundwater-v6/source/products/groundwater/tests/test_prepare_sustainability_input.py:45) — `test_prepare_preserves_the_source_role_and_assessment_payload, test_prepare_blocks_scope_mismatch_and_simulated_monitoring_counts, test_prepare_blocks_duplicate_indicator_sources`
- [third-party-services/groundwater-sustainability-service/tests/test_sustainability.py](/home/mtd/liweigis/workflow/groundwater-v6/third-party-services/groundwater-sustainability-service/tests/test_sustainability.py:42) — `test_exploitation_coefficient_boundaries, test_indicator_score_ranges, test_midpoint_scores_are_weighted_and_guide_ranges_remain_visible, test_user_custom_weights_are_applied_to_both_hierarchy_levels, test_custom_weight_sums_must_equal_100_percent, test_custom_weights_are_supported_by_http_api_and_invalid_totals_are_structured_errors, test_http_api_omitting_weights_uses_guide_recommendations, test_midpoint_selection_produces_single_grade_when_range_crossed, test_grade_iii_uses_basic_sustainability_label, test_restriction_override_and_pending_review`

## E48 独立MODFLOW/Pastas试验

源码：

- [source/products/groundwater/research/modflow6_spike.py](/home/mtd/liweigis/workflow/groundwater-v6/source/products/groundwater/research/modflow6_spike.py:79) — `Modflow6SpikeError, _canonical_bytes, _digest_bytes, _file_digest, _is_within, _mapping, _exact_keys, _nonempty_string, _positive_int, _finite_float`
- [source/products/groundwater/research/pastas_spike.py](/home/mtd/liweigis/workflow/groundwater-v6/source/products/groundwater/research/pastas_spike.py:36) — `PastasSpikeError, _canonical_bytes, _digest_bytes, _digest, _file_digest, _is_within, _validated_paths, _mapping, _exact_keys, _nonempty_string`
- [source/products/groundwater/spikes/modflow6/README.md](/home/mtd/liweigis/workflow/groundwater-v6/source/products/groundwater/spikes/modflow6/README.md:1)
- [source/products/groundwater/spikes/pastas/README.md](/home/mtd/liweigis/workflow/groundwater-v6/source/products/groundwater/spikes/pastas/README.md:1)

合同：`spike config/result v1 / separate lock`。

调用链：外部输入→独立环境→合成基准；非18模型之一。

测试关联：

- [source/products/groundwater/tests/test_modflow6_spike.py](/home/mtd/liweigis/workflow/groundwater-v6/source/products/groundwater/tests/test_modflow6_spike.py:38) — `test_modflow6_config_freezes_the_analytical_boundary, test_flopy_is_locked_outside_every_default_runtime, test_mf6_binary_lock_and_dockerfile_reject_unapproved_supply_paths, test_official_mf6_release_archive_matches_every_locked_identity, test_modflow6_spike_refuses_runtime_state_inside_source_tree, test_flopy_312_and_mf6_681_solve_the_dockerized_linear_benchmark`
- [source/products/groundwater/tests/test_pastas_spike.py](/home/mtd/liweigis/workflow/groundwater-v6/source/products/groundwater/tests/test_pastas_spike.py:33) — `test_pastas_spike_config_freezes_the_scientific_boundary, test_pastas_dependency_is_pinned_outside_every_default_runtime, test_pastas_metrics_are_identical_to_the_gru_metric_contract, test_pastas_spike_refuses_runtime_state_inside_source_tree, test_gru_comparison_requires_the_same_split_rows_and_metrics, test_pastas_201_solves_an_independent_exponential_benchmark, test_pastas_spike_rejects_missing_stress_before_model_construction`

## E49 成果交付与验收

源码：

- [source/web/src/ArtifactViewer.tsx](/home/mtd/liweigis/workflow/groundwater-v6/source/web/src/ArtifactViewer.tsx:1)
- [source/web/src/WorkspacePanel.tsx](/home/mtd/liweigis/workflow/groundwater-v6/source/web/src/WorkspacePanel.tsx:1)
- [source/docs/reference/v12-research-acceptance-policy.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/docs/reference/v12-research-acceptance-policy.v1.json:1)
- [source/products/groundwater/research/research_package.py](/home/mtd/liweigis/workflow/groundwater-v6/source/products/groundwater/research/research_package.py:20) — `_digest, ResearchCitationV1, ResearchAnswerV1, GroundwaterModelIdentity, GroundwaterDomainPackageIdentity, ResearchProposalV1, build_reproducible_research_package`

合同：`Data RESULT / ResearchAnswerV1 / archive.v1`。

调用链：原子结果→viewer/下载→用户评审；归档≠完整研究报告。

测试关联：

- [source/web/src/card-review.test.tsx](/home/mtd/liweigis/workflow/groundwater-v6/source/web/src/card-review.test.tsx:1)
- [source/products/groundwater/tests/test_research.py](/home/mtd/liweigis/workflow/groundwater-v6/source/products/groundwater/tests/test_research.py:89) — `test_controlled_corpus_verifies_external_bytes_and_import_baseline, test_import_baseline_accepts_v6_version_response_shape, test_controlled_corpus_rejects_digest_escape_and_repo_local_sources, test_research_answer_and_package_require_exact_active_evidence`

## E50 执行和恢复所有权

源码：

- [source/services/executor/src/platform_executor_service/control.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/executor/src/platform_executor_service/control.py:18) — `ExecutorControl`
- [source/services/executor/src/platform_executor_service/task_execution.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/executor/src/platform_executor_service/task_execution.py:15) — `WorkerCapacity, ReadyTaskSpec, TaskLease, TaskOutcome, select_ready_tasks, require_lease, _tail`
- [source/services/executor/src/platform_executor_service/result_publication.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/executor/src/platform_executor_service/result_publication.py:15) — `result_model, publication_contract, PublicationSession, PublicationUpload`
- [source/services/executor/src/platform_executor_service/remote_http.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/executor/src/platform_executor_service/remote_http.py:26) — `RemoteOperationError, RemoteExecutionResult, _now, _file_identity, RemoteOperationRunner, execute_remote_operation`

合同：`Execution Protocol v6 / Worker v3 / schema9`。

调用链：Kernel WorkflowRun→Job→node/lease→Data commit→终态。

测试关联：

- [source/services/executor/tests/test_schema_contract.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/executor/tests/test_schema_contract.py:17) — `test_v12_keeps_executor_sqlite_v9_signature, test_stopped_v8_database_migrates_to_v9_without_losing_worker_rows`
- [source/services/executor/tests/test_remote_http.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/executor/tests/test_remote_http.py:116) — `test_operation_must_be_enabled_by_deployment_binding, test_remote_operation_uploads_once_verifies_results_and_writes_receipt, test_receipt_pins_operation_inputs_values_and_artifact_identity, test_remote_receipt_must_confirm_success_before_outputs_are_materialized, test_result_versions_must_match_the_version_handshake, test_ambiguous_submit_reconciles_without_blind_resubmission, test_download_checksum_mismatch_fails_closed, test_cancel_records_unconfirmed_remote_termination, test_worker_shutdown_leaves_submitted_operation_for_recovery, test_accepted_cancellation_reconciles_terminal_state`
- [source/services/executor/tests/test_orchestration_engine.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/executor/tests/test_orchestration_engine.py:20) — `test_success_unlocks_only_nodes_with_all_dependencies_satisfied, test_failure_skips_the_complete_downstream_closure, test_first_lease_loss_retries_and_second_loss_fails, test_finalization_requires_every_node_to_succeed`

## E51 前端状态与组件复用

源码：

- [source/web/src/ui-selectors.ts](/home/mtd/liweigis/workflow/groundwater-v6/source/web/src/ui-selectors.ts:1)
- [source/web/src/session-stream-state.ts](/home/mtd/liweigis/workflow/groundwater-v6/source/web/src/session-stream-state.ts:1)
- [source/web/src/task-state.ts](/home/mtd/liweigis/workflow/groundwater-v6/source/web/src/task-state.ts:1)
- [source/services/data/web/src/polling.ts](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/web/src/polling.ts:1)
- [source/services/knowledge-next/ui/src/polling.ts](/home/mtd/liweigis/workflow/groundwater-v6/source/services/knowledge-next/ui/src/polling.ts:1)

合同：`API v12 snapshot / SessionEvent v20`。

调用链：服务端快照/SSE→UI映射；本地仅草稿/消息队列。

测试关联：

- [source/web/src/ui-selectors.test.ts](/home/mtd/liweigis/workflow/groundwater-v6/source/web/src/ui-selectors.test.ts:1)
- [source/web/src/session-stream-state.test.ts](/home/mtd/liweigis/workflow/groundwater-v6/source/web/src/session-stream-state.test.ts:1)

## E52 独立轻量Agent交付面

源码：

- [source/packages/lightweight-agent-core/README.md](/home/mtd/liweigis/workflow/groundwater-v6/source/packages/lightweight-agent-core/README.md:1)
- [source/packages/lightweight-agent-core/src/platform_lightweight_agent/engine.py](/home/mtd/liweigis/workflow/groundwater-v6/source/packages/lightweight-agent-core/src/platform_lightweight_agent/engine.py:87) — `ProcessResourceDiagnostics, _ProcessResourceWaiter, _ProcessResourceEntry, _ProcessResourceRegistry, process_resource_diagnostics, _run_target, _event_target, _journal_get, _journal_status, _journal_events`
- [source/packages/lightweight-agent-core/src/platform_lightweight_agent/workflow_v3.py](/home/mtd/liweigis/workflow/groundwater-v6/source/packages/lightweight-agent-core/src/platform_lightweight_agent/workflow_v3.py:99) — `_TransitionAbortedError, _WaitingDecisionConsumedError, _ModelRequestCancelledError, _RecoveryQueryCancelledError, _RecoveryObservation, _recovery_limits, _optional_dependencies, _compile_schema, _compile_schema_json, _validation_issue`

合同：`lightweight package own contracts`。

调用链：独立Python嵌入包；默认Kernel/Compose未导入。

测试关联：

- [source/packages/lightweight-agent-core/pyproject.toml](/home/mtd/liweigis/workflow/groundwater-v6/source/packages/lightweight-agent-core/pyproject.toml:1)

## E53 部署生命周期与复杂度

源码：

- [source/scripts/maintenance/project_environment.py](/home/mtd/liweigis/workflow/groundwater-v6/source/scripts/maintenance/project_environment.py:47) — `EnvironmentRefusalError, _refusal, owned_path, layout, lifecycle, _workspace_identity, validation_cache_root, validation_state_root, socket_path, require_socket`
- [source/scripts/maintenance/dependency_materializer.py](/home/mtd/liweigis/workflow/groundwater-v6/source/scripts/maintenance/dependency_materializer.py:55) — `DependencyRefusalError, _now, root, _sha256, _npm_volatile, _files, _manifest_inputs_match, _candidate_scope, _reuse_scope, _run`
- [source/scripts/maintenance/gw.py](/home/mtd/liweigis/workflow/groundwater-v6/source/scripts/maintenance/gw.py:45) — `_version, doctor, _apply_or_plan, _instance, _clean, _engine, _config, _snapshot, _archive, _migrate`
- [source/docs/architecture/dependency-runtime-architecture.md](/home/mtd/liweigis/workflow/groundwater-v6/source/docs/architecture/dependency-runtime-architecture.md:1)

合同：`layout4 / XDG / locked dependencies`。

调用链：gw→plan→explicit apply；本轮未调用mutations。

测试关联：

- [source/tests/architecture/test_xdg_runtime_architecture.py](/home/mtd/liweigis/workflow/groundwater-v6/source/tests/architecture/test_xdg_runtime_architecture.py:31) — `test_xdg_roles_are_disjoint_and_resolution_has_no_side_effects, test_instance_identifiers_cannot_escape, test_layout_schema_four_has_no_repository_runtime_role, test_doctor_runs_without_runtime_or_project_environment, test_toolchain_location_is_bound_to_lock_digest, test_staged_toolchain_rewrites_absolute_console_script_paths`

## E54 有限Workflow组合

源码：

- [source/packages/workflow-compiler/src/platform_workflow_compiler/composition/execution.py](/home/mtd/liweigis/workflow/groundwater-v6/source/packages/workflow-compiler/src/platform_workflow_compiler/composition/execution.py:34) — `_adapter_digest, _logical_type, _port, _manifest, _set_raster_contract, _set_reference_grid_contract, raster_input_facts, _workflow_vocabulary, ArtifactComposition, execution_arguments`
- [source/kernel/src/platform_kernel/workflow_execution/composed.py](/home/mtd/liweigis/workflow/groundwater-v6/source/kernel/src/platform_kernel/workflow_execution/composed.py:14) — `resolve_composed`
- [source/services/executor/src/platform_executor_service/orchestration.py](/home/mtd/liweigis/workflow/groundwater-v6/source/services/executor/src/platform_executor_service/orchestration.py:7) — `NodeSnapshot, WorkflowSnapshot, JobAdmitted, TaskSucceeded, TaskFailed, LeaseLost, TransitionCommands, transition, finalization_ready, _failed`

合同：`workflow.v7 / composition profile / Execution v6`。

调用链：10个产品白名单算子→草稿验证→确认冻结→Executor重新编译。

测试关联：

- [source/tests/e2e/test_composed_workflow.py](/home/mtd/liweigis/workflow/groundwater-v6/source/tests/e2e/test_composed_workflow.py:348) — `test_agent_composes_confirms_executes_and_publishes`
- [source/packages/workflow-compiler/tests](/home/mtd/liweigis/workflow/groundwater-v6/source/packages/workflow-compiler/tests:1)
