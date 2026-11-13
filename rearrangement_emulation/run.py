"""Run the configured experiment against an already running defense server."""

from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rearrangement_emulation import settings
from rearrangement_emulation.runner import run_experiment


def main():
    return run_experiment(**settings.configuration())


if __name__ == "__main__":
    main()
