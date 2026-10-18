from __future__ import annotations

import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken


class PiiCipher:
    """Field-level encryption for customer PII and shop credentials at rest."""

    def __init__(self, key: str):
        if not key:
            raise ValueError("PII_KEY must be set (generate with: python -m orderhub.crypto)")
        raw = key.encode()
        try:
            self._f = Fernet(raw)
        except ValueError:
            self._f = Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw).digest()))

    def encrypt(self, value: str | None) -> str | None:
        if value is None:
            return None
        return self._f.encrypt(value.encode()).decode()

    def decrypt(self, token: str | None) -> str | None:
        if token is None:
            return None
        try:
            return self._f.decrypt(token.encode()).decode()
        except InvalidToken:
            return None

    def encrypt_json(self, data: dict) -> str:
        return self.encrypt(json.dumps(data))  # type: ignore[return-value]

    def decrypt_json(self, token: str | None) -> dict:
        plain = self.decrypt(token)
        return json.loads(plain) if plain else {}


def mask_phone(phone: str | None) -> str | None:
    if not phone:
        return phone
    digits = phone.strip()
    if len(digits) <= 5:
        return "*" * len(digits)
    return digits[:3] + "*" * (len(digits) - 6) + digits[-3:]


def mask_name(name: str | None) -> str | None:
    if not name:
        return name
    parts = name.split()
    if len(parts) == 1:
        return parts[0][0] + "***"
    return " ".join([parts[0]] + [p[0] + "." for p in parts[1:-1]] + [parts[-1]])


if __name__ == "__main__":
    print(Fernet.generate_key().decode())
