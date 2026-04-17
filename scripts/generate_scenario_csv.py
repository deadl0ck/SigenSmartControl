"""generate_scenario_csv.py
---------------------------
Generate a deterministic multi-scenario CSV for read-only scheduler simulation.

The output file contains repeated 24-hour scenario blocks with the requested
columns only: Hour, SOC, Forecast, Current Mode.
"""

import argparse
import csv
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from logic.scenario_simulation import build_default_scenario_templates, generate_scenario_rows


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed CLI arguments.
    """
    parser = argparse.ArgumentParser(
        description="Generate a CSV of deterministic 24-hour scenario sets."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/scenario_inputs.csv"),
        help="Output CSV path. Defaults to data/scenario_inputs.csv.",
    )
    return parser.parse_args()


def write_csv(output_path: Path) -> None:
    """Write scenario rows to a CSV file.

    Args:
        output_path: Destination CSV path.
    """
    rows = generate_scenario_rows()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Hour", "SOC", "Forecast", "Current Mode"])
        writer.writeheader()
        writer.writerows(rows)

    scenario_count = len(build_default_scenario_templates())
    print(
        f"Wrote {len(rows)} rows across {scenario_count} scenario sets to {output_path}"
    )


def main() -> None:
    """Run the scenario CSV generator CLI."""
    args = parse_args()
    write_csv(args.output)


if __name__ == "__main__":
    main()