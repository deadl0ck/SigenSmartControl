"""evaluate_scenario_csv.py
---------------------------
Evaluate a scenario CSV against the project's pure decision engine.

This script reads Hour, SOC, Forecast, and Current Mode rows, derives the
recommended scheduler action for each hour, and writes an annotated output CSV.
"""

import argparse
import csv
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from logic.scenario_simulation import annotate_scenario_rows


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed CLI arguments.
    """
    parser = argparse.ArgumentParser(
        description="Evaluate a scenario CSV without sending real inverter commands."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/scenario_inputs.csv"),
        help="Input CSV path. Defaults to data/scenario_inputs.csv.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/scenario_outputs.csv"),
        help="Output CSV path. Defaults to data/scenario_outputs.csv.",
    )
    return parser.parse_args()


def load_csv(input_path: Path) -> list[dict[str, str]]:
    """Load raw scenario rows from CSV.

    Args:
        input_path: Source CSV path.

    Returns:
        List of raw CSV row dictionaries.
    """
    with input_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return [dict(row) for row in reader]


def write_csv(output_path: Path, rows: list[dict[str, Any]]) -> None:
    """Write annotated scenario rows to CSV.

    Args:
        output_path: Destination CSV path.
        rows: Annotated output rows.
    """
    if not rows:
        raise ValueError("No rows to write.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    """Run the scenario evaluator CLI."""
    args = parse_args()
    raw_rows = load_csv(args.input)
    annotated_rows = annotate_scenario_rows(raw_rows)
    write_csv(args.output, annotated_rows)
    print(f"Wrote {len(annotated_rows)} evaluated rows to {args.output}")


if __name__ == "__main__":
    main()