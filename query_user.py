"""
buildings_prototype/query_user.py — Zero-Trust User-Seeking-Location CLI
==========================================================================
Interactive CLI demonstrating the full Zero-Trust Infrastructure & Application-Level
Persona Privacy pipeline for one user seeking the location of another user.

Usage:
  python query_user.py --caller student_alice --target prof_smith --query Q6
  python query_user.py --caller student_alice --target student_bob --query Q6   (Anti-Stalking Denied)
  python query_user.py --caller prof_smith --target student_alice --enrolled    (Roster Override)
  python query_user.py --caller student_alice --target prof_smith --query Q5   (Trajectory Denied on Current)
  python query_user.py --caller student_alice --target prof_smith --tamper     (Tamper Rejection)
  python query_user.py --caller student_alice --target prof_smith --replay     (Replay Rejection)
"""

import sys
import argparse
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from security.crypto import NodeIdentity
from security.campus_policy import (
    CampusRole, DisclosureLevel, QueryPurpose, QueryContext,
    register_occupant_role, register_class_roster, clear_campus_registry,
)
from security.authorize import clear_registry, clear_audit_log, set_enforce_policy
from security.user_privacy_protocol import (
    SecureLocationQueryGateway, UserClient, LocationQueryRequest,
    seal_location_request, unseal_location_response, SecureLocationEnvelope,
)
from sim.scenario_b1_b5 import run_scenario


def parse_role(role_str: str) -> CampusRole:
    val = role_str.strip().lower()
    if val in ("dean", "admin"):
        return CampusRole.DEAN
    elif val in ("teacher", "prof", "faculty"):
        return CampusRole.TEACHER
    elif val in ("student", "pupil"):
        return CampusRole.STUDENT
    elif val in ("visitor", "guest"):
        return CampusRole.VISITOR
    raise ValueError(f"Unknown campus role: {role_str}")


def main():
    parser = argparse.ArgumentParser(
        description="Zero-Trust User-Seeking-Location Verification CLI (5-Tier Disclosure & ABAC)",
    )
    parser.add_argument("--caller", default="student_alice", help="Requesting user ID")
    parser.add_argument("--caller-role", default="student", help="Role: student | teacher | dean | visitor")
    parser.add_argument("--target", default="", help="Target user ID (defaults to primary simulated occupant)")
    parser.add_argument("--target-role", default="teacher", help="Target role: student | teacher | dean | visitor")
    parser.add_argument("--query", default="Q6", choices=["Q6", "Q5", "Q3", "TRACK"], help="Query template type")
    parser.add_argument("--requested-level", default=None, choices=["L0", "L1", "L2", "L3", "L4"],
                        help="Explicit requested disclosure level (defaults to query type ceiling)")
    parser.add_argument("--purpose", default="general_lookup",
                        choices=["general_lookup", "office_hours", "admin_consultation",
                                 "academic_roster", "security_investigation", "audit"],
                        help="Declared business purpose for query (ABAC context)")
    parser.add_argument("--outside-office-hours", action="store_true",
                        help="Flag query as occurring outside official office hours")
    parser.add_argument("--authorized-investigation", action="store_true",
                        help="Flag administrative/security tracking as formally authorized")
    parser.add_argument("--time", type=float, default=0.0, help="Simulation time t (0 = last event)")
    parser.add_argument("--building", default="B1", help="Building identifier for Q3/Q5")
    parser.add_argument("--enrolled", action="store_true", help="Enroll target student in teacher caller's section roster")
    parser.add_argument("--tamper", action="store_true", help="Simulate active wire tampering on ciphertext")
    parser.add_argument("--replay", action="store_true", help="Simulate duplicate envelope replay attack")

    args = parser.parse_args()

    print("\n" + "=" * 76)
    print("  ZERO-TRUST USER-SEEKING-LOCATION PRIVACY PROTOCOL")
    print("  5-Tier Disclosure Hierarchy & Attribute-Based Access Control (ABAC)")
    print("=" * 76)

    # 1. Initialize simulation & gateway
    print("\n[*] Initializing DSTS simulation environment (Seed 42)...")
    sim_result = run_scenario(seed=42)
    dsts = sim_result.dsts
    primary_occ = sim_result.handoff.occupant_id
    last_sim_time = sim_result.events[-1].sim_time
    target_id = args.target if args.target else primary_occ
    query_time = args.time if args.time > 0.0 else last_sim_time

    clear_registry()
    clear_audit_log()
    clear_campus_registry()
    set_enforce_policy(True)

    caller_role = parse_role(args.caller_role)
    target_role = parse_role(args.target_role)

    register_occupant_role(args.caller, caller_role)
    register_occupant_role(target_id, target_role)

    if args.enrolled:
        register_class_roster(args.caller, [target_id])
        print(f"  [+] Enrolled '{target_id}' in class roster of '{args.caller}'")

    gateway = SecureLocationQueryGateway(dsts, gateway_id="GW_CENTRAL")
    caller_client = UserClient(args.caller, gateway, campus_role=caller_role)

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    print(f"  [+] Central Privacy Gateway provisioned ({gateway.gateway_id})")
    print(f"  [+] Caller:    {args.caller} [{caller_role.value.upper()}] | Ed25519: {caller_client.ed_pub[:8].hex()}... | X25519: {caller_client.x_pub[:8].hex()}...")
    print(f"  [+] Target:    {target_id} [{target_role.value.upper()}]")
    print(f"  [+] Query:     {args.query} at t={query_time:.1f}s")
    print(f"  [+] Context:   Purpose={args.purpose.upper()} | OfficeHours={'NO' if args.outside_office_hours else 'YES'} | AuthInvest={'YES' if args.authorized_investigation else 'NO'}")
    if args.requested_level:
        print(f"  [+] Requested: {args.requested_level}")

    # 2. Build Request
    params = {}
    if args.query == "Q6":
        params = {"at_time": query_time, "theta": 0.3}
    elif args.query == "Q5":
        params = {"building_id": args.building}
    elif args.query == "Q3":
        params = {"building_id": args.building, "before_time": query_time}
    elif args.query == "TRACK":
        params = {"building_id": args.building if args.building != "B1" else None}

    # Automatically map default purpose if caller is student and target is faculty/dean
    purpose = args.purpose
    if purpose == "general_lookup" and caller_role == CampusRole.STUDENT and target_role in (CampusRole.TEACHER, CampusRole.DEAN):
        purpose = "office_hours"

    request = LocationQueryRequest(
        caller_id=args.caller,
        target_id=target_id,
        query_type=args.query,
        params=params,
        requested_level=args.requested_level,
        purpose=purpose,
        is_office_hours=not args.outside_office_hours,
        authorized_investigation=args.authorized_investigation,
    )

    # 3. Seal into Zero-Trust Wire Envelope
    print("\n" + "-" * 76)
    print("  PHASE 1: ZERO-TRUST ENVELOPE SEPARATION & CRYPTOGRAPHY")
    print("-" * 76)
    req_env = seal_location_request(request, caller_client.identity, caller_client.gw_x_pub)
    print(f"  [+] Ephemeral X25519 ECDH exchange & HKDF-SHA256 session key derived")
    print(f"  [+] AES-128-GCM encrypted payload: {req_env.encrypted_payload[:32]}... ({len(req_env.encrypted_payload)} chars)")
    print(f"  [+] 96-bit Cryptographic Nonce:     {req_env.encryption_nonce}")
    print(f"  [+] Ed25519 Digital Signature:      {req_env.signature[:32]}... ({len(req_env.signature)} chars)")
    print(f"  [+] Freshness Timestamp:            {req_env.timestamp:.3f} (window <= 300s)")

    if args.tamper:
        print("\n  [!] SIMULATING MAN-IN-THE-MIDDLE ATTACK: Corrupting wire ciphertext...")
        raw_ct = list(req_env.encrypted_payload)
        raw_ct[6] = '0' if raw_ct[6] != '0' else 'f'
        req_env.encrypted_payload = "".join(raw_ct)

    # 4. Gateway Processing
    print("\n" + "-" * 76)
    print("  PHASE 2: GATEWAY VERIFICATION & AUTHORIZATION (ABAC EVALUATION)")
    print("-" * 76)

    resp_env = gateway.process_query_envelope(req_env)

    if args.replay:
        print("\n  [!] SIMULATING REPLAY ATTACK: Resubmitting identical envelope...")
        resp2_env = gateway.process_query_envelope(req_env)
        resp2 = unseal_location_response(resp2_env, caller_client.identity, caller_client.gw_x_pub, caller_client.gw_ed_pub)
        print(f"  [+] Replay Result: {resp2.reason}")
        return

    # 5. Unseal Response
    resp = unseal_location_response(resp_env, caller_client.identity, caller_client.gw_x_pub, caller_client.gw_ed_pub)

    print(f"  [+] Gateway Signature & Decryption: VERIFIED")
    print(f"  [+] Permission Granted:             {'YES (200 OK)' if resp.permitted else 'DENIED (403 Forbidden)'}")
    print(f"  [+] Policy Reason:                  {resp.reason}")
    print(f"  [+] Disclosure Level Evaluated:     {resp.disclosure_level}")
    print(f"  [+] Access Scope:                   {resp.access_scope.upper()}")
    print(f"  [+] Location Granularity:           {resp.location_granularity.upper()} (Precision: {resp.precision})")
    print(f"  [+] Principle Applied:              Minimum Necessary Disclosure")

    # 6. Result Inspection & Location/Availability Separation
    print("\n" + "-" * 76)
    print("  PHASE 3: QUERY RESULT & INFORMATION EXPOSURE")
    print("-" * 76)

    if resp.permitted:
        print(f"  SUCCESS! Disclosed Level: [{resp.disclosure_level}]")
        if isinstance(resp.answer, dict) and ("most_frequented_zone" in resp.answer or "most_frequented_sector" in resp.answer):
            print(f"\n  --- SPATIAL & MOVEMENT ANALYTICS (Clearance: {resp.disclosure_level}) ---")
            ans = resp.answer
            print(f"    * Target Occupant:           {ans.get('occupant_id')}")
            print(f"    * Total Detections Tracked:  {ans.get('total_events')}")
            print(f"    * Total Track Window:        {ans.get('total_tracked_time_seconds')}s")

            if "most_frequented_zone" in ans and ans["most_frequented_zone"]:
                mf = ans["most_frequented_zone"]
                print(f"    * Most Frequented Room:      {mf.get('room')} ({mf.get('visit_count')} visits)")
            elif "most_frequented_sector" in ans and ans["most_frequented_sector"]:
                mfs = ans["most_frequented_sector"]
                print(f"    * Most Frequented Sector:    {mfs.get('sector')} ({mfs.get('visit_count')} visits)")

            if "longest_stay_zone" in ans and ans["longest_stay_zone"]:
                ls = ans["longest_stay_zone"]
                print(f"    * Longest Stay Room:         {ls.get('room')} ({ls.get('duration_seconds')}s)")
            elif "longest_stay_building" in ans and ans["longest_stay_building"]:
                lsb = ans["longest_stay_building"]
                print(f"    * Longest Stay Building:     {lsb.get('building')} ({lsb.get('duration_seconds')}s)")

            if "dwell_times_by_room" in ans:
                print("\n    * Dwell Duration by Room (Exact Analytics):")
                for r, dur in sorted(ans["dwell_times_by_room"].items(), key=lambda x: -x[1]):
                    print(f"        - {r:20s} : {dur:6.1f}s")
            if "dwell_times_by_sector" in ans:
                print("\n    * Dwell Duration by Sector (Coarse Analytics):")
                for s, dur in sorted(ans["dwell_times_by_sector"].items(), key=lambda x: -x[1]):
                    print(f"        - {s:25s} : {dur:6.1f}s")
            if "dwell_times_by_building" in ans:
                print("    * Dwell Duration by Building:")
                for b, dur in ans["dwell_times_by_building"].items():
                    print(f"        - Building {b}: {dur:6.1f}s")

            if "waypoints" in ans and ans["waypoints"]:
                print(f"\n    * Movement Waypoints Disclosed ({len(ans['waypoints'])} points):")
                for pt in ans["waypoints"][:6]:
                    print(f"        - {pt}")
                if len(ans["waypoints"]) > 6:
                    print(f"        ... [{len(ans['waypoints']) - 6} additional waypoints]")
        elif "L1" in resp.disclosure_level:
            print("  --- Availability Status (Location vs. Availability Principle) ---")
            if isinstance(resp.answer, dict):
                print(f"    * Availability:          {resp.answer.get('availability', 'Unknown')}")
                print(f"    * Present in Cabin:      {resp.answer.get('is_present', False)}")
                print(f"    * Designated Location:   {resp.answer.get('designated_location', 'N/A')}")
                print(f"    * Note: Campus coordinates and functional zone are completely REDACTED.")
            else:
                print(f"    * Answer: {resp.answer}")
        elif "L2" in resp.disclosure_level:
            print("  --- Functional Zone Disclosure (Coarse Precision) ---")
            if isinstance(resp.answer, dict):
                for k, v in resp.answer.items():
                    print(f"    * {k}: {v}")
            else:
                print(f"    * Current Zone: {resp.answer}")
            print("    * Note: Precise room coordinates redacted for privacy.")
        elif "L3" in resp.disclosure_level:
            print("  --- Precise Point-in-Time Location Disclosure ---")
            if isinstance(resp.answer, dict):
                for k, v in resp.answer.items():
                    print(f"    * {k}: {v}")
            else:
                print(f"    * Exact Location: {resp.answer}")
        elif "L4" in resp.disclosure_level:
            print("  --- Historical Trajectory Disclosure (Audited Authority) ---")
            if isinstance(resp.answer, list):
                print(f"    * Trajectory Points Disclosed: {len(resp.answer)}")
                for pt in resp.answer[:5]:
                    print(f"      - {pt}")
                if len(resp.answer) > 5:
                    print(f"      ... [{len(resp.answer) - 5} additional trajectory waypoints]")
            else:
                print(f"    * Answer: {resp.answer}")

        print("\n  Evidence Trace:")
        for ev in resp.evidence:
            print(f"    {ev}")
    else:
        print(f"  ACCESS BLOCKED: Minimum Necessary Disclosure & Zero-Trust Rejection.")
        print(f"  Reason: {resp.reason}")
        for ev in resp.evidence:
            print(f"    * {ev}")

    print("\n" + "=" * 76)


if __name__ == "__main__":
    main()
