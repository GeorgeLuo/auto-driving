"""Independent frozen claim-map authority for M007-08 semantic citation.

The committed claim_map.json must match this constant exactly. Coordinated
rewrites of bindings, paths, or empty predicates cannot invent a pass.
"""

from __future__ import annotations

from typing import Any

# Canonical #88 catalog identity (immutable source of US meaning).
CANONICAL_US88_SOURCE = {
    "url": (
        "https://github.com/GeorgeLuo/auto-driving/pull/88"
        "#issuecomment-5169077892"
    ),
    "comment_id": 5169077892,
    "title": (
        "Prospective README appendix: usage sequences and human confirmation"
    ),
}

LIVE_ACCEPTANCE_RESULT = (
    "docs/milestones/007-cli-operator-usability/evidence/"
    "live-cli-acceptance/result.json"
)
CONTINUITY_RESULT = (
    "docs/milestones/007-cli-operator-usability/evidence/"
    "cli-scenario-continuity/result.json"
)

FROZEN_CLAIM_MAP: dict[str, Any] = {
    "schema": "m007_claim_map_v1",
    "us_claim_bindings": {
        "US-01": "us01_us02_live_acceptance",
        "US-02": "us01_us02_live_acceptance",
        "US-03": "continuity_offline_perception",
        "US-04": "continuity_live_config_swap",
        "US-05": "continuity_memory_lifecycle",
        "US-08": "continuity_memory_lifecycle",
    },
    "claims": {
        "us01_us02_live_acceptance": {
            "allowed_us_ids": ["US-01", "US-02"],
            "paths": [LIVE_ACCEPTANCE_RESULT],
            "source_pr": 88,
            "source_result_schema": "m007_live_cli_acceptance_v1",
            "predicates": [
                {"path": "result", "equals": "pass"},
                {"path": "schema", "equals": "m007_live_cli_acceptance_v1"},
                {"path": "cleanup.worker_stopped", "equals": True},
            ],
        },
        "continuity_offline_perception": {
            "allowed_us_ids": ["US-03"],
            "paths": [CONTINUITY_RESULT],
            "source_pr": 100,
            "source_result_schema": "m007_cli_scenario_continuity_v0",
            "predicates": [
                {"path": "result", "equals": "pass"},
                {"path": "schema", "equals": "m007_cli_scenario_continuity_v0"},
                {
                    "path": [
                        "family_aggregates",
                        "continuity.offline_perception",
                    ],
                    "equals": "passed",
                },
                {"path": "finalizer.ok", "equals": True},
                {"path": "hitl_complete", "equals": True},
            ],
        },
        "continuity_live_config_swap": {
            "allowed_us_ids": ["US-04"],
            "paths": [CONTINUITY_RESULT],
            "source_pr": 100,
            "source_result_schema": "m007_cli_scenario_continuity_v0",
            "predicates": [
                {"path": "result", "equals": "pass"},
                {
                    "path": [
                        "family_aggregates",
                        "continuity.live_config_swap",
                    ],
                    "equals": "passed",
                },
                {"path": "restore_ok", "equals": True},
                {"path": "finalizer.ok", "equals": True},
                {"path": "hitl_complete", "equals": True},
            ],
        },
        "continuity_memory_lifecycle": {
            "allowed_us_ids": ["US-05", "US-08"],
            "paths": [CONTINUITY_RESULT],
            "source_pr": 100,
            "source_result_schema": "m007_cli_scenario_continuity_v0",
            "predicates": [
                {"path": "result", "equals": "pass"},
                {
                    "path": [
                        "family_aggregates",
                        "continuity.memory_lifecycle",
                    ],
                    "equals": "passed",
                },
                {"path": "finalizer.ok", "equals": True},
                {"path": "hitl_complete", "equals": True},
            ],
        },
    },
}

# Usage-pattern vocabulary (must be referenced by overlay usage lists).
USAGE_PATTERNS: dict[str, str] = {
    "operator_status": "Passive discovery/status of simulator or vehicle layers",
    "primary_observe_only_journey": "Primary six-step observe-only automation journey",
    "offline_perception_feedback": "Capture/apply/compare offline perception experiments",
    "memory_lifecycle": "Memory check/reset/replay lifecycle operations",
    "stage_or_deploy": "Stage perception/decision/memory or deploy code",
    "plugin_management": "Enable/disable/setup perception plugins (mutates activation)",
    "inspection_stream": "Stream or info inspection of stages",
    "bounded_operation_check": "Bounded vehicle operation checks",
    "physical_or_lab_check": "Physical-check / qualify / viability paths",
    "simulator_prep": "Simulator ensure/status preparation",
    "general_cli_leaf": "Other public CLI terminal command",
    "utility_status": "General status utility",
}
