"""
v6/net/framing.py — Binary framing protocol
===============================================
Replaces v5's recv(16384)-until-newline which:
  - splits on embedded newlines
  - has no size bound
  - silently truncates

Frame format:
  [4B BE total_len][1B msg_type][4B BE header_len][header_json][binary_blobs]
  MAX_FRAME = 8 MiB

Embeddings ride in the binary section as raw float16;
the header stays JSON so frames remain debuggable.
"""

import struct
import json
import numpy as np
from typing import Tuple, Optional, Dict, Any

MAX_FRAME = 8 * 1024 * 1024  # 8 MiB

# Message types
MSG_HEARTBEAT       = 0x01
MSG_IDENTIFY_SEEK   = 0x10
MSG_IDENTIFY_RESOLVE = 0x11
MSG_IDENTIFY_RESULT = 0x12
MSG_QUERY_REQ       = 0x20
MSG_QUERY_RES       = 0x21
MSG_GOSSIP_SUMMARY  = 0x30
MSG_STATE_SYNC      = 0x40


def recv_exact(sock, n: int) -> bytes:
    """Read exactly n bytes from socket, or raise on EOF."""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Connection closed before full frame received")
        buf += chunk
    return buf


def encode_frame(
    msg_type: int,
    header: Dict[str, Any],
    binary_blobs: Optional[bytes] = None,
) -> bytes:
    """
    Encode a message into the binary framing format.

    Args:
        msg_type: message type byte
        header: JSON-serialisable header dict
        binary_blobs: optional binary payload (e.g., float16 embeddings)

    Returns:
        Complete frame as bytes
    """
    header_json = json.dumps(header, separators=(',', ':')).encode('utf-8')
    blob = binary_blobs or b""
    header_len = len(header_json)
    total_len = 1 + 4 + header_len + len(blob)  # msg_type + header_len_field + header + blob

    if total_len > MAX_FRAME:
        raise ValueError(f"Frame too large: {total_len} > {MAX_FRAME}")

    frame = struct.pack('>I', total_len)       # 4B total length
    frame += struct.pack('B', msg_type)         # 1B message type
    frame += struct.pack('>I', header_len)      # 4B header length
    frame += header_json                        # header JSON
    frame += blob                               # binary blobs
    return frame


def decode_frame(sock) -> Tuple[int, Dict[str, Any], bytes]:
    """
    Read and decode one frame from a socket.

    Returns:
        (msg_type, header_dict, binary_blob)
    """
    # Read total length
    raw_len = recv_exact(sock, 4)
    total_len = struct.unpack('>I', raw_len)[0]

    if total_len > MAX_FRAME:
        raise ValueError(f"Frame too large: {total_len}")

    # Read the rest
    payload = recv_exact(sock, total_len)

    msg_type = payload[0]
    header_len = struct.unpack('>I', payload[1:5])[0]
    header_json = payload[5:5 + header_len]
    binary_blob = payload[5 + header_len:]

    header = json.loads(header_json.decode('utf-8'))
    return msg_type, header, binary_blob


def embed_to_blob(embedding: np.ndarray) -> bytes:
    """Convert a float16 embedding to binary blob."""
    return embedding.astype(np.float16).tobytes()


def blob_to_embed(blob: bytes, dim: int = 512) -> np.ndarray:
    """Convert a binary blob back to float16 embedding."""
    return np.frombuffer(blob, dtype=np.float16).reshape(-1, dim)
