from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol


class TelegramError(RuntimeError):
    def __init__(self, message: str, *, ambiguous_delivery: bool = False) -> None:
        super().__init__(message)
        self.ambiguous_delivery = ambiguous_delivery


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

    async def discover_destinations(self) -> list[dict[str, Any]]: ...

    async def send_proposal(
        self, *, chat_id: int, text: str, yes_callback: str, no_callback: str
    ) -> dict[str, Any]: ...

    async def send_observation_summary(self, *, chat_id: int, text: str) -> dict[str, Any]: ...

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
        def execute() -> dict[str, Any]:
            # OS credential backends may block; keep the entire I/O path off the loop.
            token = self._token()
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

        try:
            return await asyncio.wait_for(asyncio.to_thread(execute), timeout=25)
        except TimeoutError as exc:
            raise TelegramError(
                "O Telegram demorou para responder. A entrega ficou ambígua; "
                "não vou reenviar automaticamente.",
                ambiguous_delivery=True,
            ) from exc

    async def health_check(self) -> dict[str, Any]:
        result = await self._request("getMe", {})
        bot = result.get("result", {})
        return {"state": "READY", "bot_username": bot.get("username")}

    async def discover_destinations(self) -> list[dict[str, Any]]:
        # No offset: discovery must not acknowledge or consume approval callbacks.
        result = await self._request(
            "getUpdates", {"timeout": 0, "allowed_updates": ["message", "callback_query"]}
        )
        updates = result.get("result")
        if not isinstance(updates, list):
            raise TelegramError("Telegram returned an invalid updates payload.")
        destinations: dict[tuple[int, int], dict[str, Any]] = {}
        for update in updates:
            message = update.get("message", {}) if isinstance(update, dict) else {}
            if not isinstance(message, dict):
                continue
            chat, user = message.get("chat", {}), message.get("from", {})
            text = message.get("text", "")
            if not isinstance(chat, dict) or not isinstance(user, dict):
                continue
            chat_id, user_id = chat.get("id"), user.get("id")
            command = text.split()[0].split("@")[0] if isinstance(text, str) and text else ""
            if (command != "/start" or type(chat_id) is not int or chat_id == 0
                    or type(user_id) is not int or user_id <= 0 or user.get("is_bot")
                    or message.get("sender_chat")):
                continue
            destinations[(chat_id, user_id)] = {
                "chat_id": chat_id, "user_id": user_id,
                "chat_name": chat.get("title") or chat.get("first_name") or str(chat_id),
                "user_name": user.get("username") or user.get("first_name") or str(user_id),
            }
        return list(destinations.values())

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

    async def send_observation_summary(self, *, chat_id: int, text: str) -> dict[str, Any]:
        if not text.strip():
            raise TelegramError("O resumo informativo não pode estar vazio.")
        # Keep a conservative margin below Telegram's 4096-character limit.
        if len(text) > 3900:
            raise TelegramError("O resumo informativo excede o limite de uma mensagem.")
        result = await self._request("sendMessage", {"chat_id": chat_id, "text": text})
        message = result.get("result", {})
        return {"message_id": message.get("message_id"), "chat_id": chat_id}

    async def poll(self, *, offset: int | None = None) -> TelegramPollBatch:
        payload: dict[str, Any] = {"timeout": 0, "allowed_updates": ["message", "callback_query"]}
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
