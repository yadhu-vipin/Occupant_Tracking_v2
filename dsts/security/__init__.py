"""The standalone demo's lightweight security layer (from the Lane D hand-over, dsts_final.zip).

``transport.py``  HMAC-signed, replay-protected in-process transport between the demo's fake buildings.
``access.py``     the demo's three role views (officer / analyst / self) over query rows.

This is NOT the project's security: the real, tested layers are ``access_control/`` (RBAC, tokens, login lockout) and
``secure_transfer/`` (X25519 / Ed25519 / AES-GCM sealed payloads, CA certificates, replay guard). The pieces here exist
so ``python manage.py standalone`` and ``python -m scripts.terminal_walk`` run anywhere, with no corpus or pipeline run.
"""
