#!/usr/bin/env python3
"""Small offline temporal-obstacle CLI and Jev contract bridge.

The ``contract`` mode is deliberately a narrow audit helper.  It serializes
the already-computed temporal evidence, runs a deterministic local fixture,
and optionally makes one bounded provider request.  It never changes detector
or tracker output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.perception.durable_obstacles import (
    local_durability_corrected,
    replay_capture,
)


TEMPORAL_DURABILITY_QUESTION = (
    "Does this candidate have sufficient temporal evidence to represent one "
    "persistent physical obstruction during its supported visible interval "
    "while the observer moves? Judge coherent identity and motion/scale trends, "
    "not exact rectangle equality. Consider the supplied counterevidence and "
    "evidenced changes in visibility. Predictions and duplicate images are not "
    "independent confirmation. An unsupported jump, unresolved competing "
    "identity or transient boundary cannot establish durability. Persistent "
    "background edges alone do not prove an obstruction. Choose durable, "
    "rejected or uncertain; identify supporting and contradicting observations "
    "when the interface allows. Do not infer image content that was not supplied."
)
DEFAULT_JEV_URL = "https://api.typesafe.ai/v1/systemone"
CONTRACT_SCHEMA = "jev_temporal_durability_contract_v1"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"expected JSON object row: {path}")
            rows.append(value)
    return rows


def _observation_view(observation: dict[str, Any], track_id: str) -> dict[str, Any]:
    """Keep the provider packet tied to original observation evidence."""
    return {
        "observation_id": observation.get("observation_id"),
        "track_id": track_id,
        "frame_id": observation.get("frame_id"),
        "position": observation.get("position"),
        "timestamp_ms": observation.get("timestamp_ms"),
        "image_sha256": observation.get("image_sha256"),
        "detection_id": observation.get("detection_id"),
        "bbox_xyxy_norm": observation.get("bbox_xyxy_norm"),
        "source_status": observation.get("source_status"),
        "is_actual": bool(observation.get("is_actual", observation.get("source_status") == "observed")),
        "association": observation.get("association"),
    }


def _candidate_view(decision: dict[str, Any]) -> dict[str, Any]:
    track_id = str(decision.get("track_id"))
    observations = [
        _observation_view(item, track_id)
        for item in decision.get("observations", [])
        if isinstance(item, dict)
    ]
    actual_observations = [item for item in observations if item.get("is_actual")]
    actual_ids = [
        str(item.get("observation_id"))
        for item in actual_observations
        if item.get("observation_id") is not None
    ]
    unique_image_hashes = sorted({
        str(item.get("image_sha256"))
        for item in actual_observations
        if item.get("image_sha256")
    })
    unique_image_count = len(unique_image_hashes)
    timestamps = [
        float(item["timestamp_ms"])
        for item in actual_observations
        if item.get("timestamp_ms") is not None
    ]
    duration_seconds = (max(timestamps) - min(timestamps)) / 1000.0 if timestamps else None
    visible_interval = decision.get("visible_interval") if isinstance(decision.get("visible_interval"), dict) else {}
    metrics = decision.get("metrics") if isinstance(decision.get("metrics"), dict) else {}
    local_checks = decision.get("checks") if isinstance(decision.get("checks"), dict) else {}

    def retained_check(name: str) -> dict[str, Any] | None:
        value = local_checks.get(name)
        return dict(value) if isinstance(value, dict) else None

    independent_check = retained_check("minimum_independent_observations")
    duration_check = retained_check("minimum_time_span")
    fraction_check = retained_check("minimum_supported_fraction")
    denominator = visible_interval.get("eligible_frame_count")
    try:
        denominator_value = float(denominator)
    except (TypeError, ValueError):
        denominator_value = 0.0
    derived_supported_fraction = unique_image_count / denominator_value if denominator_value > 0 else None

    def threshold(check: dict[str, Any] | None) -> float | None:
        try:
            return float(check["threshold"]) if check and check.get("threshold") is not None else None
        except (TypeError, ValueError):
            return None

    independent_threshold = threshold(independent_check)
    duration_threshold = threshold(duration_check)
    fraction_threshold = threshold(fraction_check)
    support_authorization_checks = {
        "minimum_independent_observations": {
            "retained": independent_check,
            "measured_unique_image_count": unique_image_count,
            "passed": bool(
                independent_check
                and independent_check.get("passed") is True
                and independent_threshold is not None
                and unique_image_count >= independent_threshold
            ),
        },
        "minimum_time_span": {
            "retained": duration_check,
            "measured_duration_seconds": duration_seconds,
            "passed": bool(
                duration_check
                and duration_check.get("passed") is True
                and duration_threshold is not None
                and duration_seconds is not None
                and duration_seconds >= duration_threshold
            ),
        },
        "minimum_supported_fraction": {
            "retained": fraction_check,
            "measured_unique_image_fraction": derived_supported_fraction,
            "passed": bool(
                fraction_check
                and fraction_check.get("passed") is True
                and fraction_threshold is not None
                and derived_supported_fraction is not None
                and derived_supported_fraction >= fraction_threshold
            ),
        },
    }
    material_competition = decision.get("material_competition_evidence")
    if not isinstance(material_competition, list):
        material_competition = []
    hard_failures = [
        name
        for name in decision.get("failed_checks", [])
        if name in {
            "maximum_gap_duration",
            "maximum_local_center_trend_residual",
            "maximum_local_scale_residual",
        }
    ]
    if material_competition:
        hard_failures.append("material_competition")
    return {
        "track_id": track_id,
        "local_verdict": decision.get("verdict"),
        "observed_interval": {
            "start_frame_id": visible_interval.get("start_frame_id"),
            "end_frame_id": visible_interval.get("end_frame_id"),
            "eligible_frame_count": visible_interval.get("eligible_frame_count"),
            "actual_frame_count": visible_interval.get("actual_frame_count", unique_image_count),
            "supported_fraction": visible_interval.get("supported_fraction"),
            "duration_seconds": duration_seconds,
        },
        "independent_temporal_support": {
            "actual_observation_ids": actual_ids,
            "actual_observation_count": len(actual_ids),
            "unique_image_hashes": unique_image_hashes,
            "unique_image_count": unique_image_count,
            "independent_support_count": unique_image_count,
            "denominator": denominator,
            "supported_fraction": derived_supported_fraction,
            "predicted_observation_count": decision.get("prediction_count", 0),
            "held_observation_count": decision.get("held_count", 0),
            "support_authorization_checks": support_authorization_checks,
        },
        "observations": observations,
        "motion_scale_evidence": {
            "metrics": metrics,
            "local_window_evidence": decision.get("local_window_evidence"),
        },
        "gaps": decision.get("gaps", []),
        "counterevidence": decision.get("counterevidence", []),
        "failed_checks": decision.get("failed_checks", []),
        "hard_contradictions": hard_failures,
        "policy_checks": local_checks,
        "material_competition_evidence": material_competition,
    }


def build_contract_packet(
    *,
    sequence_id: str,
    tracks_path: Path,
    detections_path: Path,
    tracking_config_path: Path,
    frozen_inputs_path: Path | None,
    images_provided: bool = False,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Build one provider-neutral request from retained local evidence."""
    tracks_payload = _load_json(tracks_path)
    detections = _load_jsonl(detections_path)
    tracking_config = _load_json(tracking_config_path)
    frozen_inputs = _load_json(frozen_inputs_path) if frozen_inputs_path else {}
    decisions = [item for item in tracks_payload.get("tracks", []) if isinstance(item, dict)]
    if not decisions:
        raise ValueError("tracks JSON contains no candidate decisions")

    observations_by_id: dict[str, dict[str, Any]] = {}
    for decision in decisions:
        track_id = str(decision.get("track_id"))
        for observation in decision.get("observations", []):
            if isinstance(observation, dict) and observation.get("observation_id") is not None:
                view = _observation_view(observation, track_id)
                observations_by_id[str(view["observation_id"])] = view

    frames: list[dict[str, Any]] = []
    for row in detections:
        frame_observations: list[dict[str, Any]] = []
        for thing_index, thing in enumerate(row.get("things", [])):
            if not isinstance(thing, dict):
                continue
            observation_id = f"{row.get('frame_id')}:{thing_index}"
            linked = observations_by_id.get(observation_id)
            if linked is None:
                linked = {
                    "observation_id": observation_id,
                    "track_id": None,
                    "frame_id": row.get("frame_id"),
                    "position": row.get("position"),
                    "timestamp_ms": row.get("timestamp_ms"),
                    "image_sha256": row.get("image_sha256"),
                    "detection_id": thing.get("thing_id"),
                    "bbox_xyxy_norm": thing.get("bbox_xyxy_norm"),
                    "source_status": (thing.get("properties") or {}).get("track_event", "observed"),
                    "is_actual": (thing.get("properties") or {}).get("track_event", "observed") not in {"predicted", "held"},
                    "association": None,
                }
            frame_observations.append(linked)
        frames.append({
            "position": row.get("position"),
            "frame_id": row.get("frame_id"),
            "timestamp_ms": row.get("timestamp_ms"),
            "image_sha256": row.get("image_sha256"),
            "observations": frame_observations,
        })

    source_hashes: dict[str, str] = {
        "tracks_sha256": _sha256(tracks_path),
        "detections_sha256": _sha256(detections_path),
        "tracking_config_sha256": _sha256(tracking_config_path),
    }
    if frozen_inputs_path:
        source_hashes["frozen_inputs_sha256"] = _sha256(frozen_inputs_path)
        if frozen_inputs.get("manifest_sha256"):
            source_hashes["manifest_sha256"] = str(frozen_inputs["manifest_sha256"])
        if frozen_inputs.get("config_sha256"):
            source_hashes["detector_config_sha256"] = str(frozen_inputs["config_sha256"])
    if tracks_payload.get("source_detections_sha256"):
        source_hashes["tracks_source_detections_sha256"] = str(tracks_payload["source_detections_sha256"])

    packet = {
        "schema_version": CONTRACT_SCHEMA,
        "sequence_id": sequence_id,
        "coordinate_convention": "xyxy_norm",
        "timestamp_units": "milliseconds",
        "images_provided": bool(images_provided),
        "source_hashes": source_hashes,
        "state": {
            "frames": frames,
            "candidates": [_candidate_view(decision) for decision in decisions],
            "tracking_policy": tracking_config,
        },
        "question": {
            "id": "temporal_durability",
            "text": TEMPORAL_DURABILITY_QUESTION,
            "answer_values": ["durable", "rejected", "uncertain"],
        },
        "answer_contract": {
            "track_id": "must match a candidate track_id",
            "verdict": "durable|rejected|uncertain",
            "supporting_observation_ids": "optional IDs from that candidate's actual observations",
            "contradicting_observation_ids": "optional IDs from that candidate's observations",
            "reason": "optional provider explanation; absent values stay absent",
        },
    }
    return packet, source_hashes


def _candidate_index(packet: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(candidate["track_id"]): candidate
        for candidate in packet.get("state", {}).get("candidates", [])
        if isinstance(candidate, dict) and candidate.get("track_id") is not None
    }


def _extract_answers(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        return {}
    answers = response.get("answers")
    if isinstance(answers, dict):
        return {str(key): value for key, value in answers.items()}
    results = response.get("results")
    if isinstance(results, list):
        return {
            str(item.get("track_id")): item
            for item in results
            if isinstance(item, dict) and item.get("track_id") is not None
        }
    return {}


def _answer_verdict(answer: Any) -> tuple[str | None, str | None]:
    if isinstance(answer, str):
        value = answer.lower()
        return (value, None) if value in {"durable", "rejected", "uncertain"} else (None, "unsupported_verdict")
    if not isinstance(answer, dict):
        return None, "malformed_answer"
    raw_verdict = answer.get("verdict")
    if isinstance(raw_verdict, str):
        value = raw_verdict.lower()
        return (value, None) if value in {"durable", "rejected", "uncertain"} else (None, "unsupported_verdict")
    # This is only a compatibility mapping for a provider that exposes a
    # boolean field.  A missing or non-boolean value remains uncertain.
    value = answer.get("value")
    if isinstance(value, bool):
        return ("durable" if value else "rejected"), "boolean_compatibility_mapping"
    return None, "missing_verdict"


def _durable_authorization_failures(candidate: dict[str, Any]) -> list[str]:
    """Return retained local checks that prevent a provider durable answer."""
    support = candidate.get("independent_temporal_support")
    if not isinstance(support, dict):
        return ["missing_independent_temporal_support"]
    failures: list[str] = []
    unique_image_count = support.get("unique_image_count")
    unique_image_hashes = support.get("unique_image_hashes")
    if not isinstance(unique_image_hashes, list) or not unique_image_hashes:
        failures.append("unique_image_support")
    if not isinstance(unique_image_count, int) or unique_image_count < 1:
        failures.append("unique_image_support")
    elif isinstance(unique_image_hashes, list) and len(set(unique_image_hashes)) != unique_image_count:
        failures.append("unique_image_support")
    checks = support.get("support_authorization_checks")
    if not isinstance(checks, dict):
        return failures + [
            "minimum_independent_observations",
            "minimum_time_span",
            "minimum_supported_fraction",
        ]
    for name in (
        "minimum_independent_observations",
        "minimum_time_span",
        "minimum_supported_fraction",
    ):
        check = checks.get(name)
        if not isinstance(check, dict) or check.get("passed") is not True:
            failures.append(name)
    return sorted(set(failures))


def normalize_response(
    packet: dict[str, Any],
    response: Any,
    *,
    decision_source: str,
    request_sha256: str,
    raw_response_reference: str,
) -> dict[str, Any]:
    """Map one response without allowing prose or unsupported IDs to emit boxes."""
    candidates = _candidate_index(packet)
    answers = _extract_answers(response)
    results: list[dict[str, Any]] = []
    for track_id, candidate in candidates.items():
        answer = answers.get(track_id)
        verdict, mapping_note = _answer_verdict(answer)
        actual_ids = set(candidate.get("independent_temporal_support", {}).get("actual_observation_ids", []))
        known_ids = {
            str(item.get("observation_id"))
            for item in candidate.get("observations", [])
            if isinstance(item, dict) and item.get("observation_id") is not None
        }
        supporting = answer.get("supporting_observation_ids") if isinstance(answer, dict) else None
        contradicting = answer.get("contradicting_observation_ids") if isinstance(answer, dict) else None
        mapping_warnings: list[str] = []
        if supporting is not None and not isinstance(supporting, list):
            mapping_warnings.append("malformed_supporting_observation_ids")
            supporting = []
        if contradicting is not None and not isinstance(contradicting, list):
            mapping_warnings.append("malformed_contradicting_observation_ids")
            contradicting = []
        supporting = [str(item) for item in (supporting or [])]
        contradicting = [str(item) for item in (contradicting or [])]
        if any(item not in known_ids for item in supporting + contradicting):
            mapping_warnings.append("unknown_observation_id")
        supporting = [item for item in supporting if item in actual_ids]
        contradicting = [item for item in contradicting if item in known_ids]

        hard_contradictions = candidate.get("hard_contradictions", [])
        authorization_failures = _durable_authorization_failures(candidate)
        if verdict == "durable" and (authorization_failures or hard_contradictions or mapping_warnings):
            verdict = "uncertain"
            mapping_note = "durable_answer_withheld_by_local_evidence"
        if verdict is None:
            verdict = "uncertain"
            mapping_note = mapping_note or "missing_or_malformed_answer"
        result: dict[str, Any] = {
            "track_id": track_id,
            "verdict": verdict,
            "decision_source": decision_source,
            "request_sha256": request_sha256,
            "raw_response_reference": raw_response_reference,
            "raw_answer": answer,
            "supporting_observation_ids": supporting,
            "contradicting_observation_ids": contradicting,
        }
        if mapping_note:
            result["mapping_note"] = mapping_note
        if mapping_warnings:
            result["mapping_warnings"] = mapping_warnings
        if authorization_failures:
            result["authorization_failures"] = authorization_failures
        results.append(result)
    return {
        "schema_version": "jev_temporal_durability_normalized_v1",
        "sequence_id": packet.get("sequence_id"),
        "request_sha256": request_sha256,
        "decision_source": decision_source,
        "raw_response_reference": raw_response_reference,
        "unmapped_answer_ids": sorted(set(answers) - set(candidates)),
        "results": results,
    }


def build_fixture(packet: dict[str, Any], source_request_sha256: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Make one deterministic supported/withheld-durable round trip."""
    candidates = list(_candidate_index(packet).values())
    supported = next((item for item in candidates if item.get("local_verdict") == "durable"), candidates[0])
    insufficient = next(
        (
            item
            for item in candidates
            if item.get("track_id") != supported.get("track_id")
            and _durable_authorization_failures(item)
        ),
        None,
    )
    if insufficient is None:
        insufficient = next((item for item in candidates if item.get("track_id") != supported.get("track_id")), None)
    if insufficient is None:
        insufficient = {
            "track_id": "fixture-uncertain",
            "local_verdict": "uncertain",
            "independent_temporal_support": {"actual_observation_ids": [], "unique_image_count": 0},
            "observations": [],
            "hard_contradictions": ["unsupported_fixture_candidate"],
        }
    fixture_input = {
        "schema_version": "jev_temporal_durability_fixture_input_v1",
        "source_sequence_id": packet.get("sequence_id"),
        "source_request_sha256": source_request_sha256 or hashlib.sha256(_canonical_json(packet).encode()).hexdigest(),
        "question": packet["question"],
        "cases": {
            "supported": str(supported["track_id"]),
            "provider_durable_but_locally_unsupported": str(insufficient["track_id"]),
        },
        "candidates": [supported, insufficient],
    }
    supported_ids = supported.get("independent_temporal_support", {}).get("actual_observation_ids", [])
    insufficient_ids = insufficient.get("independent_temporal_support", {}).get("actual_observation_ids", [])
    fixture_response = {
        "schema_version": "jev_temporal_durability_fixture_response_v1",
        "answers": {
            str(supported["track_id"]): {
                "verdict": "durable",
                "supporting_observation_ids": list(supported_ids[: min(3, len(supported_ids))]),
                "reason": "fixture-supported coherent temporal evidence",
            },
            str(insufficient["track_id"]): {
                "verdict": "durable",
                "supporting_observation_ids": list(insufficient_ids[: min(3, len(insufficient_ids))]),
                "reason": "fixture provider answer deliberately conflicts with insufficient retained support",
            },
        },
    }
    return fixture_input, fixture_response


def _redacted_error(exc: BaseException) -> str:
    """Keep provider errors useful without ever serializing request headers."""
    return f"{type(exc).__name__}: {exc}"


def _persist_live_response(output_dir: Path, raw: bytes) -> tuple[Any, dict[str, Any]]:
    """Retain exact bytes and separately provide a readable parsed copy."""
    raw_path = output_dir / "jev-response.raw"
    raw_path.write_bytes(raw)
    details: dict[str, Any] = {
        "response_path": str(raw_path),
        "response_sha256": _sha256(raw_path),
    }
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        details["parse_error"] = _redacted_error(exc)
        return None, details
    parsed_path = output_dir / "jev-response.json"
    _write_json(parsed_path, payload)
    details["parsed_response_path"] = str(parsed_path)
    return payload, details


def _offline_status(request_sha256: str, url: str) -> dict[str, Any]:
    return {
        "schema_version": "jev_status_v1",
        "status": "disabled",
        "reason": "live_opt_in_required",
        "interface_status": "unverified",
        "live_opt_in": False,
        "request_sha256": request_sha256,
        "endpoint": url,
        "timeout_seconds": 30,
        "request_count": 0,
    }


def run_live_request(packet: dict[str, Any], output_dir: Path, url: str, request_sha256: str | None = None) -> dict[str, Any]:
    """Attempt at most one 30-second request through an unverified interface."""
    request_sha256 = request_sha256 or hashlib.sha256(_canonical_json(packet).encode()).hexdigest()
    api_key = os.environ.get("JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY")
    status: dict[str, Any] = {
        "schema_version": "jev_status_v1",
        "status": "not_evaluated",
        "reason": "missing_api_key",
        "interface_status": "unverified",
        "live_opt_in": True,
        "request_sha256": request_sha256,
        "endpoint": url,
        "timeout_seconds": 30,
        "request_count": 0,
    }
    if not api_key:
        return status
    body = _canonical_json(packet).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    status["request_count"] = 1
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            payload, response_details = _persist_live_response(output_dir, raw)
            status.update({
                "http_status": int(response.status),
                **response_details,
            })
            if payload is None:
                status.update({"status": "not_evaluated", "reason": "malformed_response"})
            elif not _extract_answers(payload):
                status.update({"status": "not_evaluated", "reason": "response_missing_answers"})
            else:
                status["status"] = "evaluated"
            return {"status": status, "payload": payload}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        payload, response_details = _persist_live_response(output_dir, raw)
        status.update({
            "status": "not_evaluated",
            "reason": "http_error",
            "http_status": int(exc.code),
            **response_details,
            "error": _redacted_error(exc),
        })
        return {"status": status, "payload": payload}
    except Exception as exc:
        status.update({"status": "not_evaluated", "reason": "transport_or_parse_error", "error": _redacted_error(exc)})
        return {"status": status, "payload": None}


def run_contract(
    *,
    sequence_id: str,
    tracks_path: Path,
    detections_path: Path,
    tracking_config_path: Path,
    frozen_inputs_path: Path | None,
    output_dir: Path,
    jev_url: str,
    live: bool = False,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=False)
    packet, source_hashes = build_contract_packet(
        sequence_id=sequence_id,
        tracks_path=tracks_path,
        detections_path=detections_path,
        tracking_config_path=tracking_config_path,
        frozen_inputs_path=frozen_inputs_path,
    )
    request_path = output_dir / "jev-request.json"
    _write_json(request_path, packet)
    request_sha256 = _sha256(request_path)
    _write_json(output_dir / "input-hashes.json", {
        "request_sha256": request_sha256,
        "source_hashes": source_hashes,
        "paths": {
            "tracks": str(tracks_path),
            "detections": str(detections_path),
            "tracking_config": str(tracking_config_path),
            "frozen_inputs": str(frozen_inputs_path) if frozen_inputs_path else None,
        },
    })

    fixture_input, fixture_response = build_fixture(packet, source_request_sha256=request_sha256)
    _write_json(output_dir / "fixture-input.json", fixture_input)
    _write_json(output_dir / "fixture-response.json", fixture_response)
    fixture_normalized = normalize_response(
        packet,
        fixture_response,
        decision_source="fixture",
        request_sha256=request_sha256,
        raw_response_reference="fixture-response.json",
    )
    _write_json(output_dir / "normalized-results.json", fixture_normalized)

    live_result = (
        run_live_request(packet, output_dir, jev_url, request_sha256=request_sha256)
        if live
        else {"status": _offline_status(request_sha256, jev_url), "payload": None}
    )
    status = live_result["status"]
    if live_result.get("payload") is not None and status.get("status") == "evaluated":
        live_normalized = normalize_response(
            packet,
            live_result["payload"],
            decision_source="jev_live",
            request_sha256=request_sha256,
            raw_response_reference=str(status.get("response_path")),
        )
        _write_json(output_dir / "live-normalized-results.json", live_normalized)
    _write_json(output_dir / "jev-status.json", status)
    return {
        "sequence_id": sequence_id,
        "frame_count": len(packet["state"]["frames"]),
        "candidate_count": len(packet["state"]["candidates"]),
        "request_sha256": request_sha256,
        "fixture_verdicts": {
            verdict: sum(item["verdict"] == verdict for item in fixture_normalized["results"])
            for verdict in ("durable", "rejected", "uncertain")
        },
        "jev_status": status["status"],
        "jev_reason": status.get("reason"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--detector-config", type=Path)
    parser.add_argument("--detections", type=Path)
    parser.add_argument("--tracking-config", type=Path)
    parser.add_argument("--tracks", type=Path)
    parser.add_argument("--frozen-inputs", type=Path)
    parser.add_argument("--sequence-id")
    parser.add_argument("--jev-url", default=DEFAULT_JEV_URL)
    parser.add_argument(
        "--live",
        action="store_true",
        help="explicitly opt into one bounded request through the unverified Jev interface",
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--mode", choices=["baseline", "local", "contract"], required=True)
    args = parser.parse_args()
    if args.mode == "baseline":
        if not args.manifest or not args.detector_config:
            parser.error("baseline requires --manifest and --detector-config")
        result = replay_capture(args.manifest, args.detector_config, args.output_dir)
        print(f"baseline: {args.output_dir} frames={result['frame_count']} failures={len(result['failures'])}")
    elif args.mode == "local":
        if not args.detections:
            parser.error("local requires --detections")
        result = local_durability_corrected(args.detections, args.output_dir, args.tracking_config)
        print(f"local: {args.output_dir} frames={result['frame_count']} tracks={result['track_count']} verdicts={result['verdict_counts']}")
    else:
        required = {
            "--tracks": args.tracks,
            "--detections": args.detections,
            "--tracking-config": args.tracking_config,
            "--sequence-id": args.sequence_id,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            parser.error(f"contract requires {', '.join(missing)}")
        result = run_contract(
            sequence_id=args.sequence_id,
            tracks_path=args.tracks,
            detections_path=args.detections,
            tracking_config_path=args.tracking_config,
            frozen_inputs_path=args.frozen_inputs,
            output_dir=args.output_dir,
            jev_url=args.jev_url,
            live=args.live,
        )
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
