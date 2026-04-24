"""Tests for CSV-driven read-only scenario simulation."""

from logic.scenario_simulation import (
    annotate_scenario_rows,
    derive_period_for_hour,
    evaluate_scenario_row,
    generate_scenario_rows,
)


def test_generate_scenario_rows_creates_full_24_hour_blocks() -> None:
    rows = generate_scenario_rows()

    assert len(rows) == 240
    assert rows[0] == {
        "Hour": "00:00",
        "SOC": rows[0]["SOC"],
        "Forecast": "RED",
        "Current Mode": rows[0]["Current Mode"],
    }
    assert rows[23]["Hour"] == "23:00"
    assert rows[24]["Hour"] == "00:00"


def test_generate_scenario_rows_forces_red_during_night_window() -> None:
    rows = generate_scenario_rows()
    night_hours = {"23:00", "00:00", "01:00", "02:00", "03:00", "04:00", "05:00", "06:00", "07:00"}

    for row in rows:
        if row["Hour"] in night_hours:
            assert row["Forecast"] == "RED"


def test_derive_period_for_hour_maps_night_and_daytime_hours() -> None:
    assert derive_period_for_hour("07:00") == "Night"
    assert derive_period_for_hour("08:00") == "Morn"
    assert derive_period_for_hour("12:00") == "Aftn"
    assert derive_period_for_hour("21:00") == "Eve"
    assert derive_period_for_hour("23:00") == "Night"


def test_evaluate_scenario_row_uses_tou_during_night() -> None:
    result = evaluate_scenario_row(
        hour_text="23:00",
        soc=45.0,
        forecast="RED",
        current_mode="SELF_POWERED",
    )

    assert result["Period"] == "Night"
    assert result["Target Mode"] == "TOU"
    assert result["Action"] == "CHANGE_MODE"


def test_evaluate_scenario_row_detects_no_change_when_mode_matches() -> None:
    result = evaluate_scenario_row(
        hour_text="13:00",
        soc=50.0,
        forecast="AMBER",
        current_mode="SELF_POWERED",
    )

    assert result["Target Mode"] == "SELF_POWERED"
    assert result["Action"] == "KEEP_CURRENT_MODE"


def test_evaluate_scenario_row_uses_non_bridge_path_when_usable_energy_is_low() -> None:
    result = evaluate_scenario_row(
        hour_text="16:00",
        soc=20.0,
        forecast="GREEN",
        current_mode="SELF_POWERED",
    )

    assert result["Period"] == "Eve"
    assert result["Target Mode"] == "SELF_POWERED"
    assert result["Reason"] == "Default mapping for GREEN."


def test_annotate_scenario_rows_adds_scenario_set_numbers() -> None:
    rows = [
        {"Hour": f"{hour:02d}:00", "SOC": "50", "Forecast": "RED" if hour < 8 or hour >= 23 else "AMBER", "Current Mode": "SELF_POWERED"}
        for hour in range(24)
    ]
    rows += rows

    annotated_rows = annotate_scenario_rows(rows)

    assert annotated_rows[0]["Scenario Set"] == 1
    assert annotated_rows[23]["Scenario Set"] == 1
    assert annotated_rows[24]["Scenario Set"] == 2