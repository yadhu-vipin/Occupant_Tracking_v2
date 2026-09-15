"""
v6/security/user_privacy_protocol.py — Zero-Trust User Location Query Protocol & Privacy Gateway
==================================================================================================
Implements the multi-tiered zero-trust protocol and application-level persona privacy engine
for a user seeking the location of another user:

1. Zero-Trust Cryptographic Channel & Envelope:
   - Client and Gateway possess cryptographic identities (Ed25519 signing + X25519 ECDH).
   - Requests and responses are authenticated with Ed25519 digital signatures,
     encrypted using ephemeral AES-128-GCM session keys (HKDF-SHA256), and protected
     against replay attacks via ReplayGuard (nonce cache + 300s window).

2. Layer-1 Infrastructure RBAC:
   - Evaluates whether caller client has 'QUERY' permission on target 'occupant:<id>'
     via authorize(). System targets are strictly locked to Role.ADMIN.

3. Layer-2 Application Persona & Contextual Privacy (ReBAC):
   - Evaluates human personas: Dean, Teacher, Student, Visitor.
   - Pairwise matrix evaluates (AccessScope, LocationGranularity):
     • Self-query exemption (caller_id == target_id) -> (FULL_TRACK, PRECISE)
     • Teacher class-roster section override:
         - Student in teacher's roster -> (CURRENT, ZONE)
         - Student not in roster -> (NONE, NONE)
     • Anti-stalking: Student -> peer Student -> (NONE, NONE)
     • Office hours: Student -> Teacher -> (CURRENT, ZONE)
     • Visitor queries -> (CURRENT, ZONE) for Dean/Teachers, (NONE, NONE) for Students/Visitors
     • Dean queries -> (FULL_TRACK, PRECISE) on staff/students, (CURRENT, ZONE) on peers/visitors

4. Temporal Access Gating:
   - Point-in-time queries (e.g. Q6) permitted under CURRENT or FULL_TRACK.
   - Historical trajectory queries (e.g. Q3, Q5) strictly require FULL_TRACK access.

5. Hierarchical Location Precision Obfuscation:
   - PRECISE -> EXACT room code (e.g. z3), room label (Office), probability, coordinates.
   - ZONE -> COARSE functional sector on single floor (e.g. North-West Wing), quantized time.
   - NONE / Minimal -> ABSTRACT building presence only (e.g. Inside B1), room details redacted.

6. Auditing & Sealed Transmission:
   - Full audit trail recorded.
   - Query response sealed in an authenticated, encrypted envelope back to caller.
"""

import os
import time
import json
import hashlib
from dataclasses import dataclass, asdict, field
from typing import Optional, Dict, Any, List, Tuple

from security.crypto import (
    NodeIdentity, SecureChannel, load_ed25519_public, load_x25519_public,
    aes_gcm_encrypt, aes_gcm_decrypt, generate_nonce,
)
from security.replay_guard import ReplayGuard
from security.authorize import (
    Role, PrecisionLevel, authorize, register_node as authorize_register_node,
    set_enforce_policy, audit, get_audit_log,
)
from security.campus_policy import (
    CampusRole, DisclosureLevel, QueryPurpose, QueryContext,
    AccessScope, LocationGranularity, PolicyDecision,
    evaluate_campus_query_policy, register_occupant_role,
    register_class_roster, get_occupant_role, check_office_presence,
)
from dsts.queries import QueryEngine, QueryResult
from dsts.dsts import DSTS


# ─── Data Structures ──────────────────────────────────────────────────────────

@dataclass
class LocationQueryRequest:
    """Cleartext location query request submitted by a user client."""
    caller_id: str
    target_id: str
    query_type: str = "Q6"                  # "Q6" (point-in-time), "Q3" (exit), "Q5" (trajectory)
    params: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0
    nonce: str = ""
    client_node_id: str = ""
    requested_level: Optional[str] = None   # "L0", "L1", "L2", "L3", "L4"
    purpose: str = "general_lookup"         # QueryPurpose name
    is_office_hours: bool = True            # Whether query occurs within official office hours
    authorized_investigation: bool = False  # Explicit authorization for administrative/security L4 tracking

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()
        if not self.nonce:
            self.nonce = os.urandom(12).hex()
        if not self.client_node_id:
            self.client_node_id = self.caller_id

    def to_json(self) -> bytes:
        return json.dumps(asdict(self), separators=(',', ':')).encode('utf-8')

    @classmethod
    def from_json(cls, data: bytes) -> 'LocationQueryRequest':
        return cls(**json.loads(data.decode('utf-8')))


@dataclass
class LocationQueryResponse:
    """Cleartext location query response computed by the privacy gateway."""
    query_id: str
    permitted: bool
    answer: Any
    evidence: List[str] = field(default_factory=list)
    precision: str = "ABSTRACT"
    access_scope: str = "none"
    location_granularity: str = "none"
    disclosure_level: str = "L0_NONE"
    reason: str = ""
    timestamp: float = 0.0
    request_nonce: str = ""
    audit_id: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()
        if not self.audit_id:
            self.audit_id = hashlib.sha256(os.urandom(16)).hexdigest()[:12]

    def to_json(self) -> bytes:
        return json.dumps(asdict(self), separators=(',', ':')).encode('utf-8')

    @classmethod
    def from_json(cls, data: bytes) -> 'LocationQueryResponse':
        return cls(**json.loads(data.decode('utf-8')))


@dataclass
class SecureLocationEnvelope:
    """Wire envelope carrying AES-128-GCM encrypted and Ed25519 signed location messages."""
    sender_id: str
    receiver_id: str
    encrypted_payload: str          # hex-encoded ciphertext
    encryption_nonce: str           # hex-encoded AES-GCM nonce
    signature: str                  # hex-encoded Ed25519 signature of (nonce + ciphertext)
    message_id: str                 # unique tracking ID
    timestamp: float                # creation timestamp for replay freshness
    protocol_version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SecureLocationEnvelope':
        return cls(**data)

    def to_json(self) -> bytes:
        return json.dumps(self.to_dict(), separators=(',', ':')).encode('utf-8')

    @classmethod
    def from_json(cls, raw: bytes) -> 'SecureLocationEnvelope':
        return cls.from_dict(json.loads(raw.decode('utf-8')))


# ─── Sealing and Unsealing Helpers ────────────────────────────────────────────

def seal_location_request(
    request: LocationQueryRequest,
    client_identity: NodeIdentity,
    gateway_x25519_public_bytes: bytes,
    receiver_id: str = "GW_CENTRAL",
) -> SecureLocationEnvelope:
    """Seal a location query request into an encrypted and signed envelope."""
    peer_pub = load_x25519_public(gateway_x25519_public_bytes)
    channel = SecureChannel(client_identity, peer_pub)
    plaintext = request.to_json()

    sealed = channel.seal(plaintext)
    return SecureLocationEnvelope(
        sender_id=client_identity.building_id,
        receiver_id=receiver_id,
        encrypted_payload=sealed["ciphertext"],
        encryption_nonce=sealed["nonce"],
        signature=sealed["signature"],
        message_id=sealed["message_id"],
        timestamp=sealed["timestamp"],
    )


def unseal_location_request(
    envelope: SecureLocationEnvelope,
    gateway_identity: NodeIdentity,
    client_x25519_public_bytes: bytes,
    client_ed25519_public_bytes: bytes,
) -> LocationQueryRequest:
    """Unseal and authenticate a location query request envelope."""
    peer_x25519 = load_x25519_public(client_x25519_public_bytes)
    peer_ed25519 = load_ed25519_public(client_ed25519_public_bytes)
    channel = SecureChannel(gateway_identity, peer_x25519)

    sealed_dict = {
        "sender": envelope.sender_id,
        "nonce": envelope.encryption_nonce,
        "ciphertext": envelope.encrypted_payload,
        "signature": envelope.signature,
    }
    plaintext = channel.open(sealed_dict, peer_ed25519)
    return LocationQueryRequest.from_json(plaintext)


def seal_location_response(
    response: LocationQueryResponse,
    gateway_identity: NodeIdentity,
    client_x25519_public_bytes: bytes,
    receiver_id: str,
) -> SecureLocationEnvelope:
    """Seal a location query response into an encrypted and signed envelope."""
    peer_pub = load_x25519_public(client_x25519_public_bytes)
    channel = SecureChannel(gateway_identity, peer_pub)
    plaintext = response.to_json()

    sealed = channel.seal(plaintext)
    return SecureLocationEnvelope(
        sender_id=gateway_identity.building_id,
        receiver_id=receiver_id,
        encrypted_payload=sealed["ciphertext"],
        encryption_nonce=sealed["nonce"],
        signature=sealed["signature"],
        message_id=sealed["message_id"],
        timestamp=sealed["timestamp"],
    )


def unseal_location_response(
    envelope: SecureLocationEnvelope,
    client_identity: NodeIdentity,
    gateway_x25519_public_bytes: bytes,
    gateway_ed25519_public_bytes: bytes,
) -> LocationQueryResponse:
    """Unseal and authenticate a location query response envelope."""
    peer_x25519 = load_x25519_public(gateway_x25519_public_bytes)
    peer_ed25519 = load_ed25519_public(gateway_ed25519_public_bytes)
    channel = SecureChannel(client_identity, peer_x25519)

    sealed_dict = {
        "sender": envelope.sender_id,
        "nonce": envelope.encryption_nonce,
        "ciphertext": envelope.encrypted_payload,
        "signature": envelope.signature,
    }
    plaintext = channel.open(sealed_dict, peer_ed25519)
    return LocationQueryResponse.from_json(plaintext)


# ─── Secure Location Query Gateway ────────────────────────────────────────────

class SecureLocationQueryGateway:
    """
    Zero-Trust Central Location Gateway.

    Terminates the zero-trust cryptographic channel, authenticates clients,
    enforces Layer-1 RBAC and Layer-2 Persona ReBAC, applies temporal &
    precision filters, queries DSTS, and returns sealed responses.
    """

    def __init__(
        self,
        dsts: DSTS,
        gateway_id: str = "GW_CENTRAL",
        replay_guard: Optional[ReplayGuard] = None,
        enforce_policy: bool = True,
    ):
        self.dsts = dsts
        self.gateway_id = gateway_id
        self.gateway_identity = NodeIdentity.generate(gateway_id)
        self.replay_guard = replay_guard or ReplayGuard(window_seconds=300.0)
        self.query_engine = QueryEngine(dsts)

        # Registry of provisioned clients: {client_id: (x25519_pub_bytes, ed25519_pub_bytes)}
        self.client_keys: Dict[str, Tuple[bytes, bytes]] = {}

        if enforce_policy:
            set_enforce_policy(True)

        # Register gateway node itself
        authorize_register_node(gateway_id, Role.ADMIN, PrecisionLevel.EXACT)

    def register_client(
        self,
        client_id: str,
        role: Role = Role.QUERY_CLIENT,
        campus_role: Optional[CampusRole] = None,
        identity: Optional[NodeIdentity] = None,
    ) -> Tuple[NodeIdentity, bytes, bytes]:
        """
        Provision a client identity with keys, RBAC role, and optional campus persona.
        Returns (identity, x25519_pub_bytes, ed25519_pub_bytes).
        """
        client_ident = identity or NodeIdentity.generate(client_id)
        x_pub = client_ident.x25519_public_bytes()
        ed_pub = client_ident.ed25519_public_bytes()

        self.client_keys[client_id] = (x_pub, ed_pub)
        authorize_register_node(client_id, role)

        if campus_role is not None:
            register_occupant_role(client_id, campus_role)

        audit("CLIENT_PROVISIONED", client=client_id, role=role.value,
              campus_role=campus_role.value if campus_role else None)
        return client_ident, x_pub, ed_pub

    def process_query_envelope(self, envelope: SecureLocationEnvelope) -> SecureLocationEnvelope:
        """
        End-to-end processing pipeline for an incoming sealed location query envelope.
        Performs 6 phases of zero-trust verification and privacy enforcement.
        """
        sender_id = envelope.sender_id
        if sender_id not in self.client_keys:
            # Unregistered client key -> Reject with denied envelope
            denied_resp = LocationQueryResponse(
                query_id="UNKNOWN",
                permitted=False,
                answer=None,
                evidence=["Access DENIED: Unknown client identity key not registered in Gateway"],
                reason="unregistered_client_identity",
            )
            # Cannot encrypt with unknown key; raise or handle
            raise PermissionError(f"Client '{sender_id}' not registered with Gateway")

        client_x_pub, client_ed_pub = self.client_keys[sender_id]

        # ── Phase 1: Replay Guard & Freshness Verification ────────────────────
        validation = self.replay_guard.validate(
            nonce=envelope.encryption_nonce,
            timestamp=envelope.timestamp,
            message_id=envelope.message_id,
        )
        if not validation.accepted:
            denied_resp = LocationQueryResponse(
                query_id="UNKNOWN",
                permitted=False,
                answer=None,
                evidence=[f"Zero-Trust Guard REJECTED: {validation.reason}"],
                reason=f"replay_guard_rejected: {validation.reason}",
            )
            return seal_location_response(denied_resp, self.gateway_identity, client_x_pub, sender_id)

        # ── Phase 2: Decryption & Digital Signature Authentication ─────────────
        try:
            request = unseal_location_request(
                envelope, self.gateway_identity, client_x_pub, client_ed_pub,
            )
        except Exception as ex:
            denied_resp = LocationQueryResponse(
                query_id="UNKNOWN",
                permitted=False,
                answer=None,
                evidence=[f"Zero-Trust Handshake FAILED: signature or decryption error: {str(ex)}"],
                reason="cryptographic_handshake_failed",
            )
            return seal_location_response(denied_resp, self.gateway_identity, client_x_pub, sender_id)

        # ── Phase 3: Layer-1 Infrastructure RBAC Authorization ────────────────
        target_resource = f"occupant:{request.target_id}"
        if not authorize(sender_id, "QUERY", target=target_resource):
            last_audit = get_audit_log()
            reason_str = last_audit[-1].get("reason", "") if last_audit else ""
            if "target_occupant_denied_by_policy" in reason_str:
                reason_val = f"privacy_policy_denial: {reason_str}"
            else:
                reason_val = f"layer1_rbac_unauthorized: {reason_str}"
            denied_resp = LocationQueryResponse(
                query_id=request.query_type,
                permitted=False,
                answer=None,
                evidence=[f"Access DENIED: principal '{sender_id}' unauthorized for QUERY on '{target_resource}' ({reason_str})"],
                precision="DENIED",
                reason=reason_val,
                request_nonce=request.nonce,
            )
            return seal_location_response(denied_resp, self.gateway_identity, client_x_pub, sender_id)

        # ── Phase 4: Layer-2 Persona ReBAC Privacy Policy Evaluation ──────────
        caller_campus_role = get_occupant_role(request.caller_id)
        target_campus_role = get_occupant_role(request.target_id)

        # Parse purpose & context from request
        try:
            purpose_enum = QueryPurpose.from_str(request.purpose)
        except Exception:
            purpose_enum = QueryPurpose.GENERAL_LOOKUP

        query_ctx = QueryContext(
            purpose=purpose_enum,
            is_office_hours=request.is_office_hours,
            authorized_investigation=request.authorized_investigation,
        )

        decision = evaluate_campus_query_policy(
            caller_id=request.caller_id,
            target_id=request.target_id,
            caller_role=caller_campus_role,
            target_role=target_campus_role,
            requested_level=request.requested_level,
            purpose=purpose_enum,
            context=query_ctx,
        )

        if not decision.permitted or decision.level == DisclosureLevel.L0_NONE:
            denied_resp = LocationQueryResponse(
                query_id=request.query_type,
                permitted=False,
                answer=None,
                evidence=[f"Access DENIED by campus privacy policy: {decision.reason}"],
                precision="DENIED",
                reason=f"privacy_policy_denial: {decision.reason}",
                access_scope="none",
                location_granularity="none",
                disclosure_level="L0_NONE",
                request_nonce=request.nonce,
            )
            return seal_location_response(denied_resp, self.gateway_identity, client_x_pub, sender_id)

        # Check temporal scope: trajectory queries (Q3, Q5) require L4_HISTORICAL_TRACK
        if request.query_type in ("Q3", "Q5") and decision.level < DisclosureLevel.L4_HISTORICAL_TRACK:
            denied_resp = LocationQueryResponse(
                query_id=request.query_type,
                permitted=False,
                answer=None,
                evidence=[
                    f"Access DENIED: Query '{request.query_type}' requires historical trajectory "
                    f"(L4_HISTORICAL_TRACK) access, but caller '{request.caller_id}' only holds {decision.level.name}."
                ],
                precision="DENIED",
                reason=f"temporal_scope_denied: {decision.level.name}_trajectory_requested",
                access_scope=decision.access.value,
                location_granularity=decision.granularity.value,
                disclosure_level=decision.level.name,
                request_nonce=request.nonce,
            )
            return seal_location_response(denied_resp, self.gateway_identity, client_x_pub, sender_id)

        # Map disclosure level to query engine precision
        if decision.level == DisclosureLevel.L1_PRESENCE:
            eff_precision = PrecisionLevel.COARSE
        elif decision.level == DisclosureLevel.L2_CURRENT_ZONE:
            eff_precision = PrecisionLevel.COARSE
        elif decision.level in (DisclosureLevel.L3_PRECISE_CURRENT, DisclosureLevel.L4_HISTORICAL_TRACK):
            eff_precision = PrecisionLevel.EXACT
        else:
            eff_precision = PrecisionLevel.ABSTRACT

        access_scope_str = decision.access.value
        granularity_str = decision.granularity.value
        disclosure_level_str = decision.level.name
        decision_reason = decision.reason

        # Pass policy decision to query engine so L1_PRESENCE availability check triggers
        self.query_engine.last_policy_decision = decision

        # ── Phase 5: Query Execution & Hierarchical Location Obfuscation ──────
        q_type = request.query_type.upper()
        if q_type == "Q6":
            at_time = float(request.params.get("at_time", time.time()))
            theta = float(request.params.get("theta", 0.3))
            q_result = self.query_engine.Q6(
                occupant_id=request.target_id,
                at_time=at_time,
                theta=theta,
                precision=eff_precision,
                caller_id=request.caller_id,
            )
        elif q_type == "Q5":
            building_id = str(request.params.get("building_id", "B1"))
            q_result = self.query_engine.Q5(
                occupant_id=request.target_id,
                building_id=building_id,
                precision=eff_precision,
                caller_id=request.caller_id,
            )
        elif q_type == "Q3":
            building_id = str(request.params.get("building_id", "B1"))
            before_time = float(request.params.get("before_time", time.time()))
            q_result = self.query_engine.Q3(
                occupant_id=request.target_id,
                building_id=building_id,
                before_time=before_time,
                precision=eff_precision,
                caller_id=request.caller_id,
            )
        elif q_type in ("TRACK", "TRACK_ANALYTICS", "ANALYTICS"):
            building_id = request.params.get("building_id", None)
            q_result = self.query_engine.analyze_occupant_track(
                occupant_id=request.target_id,
                building_id=building_id,
                precision=eff_precision,
                caller_id=request.caller_id,
            )
        else:
            # Fallback to general dispatch
            q_result = self.query_engine.execute_query(
                request.query_type,
                occupant_id=request.target_id,
                precision=eff_precision,
                caller_id=request.caller_id,
                **request.params,
            )

        # ── Phase 6: Response Sealing & Audit Trail ───────────────────────────
        response = LocationQueryResponse(
            query_id=q_result.query_id,
            permitted=q_result.success,
            answer=q_result.answer,
            evidence=q_result.evidence,
            precision=q_result.precision,
            access_scope=access_scope_str,
            location_granularity=granularity_str,
            disclosure_level=disclosure_level_str,
            reason=decision_reason,
            request_nonce=request.nonce,
        )

        audit("LOCATION_QUERY_SERVED",
              caller=request.caller_id,
              target=request.target_id,
              query=request.query_type,
              precision=response.precision,
              access_scope=access_scope_str,
              disclosure_level=disclosure_level_str,
              decision="PERMIT" if response.permitted else "DENY")

        return seal_location_response(response, self.gateway_identity, client_x_pub, sender_id)


# ─── User Client ──────────────────────────────────────────────────────────────

class UserClient:
    """
    Client agent representing a human user (student, teacher, dean, visitor)
    seeking the location of another user through the Zero-Trust Privacy Gateway.
    """

    def __init__(
        self,
        user_id: str,
        gateway: SecureLocationQueryGateway,
        campus_role: CampusRole = CampusRole.STUDENT,
        identity: Optional[NodeIdentity] = None,
    ):
        self.user_id = user_id
        self.gateway = gateway
        self.campus_role = campus_role

        # Provision identity and register with gateway
        self.identity, self.x_pub, self.ed_pub = gateway.register_client(
            client_id=user_id,
            role=Role.QUERY_CLIENT,
            campus_role=campus_role,
            identity=identity,
        )

        # Gateway public keys
        self.gw_x_pub = gateway.gateway_identity.x25519_public_bytes()
        self.gw_ed_pub = gateway.gateway_identity.ed25519_public_bytes()

    def seek_user_location(
        self,
        target_id: str,
        at_time: float,
        theta: float = 0.3,
        purpose: str = "general_lookup",
        is_office_hours: bool = True,
        authorized_investigation: bool = False,
        requested_level: Optional[str] = None,
    ) -> LocationQueryResponse:
        """
        Seek the current or point-in-time location of another user (Q6).
        Encrypts request, sends via zero-trust envelope, and decrypts response.
        """
        request = LocationQueryRequest(
            caller_id=self.user_id,
            target_id=target_id,
            query_type="Q6",
            params={"at_time": at_time, "theta": theta},
            purpose=purpose,
            is_office_hours=is_office_hours,
            authorized_investigation=authorized_investigation,
            requested_level=requested_level,
        )
        req_envelope = seal_location_request(
            request, self.identity, self.gw_x_pub, receiver_id=self.gateway.gateway_id,
        )

        resp_envelope = self.gateway.process_query_envelope(req_envelope)
        return unseal_location_response(
            resp_envelope, self.identity, self.gw_x_pub, self.gw_ed_pub,
        )

    def seek_user_availability(
        self,
        target_id: str,
        at_time: Optional[float] = None,
        is_office_hours: bool = True,
    ) -> LocationQueryResponse:
        """
        Check if another user is present in their designated office/cabin (L1 Availability query).
        Privacy-preserving: returns availability without revealing campus coordinates when away.
        """
        return self.seek_user_location(
            target_id=target_id,
            at_time=at_time if at_time is not None else time.time(),
            purpose="office_hours",
            is_office_hours=is_office_hours,
            requested_level="L1_PRESENCE",
        )

    def check_user_trajectory(
        self,
        target_id: str,
        building_id: str,
        purpose: str = "general_lookup",
        authorized_investigation: bool = False,
    ) -> LocationQueryResponse:
        """
        Query if another user visited all zones in a building (Q5).
        Requires 'L4_HISTORICAL_TRACK' trajectory access.
        """
        request = LocationQueryRequest(
            caller_id=self.user_id,
            target_id=target_id,
            query_type="Q5",
            params={"building_id": building_id},
            purpose=purpose,
            authorized_investigation=authorized_investigation,
        )
        req_envelope = seal_location_request(
            request, self.identity, self.gw_x_pub, receiver_id=self.gateway.gateway_id,
        )

        resp_envelope = self.gateway.process_query_envelope(req_envelope)
        return unseal_location_response(
            resp_envelope, self.identity, self.gw_x_pub, self.gw_ed_pub,
        )

    def check_user_exit(
        self,
        target_id: str,
        building_id: str,
        before_time: float,
        purpose: str = "general_lookup",
        authorized_investigation: bool = False,
    ) -> LocationQueryResponse:
        """
        Query if another user left a building before a given time (Q3).
        Requires 'L4_HISTORICAL_TRACK' trajectory access.
        """
        request = LocationQueryRequest(
            caller_id=self.user_id,
            target_id=target_id,
            query_type="Q3",
            params={"building_id": building_id, "before_time": before_time},
            purpose=purpose,
            authorized_investigation=authorized_investigation,
        )
        req_envelope = seal_location_request(
            request, self.identity, self.gw_x_pub, receiver_id=self.gateway.gateway_id,
        )

        resp_envelope = self.gateway.process_query_envelope(req_envelope)
        return unseal_location_response(
            resp_envelope, self.identity, self.gw_x_pub, self.gw_ed_pub,
        )

    def analyze_user_track(
        self,
        target_id: str,
        building_id: Optional[str] = None,
        purpose: str = "general_lookup",
        authorized_investigation: bool = False,
    ) -> LocationQueryResponse:
        """
        Query and analyze occupant movement track, dwell times, and spatial patterns.
        Permitted for L4 (exact rooms/dwell) and L2 (building/sector dwell), denied for L1/L0.
        """
        params = {}
        if building_id:
            params["building_id"] = building_id
        request = LocationQueryRequest(
            caller_id=self.user_id,
            target_id=target_id,
            query_type="TRACK",
            params=params,
            purpose=purpose,
            authorized_investigation=authorized_investigation,
        )
        req_envelope = seal_location_request(
            request, self.identity, self.gw_x_pub, receiver_id=self.gateway.gateway_id,
        )

        resp_envelope = self.gateway.process_query_envelope(req_envelope)
        return unseal_location_response(
            resp_envelope, self.identity, self.gw_x_pub, self.gw_ed_pub,
        )
