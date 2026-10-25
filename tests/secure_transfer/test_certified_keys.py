"""The CA certificate binds the X25519 key-agreement key as well as the Ed25519 signing key, and payloads are only ever
sealed to the key in the certificate."""
import hashlib
from dataclasses import replace

import numpy as np
import pytest

from buildinglib.node import Reply
from secure_transfer.crypto import NodeIdentity
from secure_transfer.handler import SecureBuildingNodeHandler

CAPTURE = np.random.default_rng(0).standard_normal(512).astype(np.float32)


@pytest.fixture(autouse=True)
def fresh_network():
    SecureBuildingNodeHandler.reset_shared_state()
    yield
    SecureBuildingNodeHandler.reset_shared_state()


@pytest.fixture()
def handler():
    h = SecureBuildingNodeHandler()
    for b in ("building_1", "building_2"):
        h.register_node(b)
    return h


def answer(capture, source):
    return Reply(matched=False, building_id="building_2")


def exchange(h, candidate="building_2"):
    return h.exchange_with_candidate("building_1", candidate, CAPTURE, h, answer)


def test_certificate_carries_and_fingerprints_both_public_keys(handler):
    cert = handler.transport_manager.get_certificate("building_2")
    identity = handler.identities["building_2"]
    assert cert.x25519_public_hex == identity.x25519_public_bytes().hex()
    assert cert.fingerprint == hashlib.sha256(identity.ed25519_public_bytes() + identity.x25519_public_bytes()).hexdigest()
    assert handler.transport_manager.ca.validate_certificate(cert) == (True, "valid")


def test_a_normal_sealed_round_trip_still_works(handler):
    reply, reason = exchange(handler)
    assert reply is not None and reason == "Success" and reply.matched is False


def test_swapping_the_key_agreement_key_breaks_the_ca_signature(handler):
    ca = handler.transport_manager.ca
    cert = handler.transport_manager.get_certificate("building_2")
    attacker = NodeIdentity.generate("attacker").x25519_public_bytes().hex()
    forged = replace(cert, x25519_public_hex=attacker)
    ok, why = ca.validate_certificate(forged)
    assert not ok and why == "bad_signature"
    handler.transport_manager._node_certs["building_2"] = forged          # an attacker plants the forged certificate
    reply, reason = exchange(handler)
    assert reply is None and "bad_signature" in reason                    # fails closed, nothing is sealed to the attacker


def test_extending_the_validity_of_a_certificate_is_caught_too(handler):
    ca = handler.transport_manager.ca
    cert = handler.transport_manager.get_certificate("building_2")
    assert ca.validate_certificate(replace(cert, not_after=cert.not_after + 10 ** 6)) == (False, "bad_signature")


def test_certificate_without_a_key_agreement_key_cannot_receive_payloads(handler):
    identity = NodeIdentity.generate("building_3")
    handler.identities["building_3"] = identity
    handler.transport_manager.provision_node("building_3", identity.ed25519_public_bytes().hex())   # no X25519 key
    reply, reason = exchange(handler, candidate="building_3")
    assert reply is None and "no key-agreement key" in reason


def test_payload_is_sealed_to_the_certified_key_not_the_identity_object(handler):
    # The CA certifies a key the real node does not hold: the node cannot decrypt, the exchange fails closed.
    other = NodeIdentity.generate("someone-else").x25519_public_bytes().hex()
    handler.transport_manager.rotate_certificate(
        "building_2", handler.identities["building_2"].ed25519_public_bytes().hex(), x25519_public_hex=other)
    reply, reason = exchange(handler)
    assert reply is None and "decryption failed" in reason
