"""Tests for inverter telemetry archive persistence helpers.

Also covers the private scoring and collection helpers:
_candidate_score, _collect_numeric_fields, and _extract_numeric_metric.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

import telemetry.telemetry_archive as telemetry_archive
from telemetry.telemetry_archive import (
    _candidate_score,
    _collect_numeric_fields,
    _extract_numeric_metric,
)


def test_append_inverter_telemetry_snapshot(tmp_path, monkeypatch) -> None:
    """Telemetry snapshots should be appended as JSONL records."""
    archive_path = tmp_path / "inverter_telemetry.jsonl"
    monkeypatch.setattr(telemetry_archive, "INVERTER_TELEMETRY_ARCHIVE_PATH", str(archive_path))

    telemetry_archive.append_inverter_telemetry_snapshot(
        energy_flow={"batterySoc": 81, "pvPower": 4200},
        operational_mode="Sigen AI Mode",
        reason="scheduler_tick",
        scheduler_now_utc=datetime(2026, 4, 3, 10, 30, tzinfo=timezone.utc),
        forecast_today={"Aftn": (2118, "Amber")},
        forecast_tomorrow={"Morn": (1139, "Red")},
    )

    snapshot_lines = archive_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(snapshot_lines) == 1

    snapshot = json.loads(snapshot_lines[0])
    assert snapshot["reason"] == "scheduler_tick"
    assert snapshot["operational_mode"] == "Sigen AI Mode"
    assert snapshot["energy_flow"]["batterySoc"] == 81
    assert snapshot["derived"]["likely_clipping"] is False
    assert snapshot["forecast_today"]["Aftn"] == [2118, "Amber"]


def test_append_inverter_telemetry_snapshot_flags_likely_clipping(tmp_path, monkeypatch) -> None:
    """Derived metrics should flag likely clipping near the inverter ceiling."""
    archive_path = tmp_path / "inverter_telemetry.jsonl"
    monkeypatch.setattr(telemetry_archive, "INVERTER_TELEMETRY_ARCHIVE_PATH", str(archive_path))

    telemetry_archive.append_inverter_telemetry_snapshot(
        energy_flow={
            "batterySoc": 100,
            "pvPower": 5500,
            "batteryPower": 0,
            "gridExportPower": 4820,
        },
        operational_mode="Sigen AI Mode",
        reason="scheduler_tick",
        scheduler_now_utc=datetime(2026, 4, 3, 10, 30, tzinfo=timezone.utc),
    )

    snapshot = json.loads(archive_path.read_text(encoding="utf-8").strip())
    assert snapshot["derived"]["likely_clipping"] is True
    assert snapshot["derived"]["clipping_confidence"] == "high"
    assert snapshot["derived"]["extracted_metrics"]["solar_power_kw"] == 5.5


def test_append_inverter_telemetry_snapshot_does_not_flag_near_ceiling_only(tmp_path, monkeypatch) -> None:
    """Near-ceiling-only samples should not be flagged when clipping requires exact ceiling output."""
    archive_path = tmp_path / "inverter_telemetry.jsonl"
    monkeypatch.setattr(telemetry_archive, "INVERTER_TELEMETRY_ARCHIVE_PATH", str(archive_path))

    telemetry_archive.append_inverter_telemetry_snapshot(
        energy_flow={
            "batterySoc": 98,
            "pvPower": 5200,
            "batteryPower": 0.1,
            "gridExportPower": 4500,
        },
        operational_mode="Sigen AI Mode",
        reason="scheduler_tick",
        scheduler_now_utc=datetime(2026, 4, 3, 10, 30, tzinfo=timezone.utc),
    )

    snapshot = json.loads(archive_path.read_text(encoding="utf-8").strip())
    assert snapshot["derived"]["likely_clipping"] is False
    assert snapshot["derived"]["clipping_confidence"] == "low"
    assert snapshot["derived"]["extracted_metrics"]["solar_power_kw"] == 5.2


def test_append_inverter_telemetry_snapshot_does_not_flag_above_ceiling(tmp_path, monkeypatch) -> None:
    """Above-ceiling readings should not be treated as clipping under the exact-ceiling rule."""
    archive_path = tmp_path / "inverter_telemetry.jsonl"
    monkeypatch.setattr(telemetry_archive, "INVERTER_TELEMETRY_ARCHIVE_PATH", str(archive_path))

    telemetry_archive.append_inverter_telemetry_snapshot(
        energy_flow={
            "batterySoc": 100,
            "pvPower": 5600,
            "batteryPower": 0,
            "gridExportPower": 5000,
        },
        operational_mode="Sigen AI Mode",
        reason="scheduler_tick",
        scheduler_now_utc=datetime(2026, 4, 3, 10, 30, tzinfo=timezone.utc),
    )

    snapshot = json.loads(archive_path.read_text(encoding="utf-8").strip())
    assert snapshot["derived"]["likely_clipping"] is False
    assert snapshot["derived"]["clipping_confidence"] == "low"
    assert snapshot["derived"]["extracted_metrics"]["solar_power_kw"] == 5.6


def test_extract_live_solar_power_kw_handles_watts_and_kw() -> None:
    """Live solar extraction should normalize both W and kW payloads."""
    watts_payload = {"pvPower": 4200}
    kw_payload = {"solarPower": 4.2}

    assert telemetry_archive.extract_live_solar_power_kw(watts_payload) == 4.2
    assert telemetry_archive.extract_live_solar_power_kw(kw_payload) == 4.2


def test_append_mode_change_event(tmp_path, monkeypatch) -> None:
    """Mode-change events should be appended as JSONL records."""
    archive_path = tmp_path / "mode_change_events.jsonl"
    monkeypatch.setattr(telemetry_archive, "MODE_CHANGE_EVENTS_ARCHIVE_PATH", str(archive_path))

    telemetry_archive.append_mode_change_event(
        scheduler_now_utc=datetime(2026, 4, 3, 11, 45, tzinfo=timezone.utc),
        period="Morn (pre-period)",
        requested_mode=5,
        requested_mode_label="GRID_EXPORT",
        reason="Headroom below target",
        simulated=True,
        success=True,
        current_mode={"mode": 0},
        response={"simulated": True, "mode": 5},
    )

    lines = archive_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1

    event = json.loads(lines[0])
    assert event["period"] == "Morn (pre-period)"
    assert event["requested_mode"] == 5
    assert event["requested_mode_label"] == "GRID_EXPORT"
    assert event["simulated"] is True
    assert event["success"] is True


# ---------------------------------------------------------------------------
# _candidate_score tests
# ---------------------------------------------------------------------------


def test_candidate_score_exact_leaf_match_returns_100() -> None:
    """Exact leaf match against a candidate returns the maximum score of 100."""
    path = ("data", "pvPower")
    candidates = ("pvPower",)
    assert _candidate_score(path, candidates) == 100


def test_candidate_score_partial_leaf_match_returns_80() -> None:
    """Candidate substring found inside the leaf (but not an exact match) returns 80."""
    # leaf is "pvPowerInstant"; compact_candidate "pvpower" is contained within compact_leaf
    path = ("data", "pvPowerInstant")
    candidates = ("pvPower",)
    assert _candidate_score(path, candidates) == 80


def test_candidate_score_match_only_in_joined_path_returns_60() -> None:
    """Candidate found in the joined path but NOT in the leaf alone returns 60."""
    # leaf is "value", joined path is "pvpower.value" which contains "pvpower"
    path = ("pvpower", "value")
    candidates = ("pvPower",)
    score = _candidate_score(path, candidates)
    assert score == 60


def test_candidate_score_no_match_returns_0() -> None:
    """Path with no candidate match anywhere returns 0."""
    path = ("device", "temperature")
    candidates = ("pvPower", "solarPower")
    assert _candidate_score(path, candidates) == 0


def test_candidate_score_underscore_normalisation() -> None:
    """Underscore-normalised candidate 'pv_power' matches camelCase leaf 'pvPower'."""
    # compact_candidate = "pvpower", compact_leaf = "pvpower" → exact → 100
    path = ("data", "pvPower")
    candidates = ("pv_power",)
    assert _candidate_score(path, candidates) == 100


def test_candidate_score_empty_candidates_returns_0() -> None:
    """Empty candidate tuple always returns 0 regardless of path."""
    path = ("data", "pvPower")
    assert _candidate_score(path, ()) == 0


def test_candidate_score_multiple_candidates_highest_score_wins() -> None:
    """When multiple candidates match at different levels, the highest score is returned."""
    # "soc" is an exact leaf match (100), "solar" only appears in joined path of a different leaf
    path = ("battery", "soc")
    candidates = ("solar", "soc")
    assert _candidate_score(path, candidates) == 100


def test_candidate_score_single_element_path() -> None:
    """A single-element path uses that element as both the leaf and the full joined string."""
    path = ("pvPower",)
    candidates = ("pvPower",)
    assert _candidate_score(path, candidates) == 100


# ---------------------------------------------------------------------------
# _collect_numeric_fields tests
# ---------------------------------------------------------------------------


def test_collect_numeric_fields_basic_nested_dict() -> None:
    """Basic nested dict returns correct (path, value) pairs for numeric leaves."""
    payload = {"data": {"pvPower": 3.5, "batterySoc": 72}}
    results = _collect_numeric_fields(payload)
    paths = {path: value for path, value in results}
    assert ("data", "pvPower") in paths
    assert paths[("data", "pvPower")] == pytest.approx(3.5)
    assert ("data", "batterySoc") in paths
    assert paths[("data", "batterySoc")] == pytest.approx(72.0)


def test_collect_numeric_fields_max_depth_guard() -> None:
    """A dict nested deeper than max_depth stops recursion; leaf beyond the limit is absent."""
    # Build a 13-level deep nested dict (default max_depth=10, so leaf is beyond the cutoff)
    deep: dict = {"leaf": 42.0}
    for _ in range(13):
        deep = {"child": deep}

    results = _collect_numeric_fields(deep, max_depth=10)
    values = [v for _, v in results]
    # The deeply-nested 42.0 must not appear in collected values
    assert 42.0 not in values


def test_collect_numeric_fields_non_numeric_values_excluded() -> None:
    """Strings and booleans are excluded; only int/float leaves are returned."""
    payload = {
        "label": "green",   # string — must be excluded
        "active": True,     # bool — must be excluded
        "inactive": False,  # bool — must be excluded
        "power": 5.0,       # float — must be included
        "index": 3,         # int — must be included
    }
    results = _collect_numeric_fields(payload)
    paths = {path: value for path, value in results}

    assert ("label",) not in paths
    assert ("active",) not in paths
    assert ("inactive",) not in paths
    assert ("power",) in paths
    assert paths[("power",)] == pytest.approx(5.0)
    assert ("index",) in paths
    assert paths[("index",)] == pytest.approx(3.0)


def test_collect_numeric_fields_list_items_indexed() -> None:
    """List items are collected with their numeric index string in the path."""
    payload = {"readings": [1.1, 2.2, 3.3]}
    results = _collect_numeric_fields(payload)
    paths = {path: value for path, value in results}

    assert ("readings", "0") in paths
    assert paths[("readings", "0")] == pytest.approx(1.1)
    assert ("readings", "2") in paths
    assert paths[("readings", "2")] == pytest.approx(3.3)


def test_collect_numeric_fields_empty_dict_returns_empty() -> None:
    """An empty dict returns an empty list."""
    assert _collect_numeric_fields({}) == []


def test_collect_numeric_fields_flat_dict_excludes_non_numeric() -> None:
    """A flat dict with a mix of types returns only the two numeric leaves."""
    payload = {"a": 1, "b": "hello", "c": 2.5, "d": None}
    results = _collect_numeric_fields(payload)
    paths = {path: value for path, value in results}
    assert len(paths) == 2
    assert ("a",) in paths
    assert ("c",) in paths


# ---------------------------------------------------------------------------
# _extract_numeric_metric integration test
# ---------------------------------------------------------------------------


def test_extract_numeric_metric_realistic_energy_flow_pv_power() -> None:
    """Realistic energy_flow payload with pvPower nested under 'data' returns correct value."""
    energy_flow = {
        "code": 200,
        "msg": "success",
        "data": {
            "pvPower": 4200,       # reported in W; should be the top-ranked match
            "batterySoc": 85,
            "batteryPower": 500,
            "gridExchange": -300,
        },
    }
    candidates = ("pvPower", "solarPower", "ppv", "pv", "solar")
    result = _extract_numeric_metric(energy_flow, candidates)

    assert result is not None
    path_str, value = result
    assert "pvPower" in path_str or "pvpower" in path_str.lower()
    assert value == pytest.approx(4200.0)
