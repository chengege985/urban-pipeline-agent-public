"""Shared collaboration contracts; validation is not authentication.

These envelopes are not registered HTTP endpoints. Platform identity, current
ACLs, dataset classification and confirmation must be rechecked by a future
controlled adapter before any tool operation. Existing OpenAPI DTOs remain the
authority for existing business payloads.
"""

CONTRACT_VERSION = "module-boundaries/1.0.0"
