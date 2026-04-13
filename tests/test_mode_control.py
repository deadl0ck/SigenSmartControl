"""Tests for mode-control parsing helpers."""

from logic.mode_control import extract_mode_value


def test_extract_mode_value_maps_sigen_ai_mode_label() -> None:
    """Map observed 'Sigen AI Mode' label to numeric AI mode."""
    assert extract_mode_value("Sigen AI Mode") == 1


def test_extract_mode_value_maps_signe_ai_mode_label_alias() -> None:
    """Map typo variant 'Signe AI Mode' to numeric AI mode."""
    assert extract_mode_value("Signe AI Mode") == 1


def test_extract_mode_value_maps_dict_label() -> None:
    """Map dict label payloads to numeric mode values."""
    assert extract_mode_value({"label": "Sigen AI Mode"}) == 1
