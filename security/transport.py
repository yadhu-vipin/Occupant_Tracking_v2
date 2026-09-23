"""
v6/security/transport.py — Transport Security Certificates & mTLS Simulation
===============================================================================
Implements transport-layer security for inter-building node communication:

  - Self-signed X.509 certificate generation per building node
  - Certificate chain validation (CA -> node certificate)
  - mTLS (mutual TLS) simulation for bidirectional authentication
  - Certificate pinning registry for known building peers
  - Certificate expiry validation and rotation support

This complements the application-layer security in crypto.py (X25519 + AES-GCM)
with transport-layer authentication, following defense-in-depth.

Threat model coverage:
  - Rogue node injection  -> certificate validation rejects unknown CAs
  - Expired certificates  -> expiry check prevents stale credentials
  - MITM at transport     -> mTLS ensures both sides are authenticated
  - Certificate spoofing  -> pinning registry cross-validates fingerprints
"""

import os
import time
import hashlib
import json
from typing import Dict, Optional, Tuple, List, Set
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class CertificateInfo:
    """Simulated X.509 certificate for a building node."""
    subject: str              # Building ID (e.g., "B1")
    issuer: str               # CA name or self-signed
    serial_number: str        # Unique serial
    fingerprint: str          # SHA-256 fingerprint of public key
    public_key_hex: str       # Hex-encoded public key
    not_before: float         # Unix timestamp
    not_after: float          # Unix timestamp (expiry)
    is_ca: bool = False       # Whether this is a CA certificate
    key_usage: str = "digitalSignature,keyEncipherment"

    @property
    def is_expired(self) -> bool:
        return time.time() > self.not_after

    @property
    def is_not_yet_valid(self) -> bool:
        return time.time() < self.not_before

    @property
    def is_valid_time(self) -> bool:
        now = time.time()
        return self.not_before <= now <= self.not_after

    @property
    def days_until_expiry(self) -> float:
        return (self.not_after - time.time()) / 86400.0

    def to_dict(self) -> Dict:
        return {
            "subject": self.subject,
            "issuer": self.issuer,
            "serial": self.serial_number,
            "fingerprint": self.fingerprint[:32] + "...",
            "not_before": datetime.fromtimestamp(self.not_before, tz=timezone.utc).isoformat(),
            "not_after": datetime.fromtimestamp(self.not_after, tz=timezone.utc).isoformat(),
            "is_ca": self.is_ca,
            "valid": self.is_valid_time,
            "days_until_expiry": round(self.days_until_expiry, 1),
        }


@dataclass
class CertificateAuthority:
    """Simulated Certificate Authority for the DSTS campus."""
    ca_name: str
    ca_cert: CertificateInfo
    _issued_serials: Set[str] = field(default_factory=set)
    _revoked_serials: Set[str] = field(default_factory=set)

    @classmethod
    def generate(cls, ca_name: str = "DSTS-Campus-CA",
                 validity_days: int = 3650) -> 'CertificateAuthority':
        """Generate a new self-signed CA."""
        now = time.time()
        key_material = os.urandom(32)
        fingerprint = hashlib.sha256(key_material).hexdigest()
        serial = hashlib.sha256(os.urandom(16)).hexdigest()[:16]

        ca_cert = CertificateInfo(
            subject=ca_name,
            issuer=ca_name,  # self-signed
            serial_number=serial,
            fingerprint=fingerprint,
            public_key_hex=key_material.hex(),
            not_before=now,
            not_after=now + validity_days * 86400,
            is_ca=True,
            key_usage="keyCertSign,cRLSign",
        )
        ca = cls(ca_name=ca_name, ca_cert=ca_cert)
        ca._issued_serials.add(serial)
        return ca

    def issue_node_certificate(
        self,
        building_id: str,
        public_key_hex: str,
        validity_days: int = 365,
    ) -> CertificateInfo:
        """Issue a certificate for a building node, signed by this CA."""
        now = time.time()
        serial = hashlib.sha256(
            f"{building_id}:{now}:{os.urandom(8).hex()}".encode()
        ).hexdigest()[:16]

        fingerprint = hashlib.sha256(
            bytes.fromhex(public_key_hex)
        ).hexdigest()

        cert = CertificateInfo(
            subject=building_id,
            issuer=self.ca_name,
            serial_number=serial,
            fingerprint=fingerprint,
            public_key_hex=public_key_hex,
            not_before=now,
            not_after=now + validity_days * 86400,
            is_ca=False,
        )
        self._issued_serials.add(serial)
        return cert

    def revoke(self, serial: str) -> bool:
        """Revoke a certificate by serial number."""
        if serial in self._issued_serials:
            self._revoked_serials.add(serial)
            return True
        return False

    def is_revoked(self, serial: str) -> bool:
        return serial in self._revoked_serials

    def validate_certificate(self, cert: CertificateInfo) -> Tuple[bool, str]:
        """
        Validate a certificate against this CA.

        Checks:
          1. Issuer matches CA name
          2. Serial was issued by this CA
          3. Certificate not revoked
          4. Certificate time validity
        """
        if cert.issuer != self.ca_name:
            return False, f"issuer_mismatch: expected {self.ca_name}, got {cert.issuer}"

        if cert.serial_number not in self._issued_serials:
            return False, f"unknown_serial: {cert.serial_number}"

        if cert.serial_number in self._revoked_serials:
            return False, f"certificate_revoked: {cert.serial_number}"

        if cert.is_not_yet_valid:
            return False, "not_yet_valid"

        if cert.is_expired:
            return False, "expired"

        return True, "valid"


class CertificatePinningRegistry:
    """
    Certificate pinning for known building peers.

    Each building maintains a map of peer building IDs to their
    expected certificate fingerprints. This prevents MITM attacks
    even if the CA is compromised.
    """

    def __init__(self, building_id: str):
        self.building_id = building_id
        self._pins: Dict[str, str] = {}  # peer_id -> fingerprint
        self._violations: List[Dict] = []

    def pin(self, peer_id: str, fingerprint: str) -> None:
        """Pin a peer's certificate fingerprint."""
        self._pins[peer_id] = fingerprint

    def verify_pin(self, peer_id: str, cert: CertificateInfo) -> Tuple[bool, str]:
        """Verify a peer's certificate against the pinned fingerprint."""
        if peer_id not in self._pins:
            return True, "no_pin_registered"  # No pin = accept (first-use)

        expected = self._pins[peer_id]
        if cert.fingerprint != expected:
            self._violations.append({
                "timestamp": time.time(),
                "peer_id": peer_id,
                "expected_fingerprint": expected[:16] + "...",
                "received_fingerprint": cert.fingerprint[:16] + "...",
            })
            return False, f"pin_mismatch: expected {expected[:16]}..."

        return True, "pin_verified"

    @property
    def violation_count(self) -> int:
        return len(self._violations)


@dataclass
class MTLSSession:
    """
    Simulated mTLS session between two building nodes.

    Both sides present certificates; both sides validate the other.
    """
    client_building: str
    server_building: str
    client_cert: CertificateInfo
    server_cert: CertificateInfo
    session_id: str = ""
    established_at: float = 0.0
    is_authenticated: bool = False
    validation_log: List[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.session_id:
            self.session_id = hashlib.sha256(
                f"{self.client_building}:{self.server_building}:"
                f"{time.time()}:{os.urandom(8).hex()}".encode()
            ).hexdigest()[:16]
        self.established_at = time.time()

    @classmethod
    def establish(
        cls,
        client_id: str,
        server_id: str,
        client_cert: CertificateInfo,
        server_cert: CertificateInfo,
        ca: CertificateAuthority,
        client_pins: Optional[CertificatePinningRegistry] = None,
        server_pins: Optional[CertificatePinningRegistry] = None,
    ) -> 'MTLSSession':
        """
        Establish an mTLS session with full validation.

        Validates:
          1. Both certificates against CA
          2. Certificate pinning (if registries provided)
          3. Subject matches expected building ID
        """
        session = cls(
            client_building=client_id,
            server_building=server_id,
            client_cert=client_cert,
            server_cert=server_cert,
        )

        # 1. Validate server cert (client-side)
        valid, reason = ca.validate_certificate(server_cert)
        session.validation_log.append(
            f"Client validates server cert: {'PASS' if valid else 'FAIL'} ({reason})"
        )
        if not valid:
            return session

        # 2. Validate client cert (server-side)
        valid, reason = ca.validate_certificate(client_cert)
        session.validation_log.append(
            f"Server validates client cert: {'PASS' if valid else 'FAIL'} ({reason})"
        )
        if not valid:
            return session

        # 3. Subject verification
        if server_cert.subject != server_id:
            session.validation_log.append(
                f"Server subject mismatch: expected {server_id}, got {server_cert.subject}"
            )
            return session

        if client_cert.subject != client_id:
            session.validation_log.append(
                f"Client subject mismatch: expected {client_id}, got {client_cert.subject}"
            )
            return session

        # 4. Certificate pinning (optional)
        if client_pins:
            pin_ok, pin_reason = client_pins.verify_pin(server_id, server_cert)
            session.validation_log.append(
                f"Client pin check: {'PASS' if pin_ok else 'FAIL'} ({pin_reason})"
            )
            if not pin_ok:
                return session

        if server_pins:
            pin_ok, pin_reason = server_pins.verify_pin(client_id, client_cert)
            session.validation_log.append(
                f"Server pin check: {'PASS' if pin_ok else 'FAIL'} ({pin_reason})"
            )
            if not pin_ok:
                return session

        session.is_authenticated = True
        session.validation_log.append("mTLS session established successfully")
        return session


class TransportSecurityManager:
    """
    Manages transport security for all building nodes in the campus.

    Orchestrates:
      - CA generation
      - Per-node certificate issuance
      - Certificate pinning setup
      - mTLS session establishment
      - Certificate lifecycle (expiry, rotation, revocation)
    """

    def __init__(self, ca_name: str = "DSTS-Campus-CA"):
        self.ca = CertificateAuthority.generate(ca_name)
        self._node_certs: Dict[str, CertificateInfo] = {}
        self._pin_registries: Dict[str, CertificatePinningRegistry] = {}
        self._sessions: List[MTLSSession] = []
        self._audit_log: List[Dict] = []

    def provision_node(
        self,
        building_id: str,
        public_key_hex: str,
        validity_days: int = 365,
    ) -> CertificateInfo:
        """Issue a certificate and set up pinning for a building node."""
        cert = self.ca.issue_node_certificate(
            building_id, public_key_hex, validity_days
        )
        self._node_certs[building_id] = cert
        self._pin_registries[building_id] = CertificatePinningRegistry(building_id)

        self._audit_log.append({
            "event": "NODE_PROVISIONED",
            "building_id": building_id,
            "serial": cert.serial_number,
            "timestamp": time.time(),
        })
        return cert

    def setup_pinning(self) -> None:
        """Cross-pin all node certificates for mutual authentication."""
        for bid, registry in self._pin_registries.items():
            for peer_id, peer_cert in self._node_certs.items():
                if peer_id != bid:
                    registry.pin(peer_id, peer_cert.fingerprint)

    def establish_session(
        self, client_id: str, server_id: str
    ) -> MTLSSession:
        """Establish an mTLS session between two building nodes."""
        client_cert = self._node_certs.get(client_id)
        server_cert = self._node_certs.get(server_id)

        if not client_cert or not server_cert:
            raise ValueError(
                f"Missing certificate: client={client_id in self._node_certs}, "
                f"server={server_id in self._node_certs}"
            )

        session = MTLSSession.establish(
            client_id, server_id,
            client_cert, server_cert,
            self.ca,
            self._pin_registries.get(client_id),
            self._pin_registries.get(server_id),
        )
        self._sessions.append(session)

        self._audit_log.append({
            "event": "MTLS_SESSION",
            "client": client_id,
            "server": server_id,
            "authenticated": session.is_authenticated,
            "session_id": session.session_id,
            "timestamp": time.time(),
        })
        return session

    def check_all_certificates(self) -> List[Dict]:
        """Audit all certificates for expiry and validity."""
        results = []
        for bid, cert in self._node_certs.items():
            valid, reason = self.ca.validate_certificate(cert)
            results.append({
                "building_id": bid,
                "serial": cert.serial_number,
                "valid": valid,
                "reason": reason,
                "days_until_expiry": round(cert.days_until_expiry, 1),
                "fingerprint": cert.fingerprint[:16] + "...",
            })
        return results

    def rotate_certificate(
        self, building_id: str, public_key_hex: str
    ) -> CertificateInfo:
        """Rotate a building node's certificate (revoke old, issue new)."""
        old_cert = self._node_certs.get(building_id)
        if old_cert:
            self.ca.revoke(old_cert.serial_number)

        new_cert = self.provision_node(building_id, public_key_hex)

        # Update pins in all peer registries
        for bid, registry in self._pin_registries.items():
            if bid != building_id:
                registry.pin(building_id, new_cert.fingerprint)

        self._audit_log.append({
            "event": "CERT_ROTATED",
            "building_id": building_id,
            "old_serial": old_cert.serial_number if old_cert else None,
            "new_serial": new_cert.serial_number,
            "timestamp": time.time(),
        })
        return new_cert

    def get_audit_log(self) -> List[Dict]:
        return list(self._audit_log)

    def get_security_summary(self) -> Dict:
        """Return a comprehensive transport security summary."""
        cert_audit = self.check_all_certificates()
        return {
            "ca": self.ca.ca_cert.to_dict(),
            "total_nodes": len(self._node_certs),
            "certificates": cert_audit,
            "total_sessions": len(self._sessions),
            "authenticated_sessions": sum(
                1 for s in self._sessions if s.is_authenticated
            ),
            "pin_violations": sum(
                r.violation_count for r in self._pin_registries.values()
            ),
            "revoked_certificates": len(self.ca._revoked_serials),
        }
