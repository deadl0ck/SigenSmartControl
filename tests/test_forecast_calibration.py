"""Tests for daily bounded forecast calibration derived from telemetry."""

from __future__ import annotations

from datetime import datetime, timezone
import json

import telemetry.forecast_calibration as forecast_calibration


def test_build_and_save_forecast_calibration_adjusts_period_settings(tmp_path, monkeypatch) -> None:
    """Recent telemetry should conservatively nudge period calibration upward."""
    telemetry_path = tmp_path / "inverter_telemetry.jsonl"
    calibration_path = tmp_path / "forecast_calibration.json"
    monkeypatch.setattr(forecast_calibration, "INVERTER_TELEMETRY_ARCHIVE_PATH", str(telemetry_path))
    monkeypatch.setattr(forecast_calibration, "FORECAST_CALIBRATION_PATH", str(calibration_path))

    snapshots = [
        {
            "captured_at": "2026-04-03T13:00:00+01:00",
            "energy_flow": {"pvPower": 5500, "batterySoc": 100, "batteryPower": 0, "gridExportPower": 4800},
            "forecast_today": {"Aftn": [3000, "Amber"]},
        },
        {
            "captured_at": "2026-04-02T13:15:00+01:00",
            "energy_flow": {"pvPower": 5200, "batterySoc": 98, "batteryPower": 0.1, "gridExportPower": 4500},
            "forecast_today": {"Aftn": [3200, "Amber"]},
        },
    ]
    telemetry_path.write_text("\n".join(json.dumps(item) for item in snapshots) + "\n", encoding="utf-8")

    calibration = forecast_calibration.build_and_save_forecast_calibration(
        now_utc=datetime(2026, 4, 3, 12, 0, tzinfo=timezone.utc)
    )

    aftn = calibration["periods"]["Aftn"]
    assert aftn["telemetry_samples"] == 2
    assert aftn["clipping_observations"] == 2
    assert aftn["power_multiplier"] > 1.0
    assert aftn["export_lead_buffer_multiplier"] > 1.1
    assert calibration_path.exists()


def test_build_and_save_forecast_calibration_limits_daily_step(tmp_path, monkeypatch) -> None:
    """Calibration changes should be bounded even if telemetry suggests a large jump."""
    telemetry_path = tmp_path / "inverter_telemetry.jsonl"
    calibration_path = tmp_path / "forecast_calibration.json"
    monkeypatch.setattr(forecast_calibration, "INVERTER_TELEMETRY_ARCHIVE_PATH", str(telemetry_path))
    monkeypatch.setattr(forecast_calibration, "FORECAST_CALIBRATION_PATH", str(calibration_path))

    prior = forecast_calibration.default_forecast_calibration()
    prior["periods"]["Morn"]["power_multiplier"] = 1.0
    calibration_path.write_text(json.dumps(prior), encoding="utf-8")

    telemetry_path.write_text(
        json.dumps(
            {
                "captured_at": "2026-04-03T08:00:00+01:00",
                "energy_flow": {"pvPower": 5500, "batterySoc": 100, "batteryPower": 0, "gridExportPower": 4800},
                "forecast_today": {"Morn": [1500, "Red"]},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    calibration = forecast_calibration.build_and_save_forecast_calibration(
        now_utc=datetime(2026, 4, 3, 12, 0, tzinfo=timezone.utc)
    )

    # Large ratio should still move only by the configured max daily step.
    assert calibration["periods"]["Morn"]["power_multiplier"] == 1.08