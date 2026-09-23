"""
v6/tests/test_transport_security.py — Transport security tests
=================================================================
Tests the transport-layer security components:

  - Certificate Authority generation and validation
  - Node certificate issuance and lifecycle
  - mTLS session establishment and mutual authentication
  - Certificate pinning and violation detection
  - Certificate revocation and rotation
  - Rogue node rejection
"""

import sys
import os
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from security.transport import (
    TransportSecurityManager, CertificateAuthority,
    CertificateInfo, CertificatePinningRegistry, MTLSSession,
)
from security.crypto import NodeIdentity


def test_ca_generation():
    """CA generates with valid self-signed certificate."""
    ca = CertificateAuthority.generate("TEST-CA")
    assert ca.ca_cert.subject == "TEST-CA"
    assert ca.ca_cert.issuer == "TEST-CA"
    assert ca.ca_cert.is_ca is True
    assert ca.ca_cert.is_valid_time is True


def test_node_certificate_issuance():
    """CA issues valid certificates for building nodes."""
    ca = CertificateAuthority.generate("TEST-CA")
    pub_key = os.urandom(32).hex()
    cert = ca.issue_node_certificate("B1", pub_key)
    assert cert.subject == "B1"
    assert cert.issuer == "TEST-CA"
    assert cert.is_ca is False
    assert cert.is_valid_time is True

    valid, reason = ca.validate_certificate(cert)
    assert valid, reason


def test_certificate_expiry():
    """Expired certificates are rejected."""
    ca = CertificateAuthority.generate("TEST-CA")
    # Issue with 0 days validity (already expired)
    cert = CertificateInfo(
        subject="EXPIRED",
        issuer="TEST-CA",
        serial_number="test_serial",
        fingerprint="test_fp",
        public_key_hex=os.urandom(32).hex(),
        not_before=time.time() - 200,
        not_after=time.time() - 100,
    )
    ca._issued_serials.add("test_serial")
    valid, reason = ca.validate_certificate(cert)
    assert not valid
    assert reason == "expired"


def test_certificate_revocation():
    """Revoked certificates are rejected."""
    ca = CertificateAuthority.generate("TEST-CA")
    cert = ca.issue_node_certificate("B2", os.urandom(32).hex())
    
    valid_before, _ = ca.validate_certificate(cert)
    assert valid_before
    
    ca.revoke(cert.serial_number)
    assert ca.is_revoked(cert.serial_number)
    
    valid_after, reason = ca.validate_certificate(cert)
    assert not valid_after
    assert "revoked" in reason


def test_unknown_ca_rejected():
    """Certificate from unknown CA is rejected."""
    ca = CertificateAuthority.generate("REAL-CA")
    rogue_ca = CertificateAuthority.generate("ROGUE-CA")
    rogue_cert = rogue_ca.issue_node_certificate("B1", os.urandom(32).hex())
    
    valid, reason = ca.validate_certificate(rogue_cert)
    assert not valid
    assert "issuer_mismatch" in reason


def test_mtls_session_establishment():
    """mTLS sessions authenticate both building nodes."""
    tsm = TransportSecurityManager()
    for i in range(5):
        bid = f"B{i}"
        tsm.provision_node(bid, os.urandom(32).hex())
    tsm.setup_pinning()
    
    session = tsm.establish_session("B0", "B1")
    assert session.is_authenticated
    assert session.client_building == "B0"
    assert session.server_building == "B1"


def test_certificate_pinning_detects_impersonation():
    """Certificate pinning detects impersonation attempts."""
    tsm = TransportSecurityManager()
    for i in range(3):
        tsm.provision_node(f"B{i}", os.urandom(32).hex())
    tsm.setup_pinning()
    
    # Create a rogue certificate pretending to be B1
    rogue_ca = CertificateAuthority.generate("ROGUE")
    rogue_cert = rogue_ca.issue_node_certificate("B1", os.urandom(32).hex())
    
    # B0's pinning registry should reject the rogue cert
    pin_reg = tsm._pin_registries["B0"]
    valid, reason = pin_reg.verify_pin("B1", rogue_cert)
    assert not valid
    assert "pin_mismatch" in reason
    assert pin_reg.violation_count == 1


def test_certificate_rotation():
    """Certificate rotation revokes old cert and issues new one."""
    tsm = TransportSecurityManager()
    old_cert = tsm.provision_node("B3", os.urandom(32).hex())
    old_serial = old_cert.serial_number
    
    new_cert = tsm.rotate_certificate("B3", os.urandom(32).hex())
    assert new_cert.serial_number != old_serial
    assert tsm.ca.is_revoked(old_serial)
    
    valid, reason = tsm.ca.validate_certificate(new_cert)
    assert valid


def test_all_buildings_provisioned():
    """All 10 campus buildings can be provisioned and cross-authenticated."""
    tsm = TransportSecurityManager()
    buildings = [f"B{i}" for i in range(10)]
    
    for bid in buildings:
        identity = NodeIdentity.generate(bid)
        tsm.provision_node(bid, identity.ed25519_public_bytes().hex())
    
    tsm.setup_pinning()
    
    # Test all buildings can establish sessions with B0
    for bid in buildings[1:]:
        session = tsm.establish_session("B0", bid)
        assert session.is_authenticated, f"B0<->{bid} failed"


def test_security_summary():
    """Security summary reports correct statistics."""
    tsm = TransportSecurityManager()
    for i in range(5):
        tsm.provision_node(f"B{i}", os.urandom(32).hex())
    tsm.setup_pinning()
    
    summary = tsm.get_security_summary()
    assert summary["total_nodes"] == 5
    assert summary["revoked_certificates"] == 0
    assert summary["pin_violations"] == 0
    assert len(summary["certificates"]) == 5


def test_transport_security_with_crypto_keys():
    """Transport security integrates with application-level crypto keys."""
    tsm = TransportSecurityManager()
    
    b1_id = NodeIdentity.generate("B1")
    b5_id = NodeIdentity.generate("B5")
    
    tsm.provision_node("B1", b1_id.ed25519_public_bytes().hex())
    tsm.provision_node("B5", b5_id.ed25519_public_bytes().hex())
    tsm.setup_pinning()
    
    session = tsm.establish_session("B1", "B5")
    assert session.is_authenticated
    
    # Verify fingerprints match Ed25519 public keys
    import hashlib
    expected_b1_fp = hashlib.sha256(b1_id.ed25519_public_bytes()).hexdigest()
    expected_b5_fp = hashlib.sha256(b5_id.ed25519_public_bytes()).hexdigest()
    
    assert tsm._node_certs["B1"].fingerprint == expected_b1_fp
    assert tsm._node_certs["B5"].fingerprint == expected_b5_fp
