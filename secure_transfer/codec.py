"""Wire encoding for the two payloads that cross a building boundary.

Compact binary framing (no JSON around the vectors, no base64 inside the ciphertext):

    capture : b"DSC1" | array
    reply   : b"DSR1" | u32 meta_len | meta (JSON, small) | u8 has_refs | [array]
    array   : u8 dtype (0=float32, 1=float64) | u8 ndim | u32 dim * ndim | raw little-endian bytes

Decoding is strict: only float32/float64, at most 2 dimensions, bounded element count, and the byte length must
match the shape exactly. Anything else raises ``ValueError``.
"""
from __future__ import annotations

import json
import struct

import numpy as np

from buildinglib.node import Reply

_CAPTURE_MAGIC = b"DSC1"
_REPLY_MAGIC = b"DSR1"
_DTYPES = {0: np.dtype("<f4"), 1: np.dtype("<f8")}
_DTYPE_CODE = {"float32": 0, "float64": 1}
_MAX_ELEMENTS = 1_000_000            # 20x512 refs is ~10k; this is a generous ceiling
_MAX_META_BYTES = 1_000_000


def _enc_array(arr) -> bytes:
    a = np.ascontiguousarray(arr)
    code = _DTYPE_CODE.get(a.dtype.name)
    if code is None:
        raise ValueError(f"unsupported array dtype {a.dtype.name}")
    if a.ndim > 2:
        raise ValueError("arrays with more than 2 dimensions are not allowed")
    head = struct.pack("<BB", code, a.ndim) + b"".join(struct.pack("<I", n) for n in a.shape)
    return head + a.astype(_DTYPES[code], copy=False).tobytes()


def _dec_array(buf: bytes, offset: int = 0) -> tuple[np.ndarray, int]:
    if len(buf) < offset + 2:
        raise ValueError("truncated array header")
    code, ndim = struct.unpack_from("<BB", buf, offset)
    if code not in _DTYPES:
        raise ValueError(f"unsupported array dtype code {code}")
    if ndim > 2:
        raise ValueError("arrays with more than 2 dimensions are not allowed")
    offset += 2
    if len(buf) < offset + 4 * ndim:
        raise ValueError("truncated array shape")
    shape = struct.unpack_from("<" + "I" * ndim, buf, offset)
    offset += 4 * ndim
    count = int(np.prod(shape, dtype=np.int64)) if shape else 1
    if count > _MAX_ELEMENTS:
        raise ValueError("array shape out of bounds")
    dtype = _DTYPES[code]
    nbytes = count * dtype.itemsize
    if len(buf) < offset + nbytes:
        raise ValueError("array byte length does not match shape")
    arr = np.frombuffer(buf, dtype=dtype, count=count, offset=offset).reshape(shape).copy()
    return arr, offset + nbytes


def encode_capture(capture_q) -> bytes:
    return _CAPTURE_MAGIC + _enc_array(capture_q)


def decode_capture(data: bytes) -> np.ndarray:
    if data[:4] != _CAPTURE_MAGIC:
        raise ValueError("not a capture payload")
    arr, end = _dec_array(data, 4)
    if end != len(data):
        raise ValueError("trailing bytes after capture")
    return arr


def encode_reply(reply: Reply) -> bytes:
    meta = json.dumps({
        "matched": bool(reply.matched),
        "building_id": reply.building_id,
        "occupant_id": reply.occupant_id,
        "votes": int(reply.votes),
        "home_building": reply.home_building,
        "candidate_distances": (None if reply.candidate_distances is None else
                                {str(k): float(v) for k, v in reply.candidate_distances.items()}),
    }, separators=(",", ":")).encode("utf-8")
    body = struct.pack("<I", len(meta)) + meta
    if reply.refs is None:
        return _REPLY_MAGIC + body + b"\x00"
    return _REPLY_MAGIC + body + b"\x01" + _enc_array(reply.refs)


def decode_reply(data: bytes) -> Reply:
    if data[:4] != _REPLY_MAGIC:
        raise ValueError("not a reply payload")
    if len(data) < 9:
        raise ValueError("truncated reply")
    (meta_len,) = struct.unpack_from("<I", data, 4)
    if meta_len > _MAX_META_BYTES or len(data) < 8 + meta_len + 1:
        raise ValueError("reply metadata out of bounds")
    meta = json.loads(data[8:8 + meta_len].decode("utf-8"))
    pos = 8 + meta_len
    flag = data[pos]
    refs = None
    if flag == 1:
        refs, pos = _dec_array(data, pos + 1)
    elif flag == 0:
        pos += 1
    else:
        raise ValueError("bad refs flag")
    if pos != len(data):
        raise ValueError("trailing bytes after reply")
    return Reply(
        matched=bool(meta["matched"]),
        building_id=meta["building_id"],
        occupant_id=meta["occupant_id"],
        votes=int(meta["votes"]),
        home_building=meta["home_building"],
        refs=refs,
        candidate_distances=meta["candidate_distances"],
    )
