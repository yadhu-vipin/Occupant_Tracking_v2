"""The federation contract -- the parameters every building must share, byte for byte.

Two buildings' Bloom filters are only comparable if these match exactly:

    seed              -> the same random hyperplanes (and the same Bloom salts)
    k                 -> the same ``space = 2**k`` item namespace
    L                 -> the same number of code slots
    mean face         -> the same centering vector, so codes live in one space
    sizing_occupants  -> the same filter geometry (m, n_hashes)

``sizing_occupants`` is what the Bloom filter is *sized* for, not what a building
actually holds. Pinning it does two things: every artifact comes out the same
size, so the file no longer publishes the building's headcount; and a building
with fewer occupants ends up below the target false-positive rate rather than
above it, which is the safe direction (Bloom filters have no false negatives, so
a real match still always hits).

Any difference and the codes silently stop matching -- nothing crashes, routing
just quietly stops working. So the four are hashed into one ``params_hash`` that
travels inside every artifact, and ``merge_check.py`` refuses a set of filters
whose hashes disagree.

``shared/params.json`` declares the contract; ``shared/mean_face.npy`` is the
vector itself. :func:`load_params` re-derives both hashes from what is actually
on disk, so a swapped mean face or an edited json is caught at build time.

Also guards the vendored copies of ``lsh.py`` / ``bloom.py`` against drifting
from ``lane_a/core/``.

    python -m buildinglib.params            # verify everything
    python -m buildinglib.params --resync   # print hashes to paste after an
                                            # intentional vendored-file update
"""
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SCHEMA_VERSION = 1

PKG_DIR = Path(__file__).resolve().parent          # buildinglib/
BUILDINGS_DIR = PKG_DIR.parent                     # lane_a/buildings/
SHARED_DIR = BUILDINGS_DIR / "shared"
VENDORED_DIR = PKG_DIR / "_vendored"

# The fields that make two buildings' codes comparable. Changing any of these
# invalidates every filter in the federation.
CONTRACT_FIELDS = ("seed", "k", "L", "embed_dim", "sizing_occupants")

# SHA-256 of each vendored module, so an accidental edit on either side is loud.
# Regenerate with `python -m buildinglib.params --resync` after a deliberate
# re-vendor from lane_a/core/.
VENDORED_SHA256 = {
    "lsh.py": "8c184b0e5ae0b1718cc5e29fc62d34a1361ab712729d48c5f93ba367669b7c1e",
    "bloom.py": "af59291f68e307de921edc36a0181b5ad208c0bf8927270e7064973dd181533b",
}


class ContractMismatch(RuntimeError):
    """Raised when on-disk state disagrees with the declared contract."""


def sha256_file(path):
    """Hex SHA-256 of a file's raw bytes. For binary files (the mean face)."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sha256_source(path):
    """Hex SHA-256 of a text file's content, line-ending-agnostic.

    ``read_text`` collapses ``\\r\\n`` and lone ``\\r`` to ``\\n`` (universal
    newlines), so a Windows checkout that turned the vendored source into CRLF
    hashes the same as the LF original. A genuine code edit still changes the
    content and is still caught.
    """
    return hashlib.sha256(
        Path(path).read_text(encoding="utf-8").encode("utf-8")
    ).hexdigest()


def compute_params_hash(seed, k, L, embed_dim, sizing_occupants, mean_face_sha256):
    """The one string that has to match across every building.

    Canonical JSON (sorted keys, no whitespace) so the hash is stable regardless
    of how the json file happens to be formatted.
    """
    payload = json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "seed": int(seed),
            "k": int(k),
            "L": int(L),
            "embed_dim": int(embed_dim),
            "sizing_occupants": int(sizing_occupants),
            "mean_face_sha256": mean_face_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, eq=False)
class Params:
    """The loaded, verified contract. ``mean_face`` is the (1, dim) centering vector."""

    schema_version: int
    seed: int
    k: int
    L: int
    embed_dim: int
    target_fpr: float
    refs_per_occupant: int
    sizing_occupants: int
    mean_face_sha256: str
    params_hash: str
    mean_face: np.ndarray

    @property
    def space(self):
        """``2**k`` -- the width of one slot's value range in the item namespace."""
        return 2 ** self.k

    @property
    def sizing_items(self):
        """The item count every building's filter is sized for, whatever it holds."""
        return self.sizing_occupants * self.L

    def summary(self):
        return (f"k={self.k} L={self.L} dim={self.embed_dim} seed={self.seed} "
                f"target_fpr={self.target_fpr} refs={self.refs_per_occupant} "
                f"sized_for={self.sizing_occupants} occ "
                f"params_hash={self.params_hash[:12]}...")


def check_vendored(raise_on_mismatch=True):
    """Verify the vendored modules still match their recorded hashes.

    Returns the dict of actual hashes either way, so ``--resync`` can print them.
    """
    actual = {name: sha256_source(VENDORED_DIR / name) for name in VENDORED_SHA256}
    drifted = {n: (VENDORED_SHA256[n], a) for n, a in actual.items() if a != VENDORED_SHA256[n]}
    if drifted and raise_on_mismatch:
        lines = [f"  {n}\n    recorded {rec}\n    actual   {act}" for n, (rec, act) in drifted.items()]
        raise ContractMismatch(
            "vendored module(s) drifted from the recorded hash:\n"
            + "\n".join(lines)
            + "\n\nThis compares CODE, not line endings -- so it means someone actually"
            "\nedited a vendored file. Don't: edit lane_a/core/ and re-copy. If you"
            "\nre-vendored deliberately, run  python -m buildinglib.params --resync"
            "\nand paste the printed VENDORED_SHA256 block into buildinglib/params.py."
        )
    return actual


def load_params(shared_dir=None):
    """Read ``shared/params.json`` + ``shared/mean_face.npy`` and verify both.

    Raises :class:`ContractMismatch` if the mean face has been swapped, the json
    has been hand-edited, or the schema version is unknown.
    """
    shared = Path(shared_dir) if shared_dir else SHARED_DIR
    json_path, mean_path = shared / "params.json", shared / "mean_face.npy"

    for p in (json_path, mean_path):
        if not p.exists():
            raise ContractMismatch(f"missing shared contract file: {p}")

    declared = json.loads(json_path.read_text(encoding="utf-8"))

    if declared.get("schema_version") != SCHEMA_VERSION:
        raise ContractMismatch(
            f"params.json schema_version={declared.get('schema_version')} but this "
            f"package speaks version {SCHEMA_VERSION}"
        )

    # the mean face on disk must be the one the contract was hashed over
    actual_mean_sha = sha256_file(mean_path)
    if actual_mean_sha != declared["mean_face_sha256"]:
        raise ContractMismatch(
            "shared/mean_face.npy does not match the contract:\n"
            f"    declared {declared['mean_face_sha256']}\n"
            f"    actual   {actual_mean_sha}\n"
            "Someone replaced the mean face. Every filter built against the other "
            "one is incompatible."
        )

    # and the declared params_hash must be the hash of the declared fields
    recomputed = compute_params_hash(
        declared["seed"], declared["k"], declared["L"], declared["embed_dim"],
        declared["sizing_occupants"], declared["mean_face_sha256"],
    )
    if recomputed != declared["params_hash"]:
        raise ContractMismatch(
            "params.json has been edited without updating params_hash:\n"
            f"    declared   {declared['params_hash']}\n"
            f"    recomputed {recomputed}"
        )

    mean_face = np.load(mean_path)
    expected_shape = (1, declared["embed_dim"])
    if mean_face.shape != expected_shape:
        raise ContractMismatch(
            f"mean_face.npy has shape {mean_face.shape}, expected {expected_shape}"
        )

    return Params(
        schema_version=declared["schema_version"],
        seed=int(declared["seed"]),
        k=int(declared["k"]),
        L=int(declared["L"]),
        embed_dim=int(declared["embed_dim"]),
        target_fpr=float(declared["target_fpr"]),
        refs_per_occupant=int(declared["refs_per_occupant"]),
        sizing_occupants=int(declared["sizing_occupants"]),
        mean_face_sha256=declared["mean_face_sha256"],
        params_hash=declared["params_hash"],
        mean_face=mean_face,
    )


def _resync():
    """Print the hashes to paste back into this file / params.json."""
    actual = check_vendored(raise_on_mismatch=False)
    print("VENDORED_SHA256 = {")
    for name, h in actual.items():
        print(f'    "{name}": "{h}",')
    print("}")

    mean_path = SHARED_DIR / "mean_face.npy"
    if mean_path.exists():
        declared = json.loads((SHARED_DIR / "params.json").read_text(encoding="utf-8"))
        mean_sha = sha256_file(mean_path)
        print(f'\n  "mean_face_sha256": "{mean_sha}",')
        print('  "params_hash": "' + compute_params_hash(
            declared["seed"], declared["k"], declared["L"], declared["embed_dim"],
            declared["sizing_occupants"], mean_sha) + '"')


if __name__ == "__main__":
    if "--resync" in sys.argv:
        _resync()
        raise SystemExit(0)

    check_vendored()
    print(f"vendored modules OK: {', '.join(VENDORED_SHA256)}")

    p = load_params()
    print(f"contract OK: {p.summary()}")
    print(f"  space = 2**{p.k} = {p.space}, sizing_items = {p.sizing_items}")
    print(f"  mean_face {p.mean_face.shape} {p.mean_face.dtype}, "
          f"norm={np.linalg.norm(p.mean_face):.4f} (not unit -- expected)")

    # a tampered mean face must be rejected
    import tempfile, shutil
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        shutil.copy(SHARED_DIR / "params.json", td / "params.json")
        np.save(td / "mean_face.npy", p.mean_face * 1.001)
        try:
            load_params(td)
        except ContractMismatch:
            print("tampered mean face rejected")
        else:
            raise AssertionError("tampered mean face was NOT rejected")
    print("OK")
