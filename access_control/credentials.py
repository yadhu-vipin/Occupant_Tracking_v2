"""Per-occupant login credentials.

``DSTS_LOGIN_KEY`` is the *master* secret held by the operator. An occupant's credential is
``HMAC-SHA256(master, "dsts-login:" + occupant_id)`` (first 32 hex characters), so knowing one occupant's credential
never lets anybody sign in as another occupant, and the master key itself is never handed out.

    python -m access_control.credentials 0001738        # prints this occupant's credential (needs DSTS_LOGIN_KEY)
"""
from __future__ import annotations

import hashlib
import hmac
import os
import sys
from typing import Optional


def _master() -> Optional[bytes]:
    key = os.environ.get("DSTS_LOGIN_KEY", "")
    return key.encode("utf-8") if key else None


def credential_for(occupant_id: str, master: Optional[bytes] = None) -> Optional[str]:
    master = master if master is not None else _master()
    if not master:
        return None
    return hmac.new(master, b"dsts-login:" + occupant_id.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def verify_credential(occupant_id: str, supplied: Optional[str]) -> bool:
    expected = credential_for(occupant_id)
    if expected is None or not supplied:
        return False
    return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    cred = credential_for(sys.argv[1].strip())
    if cred is None:
        raise SystemExit("DSTS_LOGIN_KEY is not set")
    print(cred)
