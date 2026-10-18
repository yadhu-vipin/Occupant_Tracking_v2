"""Sealed inter-building biometric transfer: capture request + match reply (security/payload.py)."""
import base64
import copy
import json
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import nodelib.deploy as deploy
import pipeline.phase4_state as phase4_state
from buildinglib.node import BuildingNode, Reply, Visitor, VisitorPool
from nodelib.security_handler import SecureBuildingNodeHandler
from nodelib.transfer_codec import decode_capture, decode_reply, encode_capture, encode_reply
from security.crypto import NodeIdentity
from security.payload import (
    KIND_CAPTURE_REQUEST, KIND_MATCH_REPLY, PayloadError, open_payload, seal_payload,
)
from security.replay_guard import ReplayGuard


@pytest.fixture(autouse=True)
def fresh_security_state():
    SecureBuildingNodeHandler.reset_shared_state()
    yield
    SecureBuildingNodeHandler.reset_shared_state()


def unit(rng, *shape):
    a = rng.normal(size=shape).astype(np.float32)
    return a / np.linalg.norm(a, axis=-1, keepdims=True)


def clustered(rng, n_occ):
    """(n_occ, 20, 512) unit refs: each occupant's 20 refs sit tightly around one centre (so votes pass)."""
    centres = unit(rng, n_occ, 1, 512)
    refs = centres + 0.01 * rng.normal(size=(n_occ, 20, 512)).astype(np.float32)
    return (refs / np.linalg.norm(refs, axis=-1, keepdims=True)).astype(np.float32), centres[:, 0]


def make_reply(rng):
    return Reply(matched=True, building_id="building_2", occupant_id="0000042", votes=19,
                 home_building="building_2", refs=unit(rng, 20, 512),
                 candidate_distances={"0000042": 7.5, "0000043": 61.25})


# ---------------------------------------------------------------- payload layer

def pair():
    return NodeIdentity.generate("building_1"), NodeIdentity.generate("building_2")


def seal(a, b, payload=b"secret-embedding-bytes", kind=KIND_CAPTURE_REQUEST, in_reply_to=""):
    return seal_payload(payload, kind=kind, sender=a, receiver_building=b.building_id,
                        receiver_x25519_public=b.x25519_public, in_reply_to=in_reply_to)


def open_(env, a, b, **kw):
    kw.setdefault("expected_kind", KIND_CAPTURE_REQUEST)
    return open_payload(env, receiver=b, sender_ed25519_public=a.ed25519_public,
                        expected_sender=a.building_id, **kw)


def test_payload_round_trip_and_ciphertext_hides_plaintext():
    a, b = pair()
    env = seal(a, b, payload=b"PLAINTEXT-MARKER-0123456789")
    assert b"PLAINTEXT-MARKER" not in json.dumps(env).encode()
    assert open_(env, a, b) == b"PLAINTEXT-MARKER-0123456789"


def test_each_message_uses_a_fresh_ephemeral_key():
    a, b = pair()
    e1, e2 = seal(a, b), seal(a, b)
    assert e1["eph_pub"] != e2["eph_pub"] and e1["ciphertext"] != e2["ciphertext"]


@pytest.mark.parametrize("field,value", [
    ("receiver", "building_9"), ("sender", "building_7"), ("in_reply_to", "other"),
    ("timestamp", 1.0), ("message_id", "forged"), ("kind", KIND_MATCH_REPLY),
])
def test_any_header_tampering_is_rejected(field, value):
    a, b = pair()
    env = copy.deepcopy(seal(a, b))
    env[field] = value
    with pytest.raises(PayloadError):
        open_(env, a, b)


def test_ciphertext_tampering_and_wrong_recipient_rejected():
    a, b = pair()
    env = seal(a, b)
    flipped = copy.deepcopy(env)
    raw = bytearray(base64.b64decode(flipped["ciphertext"]))
    raw[0] ^= 0xFF
    flipped["ciphertext"] = base64.b64encode(bytes(raw)).decode()
    with pytest.raises(PayloadError):
        open_(flipped, a, b)
    mallory = NodeIdentity.generate("building_2")          # same name, different keys
    with pytest.raises(PayloadError):
        open_(env, a, mallory)


def test_signature_from_another_building_is_rejected():
    a, b = pair()
    impostor = NodeIdentity.generate("building_1")
    env = seal(impostor, b)
    with pytest.raises(PayloadError):
        open_(env, a, b)                                   # verified against the real building_1 key


def test_replayed_envelope_is_rejected():
    a, b = pair()
    guard = ReplayGuard()
    env = seal(a, b)
    check = lambda n, t, m: guard.validate(nonce=n, timestamp=t, message_id=m)
    open_(env, a, b, replay_check=check)
    with pytest.raises(PayloadError, match="replay"):
        open_(env, a, b, replay_check=check)


def test_stale_envelope_is_rejected_via_authenticated_timestamp():
    a, b = pair()
    guard = ReplayGuard(window_seconds=1.0)
    env = seal(a, b)
    env_old = copy.deepcopy(env)
    env_old["timestamp"] = 1.0                             # forging the age breaks the signature first
    with pytest.raises(PayloadError, match="signature"):
        open_(env_old, a, b, replay_check=lambda n, t, m: guard.validate(nonce=n, timestamp=t, message_id=m))


def test_reply_is_bound_to_its_request():
    a, b = pair()
    reply_env = seal(b, a, kind=KIND_MATCH_REPLY, in_reply_to="req-A")
    assert open_(reply_env, b, a, expected_kind=KIND_MATCH_REPLY, expected_in_reply_to="req-A")
    with pytest.raises(PayloadError, match="request"):
        open_(reply_env, b, a, expected_kind=KIND_MATCH_REPLY, expected_in_reply_to="req-B")


# ---------------------------------------------------------------- codec

def test_codec_round_trip_is_lossless_and_strict():
    rng = np.random.default_rng(0)
    q = unit(rng, 512)
    assert np.array_equal(decode_capture(encode_capture(q)), q)
    reply = make_reply(rng)
    back = decode_reply(encode_reply(reply))
    assert np.array_equal(back.refs, reply.refs) and back.refs is not reply.refs
    assert (back.matched, back.occupant_id, back.votes, back.candidate_distances) == \
           (True, "0000042", 19, {"0000042": 7.5, "0000043": 61.25})
    assert decode_reply(encode_reply(Reply(matched=False, building_id="building_3"))).refs is None
    good = encode_capture(q)
    bad_dtype = good[:4] + bytes([9]) + good[5:]                       # unknown dtype code
    with pytest.raises(ValueError):
        decode_capture(bad_dtype)
    huge = b"DSC1" + struct.pack("<BB", 0, 2) + struct.pack("<II", 999, 999) + b"\x00" * 16
    with pytest.raises(ValueError):                                      # shape larger than the bytes present
        decode_capture(huge)
    with pytest.raises(ValueError):
        decode_capture(good + b"extra")                                  # trailing bytes
    with pytest.raises(ValueError):
        decode_capture(b"XXXX" + good[4:])                               # wrong magic
    with pytest.raises(ValueError):
        decode_capture(encode_reply(make_reply(rng)))                    # a reply is not a capture
    with pytest.raises(ValueError):
        encode_capture(np.zeros((2, 2, 2), dtype=np.float32))            # more than 2 dimensions
    with pytest.raises(ValueError):
        encode_capture(np.zeros(4, dtype=np.int32))                      # not a float array


def test_wire_overhead_is_small():
    rng = np.random.default_rng(12)
    reply = make_reply(rng)
    raw = reply.refs.nbytes
    assert len(encode_reply(reply)) < raw * 1.05                         # binary framing, not JSON+base64
    h1, h2 = handlers()
    sealed = h2.seal_match_reply("building_2", "building_1", reply, request_id="r")
    assert len(json.dumps(sealed)) < raw * 1.5                           # base64 envelope: about 4/3


# ---------------------------------------------------------------- node handlers

def handlers():
    h1, h2 = SecureBuildingNodeHandler(), SecureBuildingNodeHandler()
    h1.register_node("building_1")
    h2.register_node("building_2")
    return h1, h2


def test_full_sealed_round_trip_delivers_reply_without_sharing_memory():
    rng = np.random.default_rng(1)
    h1, h2 = handlers()
    capture, reply = unit(rng, 512), make_reply(rng)
    seen = {}

    def answer(cap, src):
        seen["capture"], seen["src"] = cap, src
        return reply

    got, reason = h1.exchange_with_candidate("building_1", "building_2", capture, h2, answer)
    assert got is not None, reason
    assert seen["src"] == "building_1" and np.array_equal(seen["capture"], capture)
    assert np.array_equal(got.refs, reply.refs) and not np.shares_memory(got.refs, reply.refs)


def test_wire_envelopes_never_contain_plaintext_vectors(monkeypatch):
    rng = np.random.default_rng(2)
    h1, h2 = handlers()
    capture, reply = unit(rng, 512), make_reply(rng)
    wire = []
    import nodelib.security_handler as sh
    real = sh.seal_payload
    monkeypatch.setattr(sh, "seal_payload", lambda *a, **k: (wire.append(real(*a, **k)) or wire[-1]))
    got, _ = h1.exchange_with_candidate("building_1", "building_2", capture, h2, lambda c, s: reply)
    assert got is not None and len(wire) == 2
    blob = json.dumps(wire)
    assert reply.refs.tobytes().hex() not in blob and "0000042" not in blob
    import base64
    assert base64.b64encode(reply.refs.tobytes()).decode()[:64] not in blob


def test_revoked_responder_certificate_fails_closed():
    rng = np.random.default_rng(3)
    h1, h2 = handlers()
    cert = h1.transport_manager.get_certificate("building_2")
    h1.transport_manager.ca.revoke(cert.serial_number)
    called = []
    got, reason = h1.exchange_with_candidate("building_1", "building_2", unit(rng, 512), h2,
                                             lambda c, s: called.append(1) or make_reply(rng))
    assert got is None and not called and "revoked" in reason


def test_replayed_request_is_not_answered_twice():
    rng = np.random.default_rng(4)
    h1, h2 = handlers()
    env = h1.seal_capture_request("building_1", "building_2", unit(rng, 512))
    assert h2.open_capture_request("building_2", env)[0] is True
    ok, _, _, _, reason = h2.open_capture_request("building_2", env)
    assert ok is False and "replay" in reason.lower()


def test_reply_for_another_request_is_refused():
    rng = np.random.default_rng(5)
    h1, h2 = handlers()
    sealed = h2.seal_match_reply("building_2", "building_1", make_reply(rng), request_id="req-1")
    ok, reply, reason = h1.open_match_reply("building_1", "building_2", sealed, request_id="req-2")
    assert not ok and reply is None


def test_reply_cannot_be_spoofed_by_a_different_building():
    rng = np.random.default_rng(6)
    h1, h2 = handlers()
    h3 = SecureBuildingNodeHandler()
    h3.register_node("building_3")
    forged = h3.seal_match_reply("building_3", "building_1", make_reply(rng), request_id="req-1")
    ok, reply, _ = h1.open_match_reply("building_1", "building_2", forged, request_id="req-1")
    assert not ok and reply is None


# ---------------------------------------------------------------- identify() end to end

def build_world(rng):
    (refs1, _), (refs2, centres2) = clustered(rng, 3), clustered(rng, 3)
    n1 = BuildingNode("building_1", np.array(["a", "b", "c"]), refs1)
    n2 = BuildingNode("building_2", np.array(["x", "y", "z"]), refs2)
    contract = SimpleNamespace(accept_angle_deg=40.0, min_votes=12, shortlist_k=2, n_probes=0,
                               hyperplanes=None, space=None,
                               params=SimpleNamespace(k=1, refs_per_occupant=20))
    return n1, n2, refs2, centres2, contract


@pytest.fixture
def stub_routing(monkeypatch):
    monkeypatch.setattr(deploy, "encode_with_margins", lambda q, h, k: (np.zeros((1, 1)), np.zeros((1, 1))))
    monkeypatch.setattr(deploy, "route", lambda *a, **k: (["building_2"], {"building_2": 1.0}))


def test_identify_routes_securely_and_pool_owns_a_private_copy(stub_routing):
    rng = np.random.default_rng(7)
    n1, n2, refs2, centres2, contract = build_world(rng)
    capture = centres2[1]
    ident = deploy.identify(capture, n1, {"building_2": object()}, {"building_2": n2}, contract)
    assert ident.source == "routed" and ident.occupant_id == "y" and ident.home_building == "building_2"
    pooled = list(n1.pool)[0]
    assert np.array_equal(pooled.refs, refs2[1]) and not np.shares_memory(pooled.refs, refs2)
    again = deploy.identify(capture, n1, {"building_2": object()}, {"building_2": n2}, contract)
    assert again.source == "pool"


def test_identify_fails_closed_when_secure_exchange_breaks(stub_routing):
    rng = np.random.default_rng(8)
    n1, n2, refs2, centres2, contract = build_world(rng)
    n1.security_handler, n2.security_handler                   # provision both nodes
    h = n1.security_handler
    h.transport_manager.ca.revoke(h.transport_manager.get_certificate("building_2").serial_number)
    ident = deploy.identify(centres2[1], n1, {"building_2": object()}, {"building_2": n2}, contract)
    assert ident.source == "abstain" and ident.replies == [] and len(n1.pool) == 0


def test_identify_has_no_plaintext_fallback_when_handler_missing(stub_routing, monkeypatch):
    rng = np.random.default_rng(9)
    n1, n2, refs2, centres2, contract = build_world(rng)
    n2._security_handler = None
    monkeypatch.setattr(BuildingNode, "security_handler", property(lambda self: None))
    ident = deploy.identify(centres2[1], n1, {"building_2": object()}, {"building_2": n2}, contract)
    assert ident.source == "abstain"


# ---------------------------------------------------------------- pool expiry

def test_visitor_pool_expires_unseen_visitors_and_wipes_their_copy():
    rng = np.random.default_rng(10)
    home = unit(rng, 20, 512)
    pool = VisitorPool(ttl_seconds=100)
    v = Visitor("x", "building_2", home, 1.0)
    pool.admit(v)
    assert not np.shares_memory(v.refs, home)
    assert pool.purge_expired(now=v.last_seen + 50) == 0 and len(pool) == 1
    assert pool.purge_expired(now=v.last_seen + 101) == 1 and len(pool) == 0
    assert not v.refs.any() and home.any()                    # pool copy wiped, home data untouched


def test_visitor_pool_hit_refreshes_last_seen():
    rng = np.random.default_rng(11)
    all_refs, centres = clustered(rng, 1)
    refs = all_refs[0]
    pool = VisitorPool(ttl_seconds=100)
    v = Visitor("x", "building_2", refs, 1.0)
    pool.admit(v)
    v.last_seen -= 90
    contract = SimpleNamespace(accept_angle_deg=40.0, min_votes=12)
    before = v.last_seen
    assert pool.match(centres[0], contract) is not None
    assert v.last_seen > before


def test_phase4_present_visitor_expires_after_ttl(tmp_path):
    refs = np.ones((20, 4), dtype=np.float32)
    ctx = SimpleNamespace(present_visitors={
        "old": {"home_building": "building_2", "refs": refs, "last_seen": 8 * 3600},
        "fresh": {"home_building": "building_2", "refs": np.ones((20, 4)), "last_seen": 11 * 3600},
        "legacy": {"home_building": "building_2", "refs": None},       # no last_seen: never expired
    })
    phase4_state._expire_present_visitors(ctx, 13 * 3600, tmp_path, "13:00:00")
    assert set(ctx.present_visitors) == {"fresh", "legacy"}
    assert not refs.any()
