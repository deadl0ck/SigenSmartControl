"""config.enums

Enum types for period names and forecast statuses used throughout the scheduler.

Using ``str, Enum`` means existing string comparisons and dict lookups continue
to work without modification during gradual adoption across the codebase.
"""

from enum import Enum


class Period(str, Enum):
    """Canonical scheduler period names."""

    MORN = "Morn"
    AFTN = "Aftn"
    EVE = "Eve"
    NIGHT = "Night"


class ForecastStatus(str, Enum):
    """Normalised solar forecast status values returned by all forecast providers."""

    RED = "RED"
    AMBER = "AMBER"
    GREEN = "GREEN"
