"""Preprocessing and the random-hyperplane LSH primitives (Charikar, 2002).

The whole routing signal rests on one identity: for two unit vectors at angle
theta, a random hyperplane through the origin puts them on the same side with
probability ``1 - theta / pi``. So similar faces collide in many of the L codes,
unrelated faces in only a chance handful.
"""
import numpy as np


def l2_normalize(mat):
    """Row-wise unit-normalize. A zero row is left as zero (guarded, not NaN)."""
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


def mean_face(embeddings):
    """Population mean embedding, shape ``(1, dim)`` -- the centering vector."""
    return embeddings.mean(axis=0, keepdims=True)


def preprocess(embeddings, mean=None):
    """Mean-centre (if ``mean`` given) then L2-normalize.

    Order matters. ArcFace embeddings sit in a narrow cone, so hyperplanes
    through the origin barely cut the population -- most bits come out constant
    and useless. Subtracting the mean re-centres the cloud on the origin;
    normalizing afterwards puts it back on the unit sphere so dot product =
    cosine. ``mean=None`` skips centering (ablation only).
    """
    if mean is not None:
        embeddings = embeddings - mean
    return l2_normalize(embeddings)


def random_hyperplanes(dim, k, L, seed):
    """``(k * L, dim)`` i.i.d. standard-normal hyperplane normals.

    A Gaussian vector points in a uniformly random direction. Drawn only from
    ``seed`` -- no dependence on any embedding, so enrollment and query encoding
    use the identical planes and nothing about the enrolled population leaks in.
    """
    return np.random.default_rng(seed).standard_normal((k * L, dim)).astype(np.float32)


def encode(embeddings, hyperplanes, k):
    """``(N, dim)`` preprocessed embeddings -> ``(N, L)`` integer codes in ``[0, 2**k)``.

    Sign of the projection onto each hyperplane is one bit; every ``k`` bits are
    packed little-endian into one integer; ``L = len(hyperplanes) // k`` such
    integers per face.
    """
    N = embeddings.shape[0]
    L = hyperplanes.shape[0] // k
    bits = (embeddings @ hyperplanes.T >= 0).astype(np.uint32).reshape(N, L, k)
    weights = (np.uint32(1) << np.arange(k, dtype=np.uint32))
    codes = (bits * weights).sum(axis=2)
    return codes.astype(np.uint16 if k <= 16 else np.uint32)


def encode_with_margins(embeddings, hyperplanes, k):
    """Like :func:`encode`, but also return the per-bit projection magnitudes.

    ``margins[i, l, b] = |embedding_i . hyperplane|`` -- how far bit ``b`` of
    code ``l`` landed from the sign boundary. A small margin means the bit is a
    coin-flip: the same face photographed again could flip it. Multi-probe
    routing (step 07) flips the smallest-margin bits to recover the codes a
    boundary-straddling query would otherwise miss.
    """
    N = embeddings.shape[0]
    L = hyperplanes.shape[0] // k
    proj = (embeddings @ hyperplanes.T).reshape(N, L, k)
    bits = (proj >= 0).astype(np.uint32)
    weights = (np.uint32(1) << np.arange(k, dtype=np.uint32))
    codes = (bits * weights).sum(axis=2).astype(np.uint16 if k <= 16 else np.uint32)
    return codes, np.abs(proj).astype(np.float32)


if __name__ == "__main__":
    rng = np.random.default_rng(0)
    dim, k, L = 512, 11, 121
    H = random_hyperplanes(dim, k, L, seed=42)

    a = rng.standard_normal(dim)
    near = a + 0.15 * rng.standard_normal(dim)   # same "person"
    far = rng.standard_normal(dim)               # unrelated
    codes = encode(preprocess(np.stack([a, near, far]), mean=np.zeros((1, dim))), H, k)

    shared_near = int((codes[0] == codes[1]).sum())
    shared_far = int((codes[0] == codes[2]).sum())
    print(f"codes {codes.shape} {codes.dtype}   shared: near-dup {shared_near}/{L}, unrelated {shared_far}/{L}")
    assert shared_near > shared_far
    print("OK")
