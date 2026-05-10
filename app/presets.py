import asyncio
import json
import logging
import os
import secrets
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class PresetStore:
    """Lightweight JSON-backed store for presets.

    File schema:
        {"presets": [ { "id": str, "name": str, "actions": [...], ... } ]}

    All mutations go through an asyncio.Lock and are written atomically
    (tmp file + os.replace).
    """

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self._lock = asyncio.Lock()

    async def _ensure_dir(self) -> None:
        await asyncio.to_thread(self.path.parent.mkdir, parents=True, exist_ok=True)

    def _read_sync(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to load presets file %s: %s", self.path, exc)
            return []
        items = data.get("presets", []) if isinstance(data, dict) else []
        return items if isinstance(items, list) else []

    def _write_sync(self, presets: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix=".presets-", suffix=".json", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"presets": presets}, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    async def list(self) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(self._read_sync)

    async def get(self, preset_id: str) -> dict[str, Any] | None:
        for p in await self.list():
            if p.get("id") == preset_id:
                return p
        return None

    async def get_by_webhook(self, webhook_token: str) -> dict[str, Any] | None:
        if not webhook_token:
            return None
        for p in await self.list():
            if p.get("webhook_token") == webhook_token:
                return p
        return None

    async def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        new_id = secrets.token_urlsafe(8)
        webhook_token = secrets.token_urlsafe(24)
        now = int(time.time())
        record = {
            "id": new_id,
            "name": payload["name"],
            "description": payload.get("description"),
            "actions": payload.get("actions", []),
            "webhook_token": webhook_token,
            "created_at": now,
            "updated_at": now,
        }
        async with self._lock:
            current = await asyncio.to_thread(self._read_sync)
            current.append(record)
            await asyncio.to_thread(self._write_sync, current)
        return record

    async def update(self, preset_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        async with self._lock:
            current = await asyncio.to_thread(self._read_sync)
            for p in current:
                if p.get("id") == preset_id:
                    if "name" in payload and payload["name"] is not None:
                        p["name"] = payload["name"]
                    if "description" in payload:
                        p["description"] = payload["description"]
                    if "actions" in payload and payload["actions"] is not None:
                        p["actions"] = payload["actions"]
                    p["updated_at"] = int(time.time())
                    await asyncio.to_thread(self._write_sync, current)
                    return p
        return None

    async def delete(self, preset_id: str) -> bool:
        async with self._lock:
            current = await asyncio.to_thread(self._read_sync)
            new = [p for p in current if p.get("id") != preset_id]
            if len(new) == len(current):
                return False
            await asyncio.to_thread(self._write_sync, new)
            return True

    async def rotate_webhook(self, preset_id: str) -> dict[str, Any] | None:
        async with self._lock:
            current = await asyncio.to_thread(self._read_sync)
            for p in current:
                if p.get("id") == preset_id:
                    p["webhook_token"] = secrets.token_urlsafe(24)
                    p["updated_at"] = int(time.time())
                    await asyncio.to_thread(self._write_sync, current)
                    return p
        return None
