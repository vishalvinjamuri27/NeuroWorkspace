"""Identity verification and centralized role policies."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass

import jwt
from jwt import PyJWKClient

from neuro_workspace.domain import ROLE_CAPABILITIES, Capability, Role
from neuro_workspace.errors import ForbiddenError, NeuroError


@dataclass(frozen=True, slots=True)
class Identity:
    subject: str
    email: str
    display_name: str


def require_capability(role: Role, capability: Capability) -> None:
    if capability not in ROLE_CAPABILITIES[role]:
        raise ForbiddenError()


class SessionSigner:
    def __init__(self, secret: str, ttl_seconds: int) -> None:
        self._secret = secret.encode()
        self._ttl_seconds = ttl_seconds

    def dumps(self, identity: Identity) -> str:
        payload = {
            "sub": identity.subject,
            "email": identity.email,
            "name": identity.display_name,
            "iat": int(time.time()),
            "exp": int(time.time()) + self._ttl_seconds,
        }
        body = urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(b"=")
        signature = hmac.new(self._secret, body, hashlib.sha256).digest()
        return f"{body.decode()}.{urlsafe_b64encode(signature).rstrip(b'=').decode()}"

    def loads(self, token: str) -> Identity:
        try:
            body_text, signature_text = token.split(".", maxsplit=1)
            body = body_text.encode()
            signature = urlsafe_b64decode(signature_text + "=" * (-len(signature_text) % 4))
            expected = hmac.new(self._secret, body, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError("signature")
            payload = json.loads(urlsafe_b64decode(body_text + "=" * (-len(body_text) % 4)))
            if int(payload["exp"]) < int(time.time()):
                raise ValueError("expired")
            return Identity(str(payload["sub"]), str(payload["email"]), str(payload["name"]))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise NeuroError("invalid_session", "Sign in again to continue.", 401) from exc


class OIDCVerifier:
    """Minimal standards-based verifier; domain code remains provider-neutral."""

    def __init__(self, issuer: str, audience: str) -> None:
        self.issuer = issuer.rstrip("/")
        self.audience = audience
        self.client = PyJWKClient(f"{self.issuer}/.well-known/jwks.json")

    def verify(self, token: str) -> Identity:
        try:
            key = self.client.get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                key.key,
                algorithms=["RS256", "ES256"],
                audience=self.audience,
                issuer=self.issuer,
            )
            return Identity(
                subject=str(payload["sub"]),
                email=str(payload.get("email", "")),
                display_name=str(payload.get("name", payload.get("email", payload["sub"]))),
            )
        except Exception as exc:
            raise NeuroError("invalid_token", "Sign in again to continue.", 401) from exc
