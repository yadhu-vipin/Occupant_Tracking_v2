"""Merge gate: are these buildings' filters actually compatible?

    python merge_check.py out/*.npz
    python merge_check.py out/ --summary bloom_summary.csv

Run this when the team sits down to merge. It refuses a set of artifacts whose
federation contracts disagree -- because a mismatched building does not crash
anything downstream, it just silently never matches.

Collects every failure before reporting, so one bad artifact cannot hide a
second. Exit codes: 0 ok, 2 incompatible set, 3 nothing to check.
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from buildinglib.artifact import load_building, theoretical_fpr   # noqa: E402
from buildinglib.params import ContractMismatch, load_params       # noqa: E402

GEOMETRY_FIELDS = ("k", "L", "embed_dim")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", type=Path,
                    help="artifact .npz files, or a directory containing them")
    ap.add_argument("--shared", type=Path, default=None,
                    help="also require every artifact to match the local shared/ contract")
    ap.add_argument("--summary", type=Path, default=None,
                    help="write a bloom_summary.csv of the merged set")
    return ap.parse_args(argv)


def collect(paths):
    """Expand directories into the .npz files they hold."""
    found = []
    for p in paths:
        if p.is_dir():
            found.extend(sorted(p.glob("*.npz")))
        elif p.exists():
            found.append(p)
    return found


def verify_federation(npz_paths, expected_params_hash=None):
    """``(rows, problems)`` -- one row per artifact, and every problem found."""
    rows, problems = [], []

    for path in npz_paths:
        try:
            bf = load_building(path)
        except (ContractMismatch, OSError) as exc:
            problems.append(f"{path.name}: unreadable -- {exc}")
            continue
        rows.append({
            "path": path, "building": bf.building_id, "params_hash": bf.params_hash,
            "n_occupants": bf.n_occupants, "n_items": bf.n_items,
            "m": bf.bloom.m, "n_hashes": bf.bloom.n_hashes, "seed": bf.bloom.seed,
            "k": bf.k, "L": bf.L, "embed_dim": bf.embed_dim,
            "fill_ratio": round(bf.fill_ratio, 6),
            "theoretical_fpr": round(theoretical_fpr(bf.bloom.m, bf.n_items), 8),
            "size_bytes": path.stat().st_size,
        })

    if not rows:
        return rows, problems

    # 1. one contract across the whole set
    tally = Counter(r["params_hash"] for r in rows)
    if len(tally) > 1:
        winner, n = tally.most_common(1)[0]
        for r in rows:
            if r["params_hash"] != winner:
                problems.append(
                    f"{r['path'].name}: params_hash {r['params_hash'][:16]}... != "
                    f"{winner[:16]}... ({n} of {len(rows)} buildings agree on the latter)")

    # 2. and optionally that it is *our* contract, not a shared mistake
    if expected_params_hash and rows[0]["params_hash"] != expected_params_hash:
        if len(tally) == 1:
            problems.append(
                f"all {len(rows)} artifacts agree with each other but disagree with the "
                f"local shared/params.json:\n    artifacts {rows[0]['params_hash'][:16]}..."
                f"\n    local     {expected_params_hash[:16]}...")

    # 3. geometry has to line up too (params_hash should cover it, belt and braces)
    for field in GEOMETRY_FIELDS:
        vals = {r[field] for r in rows}
        if len(vals) > 1:
            problems.append(f"artifacts disagree on {field}: {sorted(vals)}")

    # 4. one filter per building
    for building, n in Counter(r["building"] for r in rows).items():
        if n > 1:
            problems.append(f"{building} appears {n} times -- duplicate artifacts")

    # 5. filename should say which building it is
    for r in rows:
        if r["path"].stem != r["building"]:
            problems.append(
                f"{r['path'].name} contains building_id {r['building']!r} -- renamed file?")

    return rows, problems


def main(argv=None):
    args = parse_args(argv)
    paths = collect(args.paths)
    if not paths:
        print("[3] no .npz artifacts found", file=sys.stderr)
        return 3

    expected = None
    try:
        expected = load_params(args.shared).params_hash
    except ContractMismatch as exc:
        print(f"note: local shared/ unreadable, checking artifacts against each other "
              f"only ({exc})\n")

    rows, problems = verify_federation(paths, expected)

    print(f"{'building':<14}{'occ':>5}{'items':>7}{'m':>9}{'h':>3}"
          f"{'fill':>8}{'fpr':>9}{'bytes':>8}  contract")
    for r in sorted(rows, key=lambda r: r["building"]):
        print(f"{r['building']:<14}{r['n_occupants']:>5}{r['n_items']:>7}{r['m']:>9}"
              f"{r['n_hashes']:>3}{r['fill_ratio']:>8.4f}{r['theoretical_fpr']:>9.5f}"
              f"{r['size_bytes']:>8}  {r['params_hash'][:12]}...")

    if args.summary and rows:
        import csv
        cols = ["building", "n_occupants", "n_items", "m", "n_hashes", "fill_ratio",
                "theoretical_fpr", "size_bytes", "params_hash"]
        with open(args.summary, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
            w.writeheader()
            w.writerows(sorted(rows, key=lambda r: r["building"]))
        print(f"\nwrote {args.summary}")

    if problems:
        print(f"\n[2] {len(problems)} problem(s) -- this set is NOT safe to merge:",
              file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 2

    print(f"\nOK -- {len(rows)} buildings, one contract "
          f"({rows[0]['params_hash'][:16]}...), safe to merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
