"""Serialize a building's Bloom filter to a small publishable file.

The payload is about 7 KB -- four orders of magnitude smaller than the embedding
corpus it came from, and one-way: the bit array cannot be turned back into
anyone's face. It carries the packed bits and integer metadata, nothing else.
No occupant ids, no image paths, no centroids, no codes.

Bits are stored ``np.packbits``-ed (8 bits per byte instead of NumPy's 1 byte
per bool), with ``bitorder="big"`` stated explicitly rather than relied on.
``packbits`` pads up to a byte boundary, so ``m`` travels alongside and the
loader truncates back to it with ``count=m`` -- which raises on a short file
instead of silently returning fewer bits.

**Artifact identity is ``bits_sha256``, not the file bytes.** ``np.savez`` writes
a ZIP, and ZIP entries carry a wall-clock timestamp, so two runs a second apart
produce different files from identical data. Two people agree when their
``bits_sha256`` and ``params_hash`` agree.

A sidecar ``<building>.manifest.json`` mirrors the scalars in readable form, so
a re-pushed filter shows up in a git diff as *what* changed rather than just
"binary file differs".
"""
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from ._vendored.bloom import BloomFilter
from .enroll import BuildingFilter
from .params import SCHEMA_VERSION, ContractMismatch

MANIFEST_SUFFIX = ".manifest.json"
BITORDER = "big"


def bits_sha256(packed):
    """Canonical content identity of a filter: SHA-256 of its packed bytes."""
    return hashlib.sha256(np.ascontiguousarray(packed, dtype=np.uint8).tobytes()).hexdigest()


def theoretical_fpr(m, n_items):
    """The false-positive rate this geometry implies, for the manifest."""
    return math.exp(-m * math.log(2) ** 2 / max(1, n_items))


def save_building(path, bf, params):
    """Write ``bf`` to ``path`` (.npz) plus its sidecar manifest.

    Re-reads what it wrote and asserts the bits survived, so a corrupt artifact
    never reaches the repo. Returns ``(npz_path, manifest_path, bits_sha256)``.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    packed = np.packbits(bf.bloom.bits, bitorder=BITORDER)
    if packed.size != (bf.bloom.m + 7) // 8:
        raise ValueError(f"packed {packed.size} bytes for m={bf.bloom.m}")
    if int(np.unpackbits(packed, bitorder=BITORDER)[bf.bloom.m:].sum()) != 0:
        raise ValueError("packbits padding is not zero -- refusing to write")

    digest = bits_sha256(packed)

    np.savez_compressed(
        path,
        packed_bits=packed,
        m=np.int64(bf.bloom.m),
        n_hashes=np.int64(bf.bloom.n_hashes),
        seed=np.int64(bf.bloom.seed),
        n_items=np.int64(bf.n_items),
        n_occupants=np.int64(bf.n_occupants),
        target_fpr=np.float64(params.target_fpr),
        k=np.int64(bf.k),
        L=np.int64(bf.L),
        embed_dim=np.int64(bf.embed_dim),
        sizing_occupants=np.int64(params.sizing_occupants),
        schema_version=np.int64(SCHEMA_VERSION),
        building_id=np.str_(bf.building_id),
        params_hash=np.str_(bf.params_hash),
        bits_sha256=np.str_(digest),
    )

    # prove the file we just wrote reloads to the same filter
    back = load_building(path)
    if not np.array_equal(back.bloom.bits, bf.bloom.bits):
        raise ValueError(f"{path.name} did not survive a round-trip -- not writing manifest")

    manifest = {
        "bits_sha256": digest,
        "building_id": bf.building_id,
        "contract_version": SCHEMA_VERSION,
        "embed_dim": bf.embed_dim,
        "fill_ratio": round(bf.fill_ratio, 6),
        "k": bf.k,
        "L": bf.L,
        "m": int(bf.bloom.m),
        "n_hashes": int(bf.bloom.n_hashes),
        "n_items": int(bf.n_items),
        "n_occupants": int(bf.n_occupants),
        "packed_bytes": int(packed.nbytes),
        "params_hash": bf.params_hash,
        "seed": int(bf.bloom.seed),
        "sizing_occupants": int(params.sizing_occupants),
        "theoretical_fpr": round(theoretical_fpr(bf.bloom.m, bf.n_items), 8),
    }
    manifest_path = manifest_path_for(path)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n",
    )

    return path, manifest_path, digest


def manifest_path_for(npz_path):
    """The sidecar path for an artifact."""
    p = Path(npz_path)
    return p.with_name(p.stem + MANIFEST_SUFFIX)


def load_building(path):
    """Rebuild a :class:`BuildingFilter` from an .npz, with a working BloomFilter."""
    path = Path(path)
    with np.load(path, allow_pickle=False) as d:
        version = int(d["schema_version"])
        if version != SCHEMA_VERSION:
            raise ContractMismatch(
                f"{path.name} is schema v{version}, this package speaks v{SCHEMA_VERSION}"
            )

        m, packed = int(d["m"]), d["packed_bits"]
        try:
            bits = np.unpackbits(packed, count=m, bitorder=BITORDER).astype(bool)
        except ValueError as exc:
            raise ContractMismatch(
                f"{path.name}: cannot unpack {m} bits from {packed.size} bytes -- "
                f"file is truncated ({exc})"
            ) from exc

        stored = str(d["bits_sha256"])
        actual = bits_sha256(packed)
        if actual != stored:
            raise ContractMismatch(
                f"{path.name} is corrupt:\n    stored bits_sha256 {stored}\n"
                f"    actual             {actual}"
            )

        # rebuild with the stored geometry rather than re-deriving it
        bloom = BloomFilter(int(d["n_items"]) or 1, float(d["target_fpr"]), seed=int(d["seed"]))
        bloom.m, bloom.n_hashes, bloom.bits = m, int(d["n_hashes"]), bits

        return BuildingFilter(
            building_id=str(d["building_id"]),
            bloom=bloom,
            occupant_ids=np.arange(int(d["n_occupants"])),  # real ids are never published
            n_items=int(d["n_items"]),
            params_hash=str(d["params_hash"]),
            k=int(d["k"]),
            L=int(d["L"]),
            embed_dim=int(d["embed_dim"]),
        )


def read_manifest(npz_path):
    """The sidecar manifest for an artifact, or ``None`` if absent."""
    p = manifest_path_for(npz_path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


if __name__ == "__main__":
    import sys
    import tempfile
    from pathlib import Path as _P

    sys.path.insert(0, str(_P(__file__).resolve().parent.parent.parent))
    import pandas as pd
    from core import config as cfg                            # noqa: E402  (dev-only)

    from .enroll import enroll_building
    from .params import load_params
    from .split import split_building

    p = load_params()
    raw = np.load(cfg.EMB_FILE)
    meta = pd.read_csv(cfg.META_FILE)
    emb_b, meta_b = split_building(raw, meta, "building_3")
    bf = enroll_building(emb_b, meta_b, "building_3", p)

    with tempfile.TemporaryDirectory() as td:
        npz, man, digest = save_building(_P(td) / "building_3.npz", bf, p)
        print(f"wrote {npz.name} ({npz.stat().st_size} bytes) + {man.name}")
        print(f"bits_sha256 {digest[:16]}...")

        back = load_building(npz)
        assert np.array_equal(back.bloom.bits, bf.bloom.bits), "bits changed on round-trip"
        assert (back.bloom.m, back.bloom.n_hashes) == (bf.bloom.m, bf.bloom.n_hashes)
        assert back.building_id == bf.building_id and back.params_hash == bf.params_hash
        print(f"round-trip bit-identical: m={back.bloom.m}, "
              f"hashes={back.bloom.n_hashes}, fill={back.fill_ratio:.4f}")

        probe = np.arange(0, 200000, 977, dtype=np.int64)
        assert bf.bloom.hits(probe) == back.bloom.hits(probe), "reloaded filter scores differently"
        print(f"reloaded filter scores identically on {len(probe)} probe items")

        # writing twice gives different FILE bytes (zip timestamps) but the same content id
        npz2, _, digest2 = save_building(_P(td) / "again.npz", bf, p)
        assert digest2 == digest, "content hash must be stable across writes"
        print(f"content hash stable across writes (file bytes are not: "
              f"{npz.stat().st_size} vs {npz2.stat().st_size} bytes, zip timestamps differ)")

        # corruption must be caught
        with np.load(npz, allow_pickle=False) as d:
            fields = {k: d[k] for k in d.files}
        fields["packed_bits"] = fields["packed_bits"] ^ np.uint8(1)
        np.savez(_P(td) / "corrupt.npz", **fields)
        try:
            load_building(_P(td) / "corrupt.npz")
        except ContractMismatch:
            print("flipped bit detected via bits_sha256")
        else:
            raise AssertionError("corruption was NOT detected")
    print("OK")
