from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol


class TelegramError(RuntimeError):
    pass


class CredentialStore(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...


class KeyringCredentialStore:
    def get_password(self, service: str, username: str) -> str | None:
        import keyring

        return keyring.get_password(service, username)


@dataclass(frozen=True, slots=True)
class TelegramCallback:
    update_id: int
    callback_query_id: str
    data: str
    chat_id: int
    user_id: int


@dataclass(frozen=True, slots=True)
class TelegramPollBatch:
    callbacks: tuple[TelegramCallback, ...]
    next_offset: int | None


class TelegramClientContract(Protocol):
    async def health_check(self) -> dict[str, Any]: ...

    async def send_proposal(
        self, *, chat_id: int, text: str, yes_callback: str, no_callback: str
    ) -> dict[str, Any]: ...

    async def poll(self, *, offset: int | None = None) -> TelegramPollBatch: ...

    async def answer_callback(self, callback_query_id: str, text: str) -> None: ...


class TelegramClient:
    def __init__(
        self,
        credential_store: CredentialStore | None = None,
        *,
        service: str = "Providency",
        username: str = "telegram-bot-token",
    ) -> None:
        self.credential_store = credential_store or KeyringCredentialStore()
        self.service = service
        self.username = username

    def _token(self) -> str:
        try:
            token = self.credential_store.get_password(self.service, self.username)
        except Exception as exc:
            raise TelegramError("Telegram credential store is unavailable.") from exc
        if not token:
            raise TelegramError("Telegram bot token is missing from the credential store.")
        return token

    async def _request(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        token = self._token()

        def execute() -> dict[str, Any]:
            request = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/{method}",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=15) as response:
                    result = json.loads(response.read().decode())
            except (OSError, ValueError, urllib.error.URLError) as exc:
                raise TelegramError(f"Telegram request {method} failed.") from exc
            if not isinstance(result, dict):
                raise TelegramError(f"Telegram returned an invalid response for {method}.")
            if not result.get("ok"):
                raise TelegramError(f"Telegram rejected request {method}.")
            return dict(result)

        return await asyncio.to_thread(execute)

    async def health_check(self) -> dict[str, Any]:
        result = await self._request("getMe", {})
        bot = result.get("result", {})
        return {"state": "READY", "bot_username": bot.get("username")}

    async def send_proposal(
        self, *, chat_id: int, text: str, yes_callback: str, no_callback: str
    ) -> dict[str, Any]:
        result = await self._request(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "reply_markup": {
                    "inline_keyboard": [[
                        {"text": "SIM", "callback_data": yes_callback},
                        {"text": "NÃO", "callback_data": no_callback},
                    ]]
                },
            },
        )
        message = result.get("result", {})
        return {"message_id": message.get("message_id"), "chat_id": chat_id}

    async def poll(self, *, offset: int | None = None) -> TelegramPollBatch:
        payload: dict[str, Any] = {"timeout": 0, "allowed_updates": ["callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        result = await self._request("getUpdates", payload)
        updates = result.get("result")
        if not isinstance(updates, list):
            raise TelegramError("Telegram returned an invalid updates payload.")
        callbacks: list[TelegramCallback] = []
        next_offset = offset
        for update in updates:
            if not isinstance(update, dict) or not isinstance(update.get("update_id"), int):
                raise TelegramError("Telegram returned an invalid update identifier.")
            update_id = int(update["update_id"])
            next_offset = max(next_offset or 0, update_id + 1)
            query = update.get("callback_query")
            message = query.get("message") if isinstance(query, dict) else None
            chat = message.get("chat") if isinstance(message, dict) else None
            user = query.get("from") if isinstance(query, dict) else None
            data = query.get("data") if isinstance(query, dict) else None
            if (
                not isinstance(chat, dict)
                or not isinstance(user, dict)
                or not isinstance(data, str)
                or not isinstance(query, dict)
            ):
                continue
            try:
                callbacks.append(
                    TelegramCallback(
                        update_id=update_id,
                        callback_query_id=str(query["id"]),
                        data=data,
                        chat_id=int(chat["id"]),
                        user_id=int(user["id"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return TelegramPollBatch(tuple(callbacks), next_offset)

    async def answer_callback(self, callback_query_id: str, text: str) -> None:
        await self._request(
            "answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text}
        )
