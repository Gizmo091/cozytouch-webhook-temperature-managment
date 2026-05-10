import asyncio
import logging
from typing import Awaitable, Callable, TypeVar

from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.enums import Server
from pyoverkiz.exceptions import (
    BadCredentialsException,
    NotAuthenticatedException,
    TooManyRequestsException,
)

from .config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _resolve_server():
    key = settings.cozytouch_server.strip().lower()
    try:
        enum_value = Server(key)
    except ValueError as exc:
        valid = ", ".join(s.value for s in Server)
        raise ValueError(
            f"Unknown COZYTOUCH_SERVER='{settings.cozytouch_server}'. "
            f"Valid values: {valid}"
        ) from exc
    return SUPPORTED_SERVERS[enum_value]


class CozytouchClient:
    """Singleton wrapper around pyoverkiz with lazy login and auto-reconnect."""

    def __init__(self) -> None:
        self._client: OverkizClient | None = None
        self._lock = asyncio.Lock()

    async def _build(self) -> OverkizClient:
        server = _resolve_server()
        client = OverkizClient(
            username=settings.cozytouch_username,
            password=settings.cozytouch_password,
            server=server,
            token=settings.cozytouch_token or None,
        )
        await client.login()
        logger.info("Logged in to Overkiz server '%s'", server.name)
        return client

    async def get(self) -> OverkizClient:
        if self._client is not None:
            return self._client
        async with self._lock:
            if self._client is None:
                self._client = await self._build()
        return self._client

    async def reconnect(self) -> OverkizClient:
        async with self._lock:
            if self._client is not None:
                try:
                    await self._client.close()
                except Exception:  # noqa: BLE001
                    logger.debug("Error closing stale client", exc_info=True)
                self._client = None
            self._client = await self._build()
        return self._client

    async def close(self) -> None:
        async with self._lock:
            if self._client is not None:
                try:
                    await self._client.close()
                finally:
                    self._client = None

    async def call(self, fn: Callable[[OverkizClient], Awaitable[T]]) -> T:
        """Run an async function with the client, retrying once on auth loss."""
        client = await self.get()
        try:
            return await fn(client)
        except NotAuthenticatedException:
            logger.info("Session expired, reconnecting…")
            client = await self.reconnect()
            return await fn(client)


cozytouch = CozytouchClient()


__all__ = [
    "cozytouch",
    "CozytouchClient",
    "BadCredentialsException",
    "NotAuthenticatedException",
    "TooManyRequestsException",
]
