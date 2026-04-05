"""Tests for inverter telemetry archive persistence helpers."""

from __future__ import annotations

from datetime import datetime, timezone
import json

import telemetry.telemetry_archive as telemetry_archive


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


def test_append_inverter_telemetry_snapshot_flags_near_ceiling_clipping(tmp_path, monkeypatch) -> None:
    """Near-ceiling solar with full battery and export should still count as clipping risk."""
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
    assert snapshot["derived"]["likely_clipping"] is True
    assert snapshot["derived"]["clipping_confidence"] == "high"
    assert snapshot["derived"]["extracted_metrics"]["solar_power_kw"] == 5.2


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
