from __future__ import annotations

import abc
import hashlib
import hmac
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import httpx

from orderhub.config import Settings
from orderhub.models import NormalizedOrder, Shop


class ConnectorError(RuntimeError):
    pass


def hmac_sha256(secret: str, message: str) -> str:
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def ts_to_dt(ts: int | str | None) -> datetime:
    if ts in (None, "", 0):
        return datetime.now(timezone.utc)
    ts = int(ts)
    if ts > 10**12:
        ts //= 1000
    return datetime.fromtimestamp(ts, tz=timezone.utc)


class Connector(abc.ABC):
    """One instance per shop. Handles login (token refresh) and pulls orders for a time window."""

    def __init__(self, shop: Shop, settings: Settings, http: httpx.AsyncClient):
        self.shop, self.settings, self.http = shop, settings, http
        self.credentials_changed = False

    @property
    def creds(self) -> dict:
        return self.shop.credentials

    def token_expiring(self, margin_s: int = 600) -> bool:
        exp = self.creds.get("expires_at")
        return not self.creds.get("access_token") or (exp is not None and int(exp) - margin_s < datetime.now().timestamp())

    async def login(self) -> None:
        """Ensure a valid access token, refreshing it with the shop's refresh_token if needed."""
        if self.token_expiring():
            await self.refresh_token()
            self.credentials_changed = True

    @abc.abstractmethod
    async def refresh_token(self) -> None: ...

    @abc.abstractmethod
    def fetch_orders(self, since: datetime, until: datetime) -> AsyncIterator[NormalizedOrder]:
        """Yield every order created or updated in [since, until)."""
