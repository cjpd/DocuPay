"""Telegram alerts through a BotFather bot. Only the owner's chat is read or written."""
import os
import re
from typing import Optional

import httpx


class Telegram:
    def __init__(self, token: Optional[str] = None, chat_id: Optional[str] = None):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = str(chat_id or os.environ.get("TELEGRAM_CHAT_ID") or "")
        self.offset = 0
        self.enabled = bool(self.token and self.chat_id)

    def _url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.token}/{method}"

    def send(self, text: str) -> None:
        if not self.enabled:
            return
        try:
            httpx.post(self._url("sendMessage"), json={"chat_id": self.chat_id, "text": text[:4000],
                                                     "disable_web_page_preview": True}, timeout=10)
        except httpx.HTTPError:
            pass  # an alert failure must never stop the graph; it is still in the event log

    def commands(self) -> list[tuple[str, int, float]]:
        """Return (verb, gate id, sent_at) for /approve N and /reject N from the owner chat; plus ('ping',0,t)."""
        if not self.enabled:
            return []
        try:
            r = httpx.get(self._url("getUpdates"), params={"offset": self.offset, "timeout": 0}, timeout=15).json()
        except httpx.HTTPError:
            return []
        out = []
        for u in r.get("result", []):
            self.offset = max(self.offset, u["update_id"] + 1)
            msg = u.get("message") or {}
            if str((msg.get("chat") or {}).get("id")) != self.chat_id:
                continue  # anyone else talking to the bot is ignored
            text, at = (msg.get("text") or "").strip(), float(msg.get("date") or 0)
            if m := re.fullmatch(r"/(approve|reject)\s+(\d+)", text):
                out.append((m.group(1), int(m.group(2)), at))
            else:
                out.append(("ping", 0, at))
        return out


class Null:
    enabled = False

    def __init__(self):
        self.sent: list[str] = []

    def send(self, text):
        self.sent.append(text)

    def commands(self):
        return []
