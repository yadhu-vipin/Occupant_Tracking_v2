"""CLI entry point for Stage-1 event generation.

    python pipeline/generate_events.py --seed 42

Thin wrapper over ``events/generator.py`` (``GenerationConfig`` +
``EventGenerator``), which already produces exactly 8,000 local + 2,000
visitor events by default (``visitor_ratio=0.20``, ``events_per_occupant=20``,
500 occupants = 10,000 total events); ``--seed`` fully parameterizes/
randomizes generation deterministically. No functional change to the
generator itself -- this file is "the one file someone runs" for event
generation.

Writes ``events/output/generated_events.json`` + ``events/output/ground_truth.json``
by default.
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from buildinglib.split import default_emb_path, default_meta_path        # noqa: E402
from events.generator import EventGenerator, GenerationConfig, print_report  # noqa: E402
from events.io import save_events                                          # noqa: E402

DEFAULT_OUTPUT_DIR = ROOT / "events" / "output"


def _normalise_clock(value: str) -> str:
    """Accept the documented HH:MM shorthand as well as HH:MM:SS."""
    if len(value) == 5:
        value = f"{value}:00"
    datetime.strptime(value, "%H:%M:%S")
    return value


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate Stage 1 events from existing TEST embeddings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--meta", type=Path, default=default_meta_path())
    parser.add_argument("--emb", type=Path, default=default_emb_path())
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--visitor-ratio", type=float, default=.20)
    parser.add_argument("--min-interval", type=int, default=30,
                        help="Minimum stochastic interval between this occupant's events, in seconds.")
    parser.add_argument("--max-interval", type=int, default=180,
                        help="Maximum stochastic interval between this occupant's events, in seconds.")
    parser.add_argument("--start-time", default="08:00:00", help="Inclusive HH:MM[:SS] simulation start.")
    parser.add_argument("--end-time", default="17:00:00", help="Inclusive HH:MM[:SS] simulation end.")
    parser.add_argument("--events-per-occupant", type=int, default=20)
    parser.add_argument("--occupant-limit", type=int, default=None)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    config = GenerationConfig(seed=args.seed, visitor_ratio=args.visitor_ratio,
                              min_interval=args.min_interval, max_interval=args.max_interval,
                              start_time=_normalise_clock(args.start_time),
                              end_time=_normalise_clock(args.end_time),
                              events_per_occupant=args.events_per_occupant,
                              occupant_limit=args.occupant_limit)
    events, truth = EventGenerator.from_corpus(args.meta, args.emb, config).generate()
    save_events(events, truth, args.output_dir)
    print_report(events, truth, config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
