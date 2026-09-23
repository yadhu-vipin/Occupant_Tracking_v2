"""
v6/identify/normalize.py — Embedding preparation
===================================================
Pipeline: x → (x − μ) → x/‖x‖ → float16

Mean-centering is NOT optional: face embeddings occupy a narrow cone,
so uncentered SimHash bits are heavily correlated and buckets collapse.

Where μ comes from matters: computed on a HELD-OUT image set that is
never enrolled, shipped in the provisioning bundle. Computing μ over
the union of buildings' galleries would silently re-centralise the data.
"""

import numpy as np
from typing import Optional


def compute_mean(embeddings: np.ndarray) -> np.ndarray:
    """
    Compute the mean embedding from a held-out set.
    This mean must be computed on data that is NEVER enrolled.

    Args:
        embeddings: (N, D) array of held-out embeddings

    Returns:
        (D,) mean vector
    """
    return embeddings.mean(axis=0).astype(np.float64)


def normalize_embedding(
    x: np.ndarray,
    mu: np.ndarray,
    output_dtype: np.dtype = np.float16,
) -> np.ndarray:
    """
    Prepare an embedding for SimHash and gallery comparison.

    Pipeline: x → (x − μ) → x/‖x‖ → float16

    Args:
        x: raw embedding vector (D,) or batch (N, D)
        mu: held-out mean vector (D,)
        output_dtype: output precision (default float16)

    Returns:
        normalised embedding(s) in output_dtype
    """
    centered = x.astype(np.float64) - mu.astype(np.float64)

    if centered.ndim == 1:
        norm = np.linalg.norm(centered)
        if norm > 0:
            centered /= norm
    else:
        norms = np.linalg.norm(centered, axis=1, keepdims=True)
        norms = np.maximum(norms, 1e-10)
        centered /= norms

    return centered.astype(output_dtype)


def normalize_gallery(
    gallery: dict,
    mu: np.ndarray,
) -> dict:
    """
    Normalize an entire gallery of embeddings.

    Args:
        gallery: {occupant_id: np.ndarray of shape (K, D)}
        mu: held-out mean

    Returns:
        {occupant_id: normalised np.ndarray of shape (K, D) in float16}
    """
    return {
        oid: normalize_embedding(embs, mu)
        for oid, embs in gallery.items()
    }
