"""Tests for inverter telemetry archive persistence helpers."""

from __future__ import annotations

from datetime import datetime, timezone
import json

import telemetry_archive


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
