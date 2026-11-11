from __future__ import annotations

import json
import os
import time

import requests

from .util import log

LIMIT = 4000


class TelegramError(Exception):
    pass


class Telegram:
    def __init__(self, token: str, chat_id: str, dry_run: bool = False):
        self.token, self.chat_id = token, chat_id
        self.dry = dry_run or not (token and chat_id)
        if self.dry:
            log("Telegram: DRY-RUN mode (messages printed only)")

    # --- low level (token URL එක error messages වල print වෙන්නේ නැහැ)
    def _call(self, method: str, data: dict, files: dict | None = None) -> dict:
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        for attempt in range(3):
            try:
                r = requests.post(url, data=data, files=files, timeout=60)
            except requests.RequestException as e:
                raise TelegramError(f"{method}: {type(e).__name__}") from None
            if r.status_code == 429 and attempt < 2:
                wait = 5
                try:
                    wait = int(r.json().get("parameters", {}).get("retry_after", 5))
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(min(wait + 1, 30))
                continue
            try:
                j = r.json()
            except ValueError:
                raise TelegramError(f"{method}: HTTP {r.status_code} non-JSON") from None
            if not j.get("ok"):
                raise TelegramError(f"{method}: {j.get('description')}")
            return j
        raise TelegramError(f"{method}: retries exhausted")

    # --- public
    def send(self, text: str, silent: bool = False) -> None:
        chunks = [text[i:i + LIMIT] for i in range(0, len(text), LIMIT)] or [""]
        for ch in chunks:
            if self.dry:
                print("\n[TG TEXT]\n" + ch + "\n")
                continue
            self._call("sendMessage", {
                "chat_id": self.chat_id, "text": ch, "parse_mode": "HTML",
                "disable_web_page_preview": "true", "disable_notification": "true" if silent else "false",
            })

    def send_photo(self, path: str, caption: str = "") -> None:
        if self.dry:
            print(f"\n[TG PHOTO] {path} | {caption}\n")
            return
        with open(path, "rb") as f:
            self._call("sendPhoto", {"chat_id": self.chat_id, "caption": caption[:1000], "parse_mode": "HTML"},
                       files={"photo": f})

    def send_album(self, paths: list[str], captions: list[str] | None = None, silent: bool = False) -> None:
        if self.dry:
            print(f"\n[TG ALBUM] {[os.path.basename(p) for p in paths]}\n")
            return
        media, files, handles = [], {}, []
        try:
            for i, p in enumerate(paths):
                key = f"f{i}"
                h = open(p, "rb")
                handles.append(h)
                files[key] = h
                item = {"type": "photo", "media": f"attach://{key}"}
                if captions and captions[i]:
                    item["caption"] = captions[i][:1000]
                    item["parse_mode"] = "HTML"
                media.append(item)
            self._call("sendMediaGroup", {
                "chat_id": self.chat_id, "media": json.dumps(media),
                "disable_notification": "true" if silent else "false",
            }, files=files)
        finally:
            for h in handles:
                h.close()
