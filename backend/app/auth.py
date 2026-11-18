from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from hashlib import sha256
from os import getenv
from secrets import compare_digest
from typing import Callable


AUTH_CONFIG_ENV = "BACKEND_API_TOKEN_CONFIG"
WEBSOCKET_BEARER_PREFIX = "bearer."


@dataclass(frozen=True)
class AuthPrincipal:
    subject: str
    role: str


@dataclass(frozen=True)
class _TokenRecord:
    token_sha256: str
    principal: AuthPrincipal


class TokenAuthenticator:
    """Validates pre-issued API tokens without retaining their plaintext."""

    def __init__(self, records: tuple[_TokenRecord, ...] = ()) -> None:
        self._records = records

    @property
    def enabled(self) -> bool:
        return bool(self._records)

    @classmethod
    def disabled(cls) -> TokenAuthenticator:
        return cls()

    @classmethod
    def from_environment(
        cls,
        getenv_fn: Callable[[str], str | None] = getenv,
    ) -> TokenAuthenticator:
        raw_config = getenv_fn(AUTH_CONFIG_ENV)
        if raw_config is None or not raw_config.strip():
            return cls.disabled()
        return cls.from_json(raw_config)

    @classmethod
    def from_json(cls, raw_config: str) -> TokenAuthenticator:
        try:
            parsed = json.loads(raw_config)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{AUTH_CONFIG_ENV}는 유효한 JSON이어야 합니다.") from exc
        if not isinstance(parsed, list) or not parsed:
            raise ValueError(f"{AUTH_CONFIG_ENV}는 하나 이상의 토큰 설정 배열이어야 합니다.")

        records: list[_TokenRecord] = []
        seen_digests: set[str] = set()
        for index, item in enumerate(parsed):
            if not isinstance(item, dict):
                raise ValueError(f"{AUTH_CONFIG_ENV}[{index}]는 JSON 객체여야 합니다.")
            unknown_fields = set(item) - {"token_sha256", "subject", "role"}
            if unknown_fields:
                raise ValueError(
                    f"{AUTH_CONFIG_ENV}[{index}]에 알 수 없는 필드가 있습니다: "
                    + ", ".join(sorted(unknown_fields))
                )
            digest = item.get("token_sha256")
            subject = item.get("subject")
            role = item.get("role")
            if not isinstance(digest, str) or not _is_sha256_hex(digest):
                raise ValueError(
                    f"{AUTH_CONFIG_ENV}[{index}].token_sha256는 64자리 SHA-256 hex여야 합니다."
                )
            digest = digest.lower()
            if digest in seen_digests:
                raise ValueError(f"{AUTH_CONFIG_ENV}에 중복된 token_sha256가 있습니다.")
            subject = _validate_identity_field(subject, "subject", index)
            role = _validate_identity_field(role, "role", index)
            seen_digests.add(digest)
            records.append(
                _TokenRecord(
                    token_sha256=digest,
                    principal=AuthPrincipal(subject=subject, role=role),
                )
            )
        return cls(tuple(records))

    def authenticate_token(self, token: str | None) -> AuthPrincipal | None:
        if token is None or not token:
            return None
        digest = token_sha256(token)
        matched: AuthPrincipal | None = None
        for record in self._records:
            if compare_digest(digest, record.token_sha256):
                matched = record.principal
        return matched

    def authenticate_authorization(
        self,
        authorization: str | None,
    ) -> AuthPrincipal | None:
        return self.authenticate_token(bearer_token_from_authorization(authorization))


def token_sha256(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def bearer_token_from_authorization(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer":
        return None
    token = token.strip()
    if not token or " " in token:
        return None
    return token


def encode_websocket_bearer_protocol(token: str) -> str:
    encoded = base64.urlsafe_b64encode(token.encode("utf-8")).decode("ascii")
    return WEBSOCKET_BEARER_PREFIX + encoded.rstrip("=")


def websocket_bearer_token(
    authorization: str | None,
    subprotocols: str | None,
) -> str | None:
    header_token = bearer_token_from_authorization(authorization)
    if header_token is not None:
        return header_token
    for protocol in (subprotocols or "").split(","):
        protocol = protocol.strip()
        if not protocol.startswith(WEBSOCKET_BEARER_PREFIX):
            continue
        encoded = protocol.removeprefix(WEBSOCKET_BEARER_PREFIX)
        if not encoded:
            return None
        try:
            padding = "=" * (-len(encoded) % 4)
            token = base64.b64decode(
                encoded + padding,
                altchars=b"-_",
                validate=True,
            ).decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            return None
        return token or None
    return None


def _is_sha256_hex(value: str) -> bool:
    if len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _validate_identity_field(value: object, field: str, index: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{AUTH_CONFIG_ENV}[{index}].{field}는 비어 있지 않은 문자열이어야 합니다.")
    value = value.strip()
    if len(value) > 128:
        raise ValueError(f"{AUTH_CONFIG_ENV}[{index}].{field}는 128자 이하여야 합니다.")
    return value
