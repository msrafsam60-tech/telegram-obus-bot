#!/usr/bin/env python3
"""╔══════════════════════════════════════════════════════════════════════╗
║  🎯 CallBot — এক ফাইলে পুরো কোড                                   ║
║                                                                      ║
║  একটা Telegram বট + ইউজারবট জোড়া, এক প্রসেসে:                      ║
║    • Bot (aiogram)    → চ্যানেলে তোমার অ্যাকাউন্ট add করে,            ║
║                         watch list manage করে, status দেখায়           ║
║    • Userbot (Telethon + pytgcalls) → চ্যানেলে কল মেসেজ detect করে     ║
║                         সাথে সাথে online → IP/port বের করে →           ║
║                         join → vote → leave                           ║
║                                                                      ║
║  চালানো:                                                            ║
║      pip install -r requirements.txt                                  ║
║      cp .env.example .env   (সেটিংস পূরণ করো)                        ║
║      python main.py                                                  ║
║                                                                      ║
║  টেস্ট (টেলিগ্রাম ছাড়াই):                                            ║
║      python main.py --selftest                                       ║
╚══════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import asyncio
import html
import inspect
import json
import logging
import os
import re
import socket
import sys
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════════════
#  ১) কনফিগ — সব সেটিংস .env ফাইল থেকে
# ═══════════════════════════════════════════════════════════════════════════

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

try:  # python-dotenv না থাকলেও env var দিয়ে চলবে
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass


def _bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on", "ok"}


def _int(key: str, default: int = 0) -> int:
    try:
        return int(str(os.getenv(key, "")).strip())
    except (TypeError, ValueError):
        return default


def _list(key: str) -> list[str]:
    raw = os.getenv(key, "") or ""
    return [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]


# ── Telegram credentials ────────────────────────────────────────────────────
API_ID = _int("API_ID")
API_HASH = os.getenv("API_HASH", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
SESSION_NAME = os.getenv("SESSION_NAME", "callbot").strip()

# অটো লগইনের জন্য (খালি রাখলে ইন্টার‌্যাক্টিভ প্রম্পট আসবে)
PHONE = os.getenv("PHONE", "").strip()
PASSWORD = os.getenv("PASSWORD", "").strip()

# শুধু এই ইউজাররা বটের কমান্ড দিতে পারবে (খালি = সবাই পারবে, ঝুঁকিপূর্ণ)
ADMIN_IDS = [int(x) for x in _list("ADMIN_IDS") if x.isdigit()]

# ── Monitoring ──────────────────────────────────────────────────────────────
WATCH_CHANNELS = _list("WATCH_CHANNELS")            # @mychannel,-1001234567890
LOG_CHANNEL = os.getenv("LOG_CHANNEL", "").strip()  # খালি রাখলে লগ যাবে না
DEDUPE_TTL = _int("DEDUPE_TTL", 300)                # একই মেসেজ দুইবার হবে না (সেকেন্ড)
COOLDOWN = _int("COOLDOWN", 3)                      # দুই কলের মধ্যে সর্বনিম্ন বিরতি

# ── Telegram voice chat (কল) ───────────────────────────────────────────────
VC_ENABLED = _bool("VC_ENABLED", True)
VC_CHAT_ID = _int("VC_CHAT_ID", 0)                 # 0 হলে মেসেজের লিংক থেকে নেবে
VC_MEDIA_URL = os.getenv("VC_MEDIA_URL", "").strip()  # খালি = নিজে নীরব wav বানাবে
DWELL_SECONDS = _int("DWELL_SECONDS", 20)          # কলে কত সেকেন্ড থাকবে

# ── Vote ────────────────────────────────────────────────────────────────────
VOTE_ENABLED = _bool("VOTE_ENABLED", True)
VOTE_OPTION_KEYWORD = os.getenv("VOTE_OPTION_KEYWORD", "").strip()   # যেমন: হ্যাঁ / Yes / 1
VOTE_BUTTON_KEYWORD = os.getenv("VOTE_BUTTON_KEYWORD", "").strip()  # ইনলাইন বাটনে কী খুঁজবে
VOTE_METHOD = os.getenv("VOTE_METHOD", "POST").strip().upper()
VOTE_URL_TEMPLATE = os.getenv("VOTE_URL_TEMPLATE", "").strip()       # {ip} ও {port} বসবে
VOTE_BODY_TEMPLATE = os.getenv("VOTE_BODY_TEMPLATE", "").strip()
VOTE_HEADERS_JSON = os.getenv("VOTE_HEADERS_JSON", "").strip()
HTTP_TIMEOUT = _int("HTTP_TIMEOUT", 10)

# ── IP:port "come online" probe ────────────────────────────────────────────
KNOCK_ENABLED = _bool("KNOCK_ENABLED", False)
KNOCK_PROTO = os.getenv("KNOCK_PROTO", "udp").strip().lower()   # udp | tcp
KNOCK_PAYLOAD_HEX = os.getenv("KNOCK_PAYLOAD_HEX", "").strip()  # খালি হলে টেক্সট "ping"
KNOCK_TIMEOUT = _int("KNOCK_TIMEOUT", 5)

# ── Files ───────────────────────────────────────────────────────────────────
SESSION_FILE = DATA_DIR / f"{SESSION_NAME}.session"
STATE_FILE = Path(os.getenv("STATE_FILE", str(DATA_DIR / "state.json")))
LOG_FILE = Path(os.getenv("LOG_FILE", str(DATA_DIR / "callbot.log")))


def is_admin(user_id: int) -> bool:
    """ADMIN_IDS খালি রাখলে সবাই admin (শুধু ডেভ টেস্টিং এর জন্য)।"""
    if not ADMIN_IDS:
        return True
    return user_id in ADMIN_IDS


# ═══════════════════════════════════════════════════════════════════════════
#  ২) State store — watch list, টগল আর শেষ রান রাখার JSON ফাইল
# ═══════════════════════════════════════════════════════════════════════════

def normalize(entity) -> int:
    """Entity থেকে canonical chat id (channel হলে -100xxxx) বের করে।"""
    if isinstance(entity, int):
        return entity
    try:
        from telethon import utils

        return utils.get_peer_id(entity)
    except Exception:
        return int(entity)


class StateStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = asyncio.Lock()
        self.data = {
            "enabled": True,       # পুরো pipeline চালু/বন্ধ
            "vc_enabled": True,    # voice chat join/leave চালু/বন্ধ
            "vote_enabled": True,  # vote চালু/বন্ধ
            "watched": {},         # {chat_id: {"title":..,"username":..}}
            "processed": {},       # {msg_key: timestamp} — ডুপ্লিকেট আটকাতে
            "last_run": None,
            "last_error": None,
        }

    # ── persistence ─────────────────────────────────────────────────────────
    async def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.data.update(raw)
        except Exception:
            pass  # corrupt file হলে চুপচাপ default ব্যবহার

    async def save(self) -> None:
        async with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            tmp.replace(self.path)

    # ── watch list ──────────────────────────────────────────────────────────
    def watched_ids(self) -> set[int]:
        return {int(k) for k in self.data.get("watched", {})}

    def is_watched(self, chat_id: int) -> bool:
        return int(chat_id) in self.watched_ids()

    def add_watched(self, chat_id: int, meta: dict | None = None) -> None:
        self.data.setdefault("watched", {})[str(int(chat_id))] = meta or {}

    def remove_watched(self, chat_id: int) -> bool:
        return self.data.setdefault("watched", {}).pop(str(int(chat_id)), None) is not None

    # ── dedupe ──────────────────────────────────────────────────────────────
    def already_processed(self, key: str, ttl: int) -> bool:
        processed = self.data.setdefault("processed", {})
        now = time.time()
        for k in [k for k, ts in processed.items() if now - float(ts) > ttl]:
            processed.pop(k, None)
        if key in processed:
            return True
        processed[key] = now
        return False


# ═══════════════════════════════════════════════════════════════════════════
#  ৩) Extract — মেসেজ থেকে IP / port / link / poll বের করা
# ═══════════════════════════════════════════════════════════════════════════

IPV4 = r"(?:(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)"

RE_IP_PORT = re.compile(rf"({IPV4})\s*[:\-/,|\s]\s*(\d{{1,5}})")
RE_IP_ONLY = re.compile(rf"\b({IPV4})\b")
RE_PORT_ONLY = re.compile(r"(?:port|পোর্ট)\s*[:=]?\s*(\d{1,5})", re.IGNORECASE)
RE_IP_LABEL = re.compile(
    r"(?:ip|আইপি|server|সার্ভার|host|ঠিকানা)\s*[:=]?\s*(" + IPV4 + r")",
    re.IGNORECASE,
)
RE_LINK = re.compile(
    r"(?:https?://t\.me/(?:joinchat/|\+|s/)?[A-Za-z0-9_\-]+"
    r"|tg://join\?invite=[A-Za-z0-9_\-]+"
    r"|https?://t\.me/c/\d+/\d+)"
)
RE_PHONE = re.compile(r"(?<!\d)(?:\+?88)?01[3-9]\d{8}(?!\d)")

CALL_KEYWORDS = (
    "call", "কল", "voice", "ভয়েস", "voice chat", "live", "লাইভ", "room",
    "join", "যোগ", "started", "শুরু", "going live", "ওপেন", "open", "invite",
)


def _valid_port(p: str) -> int | None:
    try:
        port = int(p)
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


def extract_ip_port(text: str) -> tuple[str, int] | None:
    """টেক্সট থেকে (ip, port) বের করে — পাওয়া না গেলে None।"""
    if not text:
        return None
    clean = RE_PHONE.sub(" ", text)

    # ১) সরাসরি ip:port
    for ip, port in RE_IP_PORT.findall(clean):
        valid = _valid_port(port)
        if valid:
            return ip, valid

    # ২) আলাদা করে IP: লেবেল আছে আর Port: লেবেল আছে
    ip_only = RE_IP_LABEL.search(clean) or RE_IP_ONLY.search(clean)
    port_only = RE_PORT_ONLY.search(clean)
    if ip_only and port_only:
        valid = _valid_port(port_only.group(1))
        if valid:
            return ip_only.group(1), valid

    return None  # ৩) শুধু IP আছে, port নেই → বের করা যাবে না


def extract_links(text: str) -> list[str]:
    """মেসেজে থাকা t.me invite / join লিংকগুলো।"""
    if not text:
        return []
    found, seen = [], set()
    for link in RE_LINK.findall(text):
        if link not in seen:
            seen.add(link)
            found.append(link)
    return found


def invite_hash(link: str) -> str | None:
    """t.me/+abc বা t.me/joinchat/abc লিংক থেকে hash বের করে।"""
    link = (link or "").strip()
    if link.startswith("tg://join?invite="):
        return link.split("invite=", 1)[1] or None
    if "/joinchat/" in link:
        return link.rsplit("/", 1)[-1].split("?")[0] or None
    if "t.me/+" in link:
        return link.split("t.me/+", 1)[1].split("?")[0] or None
    return None


def looks_like_call(text: str, has_poll: bool = False, has_ip_port: bool = False) -> bool:
    """মেসেজটা "কল" নোটিফিকেশন কিনা সেটা আন্দাজ।"""
    if has_ip_port:
        return True
    if has_poll:
        return True
    low = (text or "").lower()
    return any(k in low for k in CALL_KEYWORDS)


# ═══════════════════════════════════════════════════════════════════════════
#  ৪) Actions — কল পাইপলাইনের সব অ্যাকশন
#       1. online()   → অ্যাকাউন্ট সাথে সাথে অনলাইন
#       2. knock()    → বের করা IP:port এ কানেকশন
#       3. join_vc()  → voice chat-এ join
#       4. vote()     → পোল/বাটন/HTTP ভোট
#       5. leave_vc() → কল থেকে বের হয়ে আসা
# ═══════════════════════════════════════════════════════════════════════════

log = logging.getLogger("callbot")


@dataclass
class CallContext:
    """একটা কল মেসেজ থেকে বের করা সব তথ্য।"""

    message: object
    chat_id: int
    text: str = ""
    ip: str | None = None
    port: int | None = None
    poll: object | None = None
    links: list[str] = field(default_factory=list)

    @classmethod
    def from_message(cls, message, chat_id: int) -> "CallContext":
        text = (getattr(message, "message", "") or "")
        ip_port = extract_ip_port(text)
        media = getattr(message, "media", None)
        poll = None
        # MessageMediaPoll → poll.answers থেকে অপশন বের করা যায়
        if media is not None and hasattr(media, "poll") and hasattr(media, "results"):
            poll = media.poll
        return cls(
            message=message,
            chat_id=chat_id,
            text=text,
            ip=ip_port[0] if ip_port else None,
            port=ip_port[1] if ip_port else None,
            poll=poll,
            links=extract_links(text),
        )


def render(template: str, ip: str | None, port: int | None) -> str:
    """'{ip}' / '{port}' প্লেসহোল্ডার বসায় (JSON ব্র্যাকেট নষ্ট হয় না)।"""
    return (template or "").replace("{ip}", str(ip or "")).replace("{port}", str(port or ""))


def make_silent_wav(path: Path, seconds: int = 2, rate: int = 8000) -> Path:
    """voice chat join করার জন্য নীরব অডিও ফাইল তৈরি করে।"""
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * rate * seconds)
    return path


class ActionRunner:
    def __init__(self, client, calls, state):
        self.client = client
        self.calls = calls
        self.state = state
        self._silent_wav: Path | None = None
        self._in_vc: dict[int, bool] = {}

    # ── হেল্পার ─────────────────────────────────────────────────────────────
    def _media_stream(self):
        """voice chat-এ stream করার জন্য মিডিয়া অবজেক্ট তৈরি করে।"""
        try:
            from pytgcalls.types import MediaStream
        except ImportError:
            try:
                from pytgcalls.types.stream import MediaStream
            except ImportError:
                return None
        if VC_MEDIA_URL:
            return MediaStream(VC_MEDIA_URL)
        if self._silent_wav is None:
            self._silent_wav = make_silent_wav(DATA_DIR / "silent.wav")
        return MediaStream(str(self._silent_wav))

    async def _online(self) -> dict:
        """অ্যাকাউন্টকে 'কোলে' (online) আনে।"""
        result = {"ok": False, "detail": ""}
        try:
            from telethon.tl import functions

            await self.client(functions.account.UpdateStatusRequest(offline=False))
            result = {"ok": True, "detail": "status=online"}
        except Exception as exc:  # স্ট্যাটাস না গেলেও বাকি কাজ চলবে
            result = {"ok": False, "detail": f"status error: {exc}"}
        try:
            me = await self.client.get_me()
            result["detail"] += f" | @{me.username or me.id}"
        except Exception:
            pass
        return result

    # ── ১. IP:port এ কানেকশন (come online on the server) ────────────────────
    async def knock(self, ctx: CallContext) -> dict:
        if not (KNOCK_ENABLED and ctx.ip and ctx.port):
            return {"skipped": True, "detail": "knock disabled"}

        payload = b"ping"
        if KNOCK_PAYLOAD_HEX:
            try:
                payload = bytes.fromhex(KNOCK_PAYLOAD_HEX.replace(" ", ""))
            except ValueError:
                payload = KNOCK_PAYLOAD_HEX.encode()

        def _probe():
            try:
                if KNOCK_PROTO == "tcp":
                    with socket.create_connection(
                        (ctx.ip, ctx.port), timeout=KNOCK_TIMEOUT
                    ) as sock:
                        sock.sendall(payload)
                        try:
                            return sock.recv(2048)
                        except socket.timeout:
                            return b""
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.settimeout(KNOCK_TIMEOUT)
                    sock.sendto(payload, (ctx.ip, ctx.port))
                    try:
                        data, _ = sock.recvfrom(2048)
                        return data
                    except socket.timeout:
                        return b""
            except OSError as exc:
                return f"error: {exc}".encode()

        data = await asyncio.to_thread(_probe)
        if isinstance(data, bytes) and data.startswith(b"error:"):
            return {"ok": False, "detail": data.decode(errors="ignore")}
        return {"ok": True, "detail": f"{KNOCK_PROTO} {ctx.ip}:{ctx.port} -> {len(data)} bytes"}

    # ── ২. Telegram voice chat join ──────────────────────────────────────────
    async def _resolve_vc_chat(self, ctx: CallContext) -> int | None:
        """মেসেজের লিংক থেকে (বা কনফিগ থেকে) voice chat চ্যাটের আইডি বের করে।"""
        if VC_CHAT_ID:
            return int(VC_CHAT_ID)

        from telethon.tl.functions.messages import ImportChatInviteRequest

        for link in ctx.links:
            h = invite_hash(link)
            if not h:
                continue
            try:
                result = await self.client(ImportChatInviteRequest(h))
                chats = getattr(result, "chats", None)
                if chats:
                    return normalize(chats[0])
            except Exception as exc:
                log.warning("invite join fail (%s): %s", link, exc)
        return None

    async def join_vc(self, ctx: CallContext) -> dict:
        if self.calls is None:
            return {"skipped": True, "detail": "pytgcalls not installed"}
        chat_id = await self._resolve_vc_chat(ctx)
        if not chat_id:
            return {"skipped": True, "detail": "vc chat not found (VC_CHAT_ID বা invite লিংক দরকার)"}

        media = self._media_stream()
        try:
            if hasattr(self.calls, "play"):
                await self.calls.play(chat_id, media)
            else:
                await self.calls.join_group_call(chat_id, media)
            self._in_vc[chat_id] = True
            return {"ok": True, "detail": f"joined vc {chat_id}", "chat_id": chat_id}
        except TypeError:
            # অন্য সাইনেচার (যেমন join_group_call(chat_id, media, ...))
            try:
                await self.calls.join_group_call(chat_id, media)
                self._in_vc[chat_id] = True
                return {"ok": True, "detail": f"joined vc {chat_id}", "chat_id": chat_id}
            except Exception as exc:
                return {"ok": False, "detail": f"join failed: {exc}"}
        except Exception as exc:
            return {"ok": False, "detail": f"join failed: {exc}"}

    async def leave_vc(self, chat_id: int | None = None) -> dict:
        """voice chat থেকে বের হয়ে আসে।"""
        if self.calls is None:
            return {"skipped": True, "detail": "pytgcalls not installed"}
        targets = [chat_id] if chat_id else list(self._in_vc.keys())
        if not targets:
            return {"skipped": True, "detail": "no active vc"}

        report = []
        for cid in targets:
            done = False
            for name in ("leave", "leave_group_call"):
                fn = getattr(self.calls, name, None)
                if fn is None:
                    continue
                try:
                    await fn(cid)
                    report.append(f"left {cid}")
                    done = True
                    break
                except TypeError:
                    continue
                except Exception as exc:
                    report.append(f"leave {cid} error: {exc}")
            if not done and not any(cid in r for r in report):
                report.append(f"leave {cid} not supported")
            self._in_vc.pop(cid, None)
        return {"ok": True, "detail": "; ".join(report)}

    # ── ৩. Vote ──────────────────────────────────────────────────────────────
    def _match_option(self, answers) -> int | None:
        """কনফিগের কীওয়ার্ড দিয়ে পোল অপশন ম্যাচ করে।"""
        kw = VOTE_OPTION_KEYWORD.lower().strip()
        if not kw:
            return 0  # কীওয়ার্ড না থাকলে প্রথম অপশন
        for idx, answer in enumerate(answers):
            if kw in (answer.text or "").lower():
                return idx
        for idx, answer in enumerate(answers):
            if (answer.text or "").strip().lower() == kw:
                return idx
        return None

    async def vote(self, ctx: CallContext) -> dict:
        if not VOTE_ENABLED:
            return {"skipped": True, "detail": "vote disabled"}

        # (ক) পোল থাকলে পোলে ভোট
        if ctx.poll is not None:
            answers = list(getattr(ctx.poll, "answers", []) or [])
            if answers:
                idx = self._match_option(answers)
                if idx is None:
                    return {"ok": False, "detail": "poll option match হয়নি"}
                answer = answers[idx]
                try:
                    from telethon.tl.functions.messages import SendVoteRequest

                    peer = await self.client.get_input_peer(ctx.chat_id)
                    await self.client(
                        SendVoteRequest(
                            peer=peer,
                            msg_id=ctx.message.id,
                            options=[answer.option],
                        )
                    )
                    return {"ok": True, "detail": f"poll vote → '{answer.text}'"}
                except Exception as exc:
                    # ফলব্যাক: Telethon-এর click হেল্পার
                    try:
                        await ctx.message.click(text=answer.text)
                        return {"ok": True, "detail": f"poll vote (click) → '{answer.text}'"}
                    except Exception:
                        return {"ok": False, "detail": f"vote failed: {exc}"}

        # (খ) ইনলাইন বাটনে ভোট
        kw = VOTE_BUTTON_KEYWORD.lower().strip()
        buttons = getattr(ctx.message, "buttons", None) or []
        if kw and buttons:
            for r, row in enumerate(buttons):
                for c, btn in enumerate(row):
                    if kw in (getattr(btn, "text", "") or "").lower():
                        try:
                            await ctx.message.click(i=r, j=c)
                            return {"ok": True, "detail": f"button clicked → {btn.text}"}
                        except Exception as exc:
                            return {"ok": False, "detail": f"button click failed: {exc}"}

        # (গ) বাইরের ভোট API (http)
        if VOTE_URL_TEMPLATE:
            return await self.http_vote(ctx)

        return {"skipped": True, "detail": "কোনো পোল/বাটন/vote-url পাওয়া যায়নি"}

    async def http_vote(self, ctx: CallContext) -> dict:
        """বাইরের সার্ভারে HTTP রিকোয়েস্ট পাঠায় (URL-এ {ip}/{port} বসে)।"""
        import httpx

        url = render(VOTE_URL_TEMPLATE, ctx.ip, ctx.port)
        if not url:
            return {"ok": False, "detail": "empty url"}
        headers = {}
        if VOTE_HEADERS_JSON:
            try:
                headers = json.loads(VOTE_HEADERS_JSON)
            except json.JSONDecodeError as exc:
                return {"ok": False, "detail": f"bad headers json: {exc}"}
        kwargs = {"headers": headers}
        if VOTE_BODY_TEMPLATE:
            try:
                kwargs["json"] = json.loads(render(VOTE_BODY_TEMPLATE, ctx.ip, ctx.port))
            except json.JSONDecodeError as exc:
                return {"ok": False, "detail": f"bad body template: {exc}"}
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as http:
                resp = await http.request(VOTE_METHOD, url, **kwargs)
            return {"ok": resp.is_success, "detail": f"HTTP {resp.status_code} {url}"}
        except Exception as exc:
            return {"ok": False, "detail": f"http error: {exc}"}

    # ── পুরো পাইপলাইন ────────────────────────────────────────────────────────
    async def run(self, ctx: CallContext) -> dict:
        """পুরো 'কল → vote → leave' সিকোয়েন্স চালায়।"""
        report: dict = {"ip": ctx.ip, "port": ctx.port}
        report["online"] = await self._online()

        if KNOCK_ENABLED:
            report["knock"] = await self.knock(ctx)

        vc_chat_id = None
        if VC_ENABLED and self.state.data.get("vc_enabled", True):
            joined = await self.join_vc(ctx)
            report["call"] = joined
            if joined.get("ok"):
                vc_chat_id = joined.get("chat_id") or next(iter(self._in_vc), None)

        if self.state.data.get("vote_enabled", True):
            report["vote"] = await self.vote(ctx)

        if vc_chat_id or self._in_vc:
            try:
                await asyncio.sleep(DWELL_SECONDS)
            except asyncio.CancelledError:
                pass
            report["leave"] = await self.leave_vc(vc_chat_id)

        self.state.data["last_run"] = {
            "ip": ctx.ip,
            "port": ctx.port,
            "report": {
                k: (v.get("detail") if isinstance(v, dict) else v)
                for k, v in report.items()
            },
        }
        await self.state.save()
        return report


# ═══════════════════════════════════════════════════════════════════════════
#  ৫) Watcher — চ্যানেলের নতুন মেসেজ monitor করে পাইপলাইন চালায়
# ═══════════════════════════════════════════════════════════════════════════

class CallWatcher:
    def __init__(self, client, state: StateStore, runner: ActionRunner):
        self.client = client
        self.state = state
        self.runner = runner
        self._last_call_ts = 0.0

    async def start(self) -> None:
        from telethon import events

        await self._load_configured_channels()
        self.client.add_event_handler(self._on_message, events.NewMessage())
        self._heartbeat = asyncio.create_task(self._heartbeat_loop())
        log.info("watcher ready — %d channel(s) monitored", len(self.state.watched_ids()))

    # ── .env এ থাকা চ্যানেলগুলো watch list-এ যোগ ───────────────────────────
    async def _load_configured_channels(self) -> None:
        for ref in WATCH_CHANNELS:
            try:
                entity = await self.client.get_entity(ref)
                chat_id = normalize(entity)
                self.state.add_watched(
                    chat_id,
                    {
                        "title": getattr(entity, "title", str(ref)),
                        "username": getattr(entity, "username", None),
                    },
                )
                log.info("watching channel: %s (%s)", getattr(entity, "title", ref), chat_id)
            except Exception as exc:
                log.warning("watch channel resolve হয়নি (%s): %s", ref, exc)
        await self.state.save()

    # ── নতুন মেসেজ এলে ───────────────────────────────────────────────────────
    async def _on_message(self, event) -> None:
        if not self.state.data.get("enabled", True):
            return
        chat_id = event.chat_id
        if not self.state.is_watched(chat_id):
            return

        message = event.message
        text = getattr(message, "message", "") or ""
        media = getattr(message, "media", None)
        has_poll = media is not None and hasattr(media, "poll")

        ctx = CallContext.from_message(message, chat_id)
        if not looks_like_call(text, has_poll, bool(ctx.ip and ctx.port)):
            return

        key = f"{chat_id}:{message.id}"
        if self.state.already_processed(key, DEDUPE_TTL):
            return

        # সাথে সাথে কাজ শুরু — handler আটকে রাখা হবে না
        asyncio.create_task(self._handle_call(ctx, key))

    # ── আসল কাজ ─────────────────────────────────────────────────────────────
    async def _handle_call(self, ctx: CallContext, key: str) -> None:
        try:
            # দুইটা কলের মধ্যে ছোট বিরতি (টেলিগ্রাম rate limit এড়াতে)
            gap = time.time() - self._last_call_ts
            if gap < COOLDOWN:
                await asyncio.sleep(COOLDOWN - gap)
            self._last_call_ts = time.time()

            log.info(
                "⚡ কল detected | chat=%s msg=%s ip=%s port=%s",
                ctx.chat_id, ctx.message.id, ctx.ip, ctx.port,
            )
            report = await self.runner.run(ctx)
            await self._notify(report, ctx)
        except Exception as exc:
            log.exception("call pipeline fail: %s", exc)
            self.state.data["last_error"] = str(exc)
            await self.state.save()

    async def _notify(self, report: dict, ctx: CallContext) -> None:
        lines = ["✅ কল প্রসেস হয়েছে", f"IP: {ctx.ip}", f"Port: {ctx.port}"]
        for key, value in report.items():
            if isinstance(value, dict):
                lines.append(f"{key}: {value.get('detail', '')}")
        text = "\n".join(lines)
        log.info("report: %s", text.replace("\n", " | "))
        if LOG_CHANNEL:
            try:
                await self.client.send_message(LOG_CHANNEL, text)
            except Exception as exc:
                log.warning("log channel send fail: %s", exc)

    # ── অ্যাকাউন্ট জীবিত ও "online" থাকবে ─────────────────────────────────
    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(90)
                if not self.client.is_connected():
                    log.warning("client disconnected — reconnecting...")
                    await self.client.connect()
                from telethon.tl import functions

                await self.client(functions.account.UpdateStatusRequest(offline=False))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.debug("heartbeat: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════
#  ৬) Bot — চ্যানেলে অ্যাকাউন্ট add, watch list, status ইত্যাদি
# ═══════════════════════════════════════════════════════════════════════════

HELP = """🎯 <b>CallBot</b> — কল অটো-জয়েন ও ভোট বট

<b>চ্যানেল ব্যবস্থাপনা</b>
/add &lt;channel&gt; &lt;user_id|@user&gt; — চ্যানেলে তোমার অ্যাকাউন্ট add করে
/invite &lt;channel&gt; — চ্যানেলের invite link দেয়
/watch &lt;channel&gt; — চ্যানেলটা monitor শুরু করে
/unwatch &lt;channel&gt; — monitor বন্ধ করে
/watchlist — এখন কোন চ্যানেলগুলো monitor হচ্ছে

<b>সুইচ</b>
/on — পুরো pipeline চালু
/off — পুরো pipeline বন্ধ
/vc on|off — voice chat join/leave চালু/বন্ধ
/vote on|off — ভোট চালু/বন্ধ

<code>/status</code> — অবস্থা
/last — monitor করা চ্যানেলের শেষ মেসেজ ও বের করা IP/port
/test — শেষ মেসেজ দিয়ে pipeline চালিয়ে দেখা
"""


def esc(text) -> str:
    """ইউজারের লেখা Telegram HTML-এ নিরাপদে বসানোর জন্য escape।"""
    return html.escape(str(text or ""), quote=False)


def build_router(client, bot, state, runner):
    """বটের সব কমান্ড — aiogram Router তৈরি করে।"""
    from aiogram import Router
    from aiogram.filters import BaseFilter, Command, CommandObject

    router = Router(name="callbot")

    class AdminOnly(BaseFilter):
        async def __call__(self, message) -> bool:
            return bool(message.from_user) and is_admin(message.from_user.id)

    # ── সাহায্য ─────────────────────────────────────────────────────────────
    @router.message(Command("start", "help"))
    async def cmd_help(message):
        await message.answer(HELP, parse_mode="HTML")

    # ── চ্যানেলে অ্যাকাউন্ট add ─────────────────────────────────────────────
    @router.message(Command("add"), AdminOnly())
    async def cmd_add(message, command):
        args = (command.args or "").split()
        if len(args) < 2:
            return await message.answer(
                "ব্যবহার: <code>/add @your_channel 123456789</code>"
            )
        channel_ref, user_ref = args[0], args[1]

        try:
            entity = await client.get_entity(channel_ref)
        except Exception as exc:
            return await message.answer(f"❌ চ্যানেল পাওয়া যায়নি: <code>{esc(exc)}</code>")
        chat_id = normalize(entity)

        try:
            if user_ref.startswith("@"):
                user_id = (await client.get_entity(user_ref)).id
            else:
                user_id = int(user_ref)
        except Exception as exc:
            return await message.answer(f"❌ ইউজার ঠিকানা বোঝা যায়নি: <code>{esc(exc)}</code>")

        # ১) বট দিয়ে add করার চেষ্টা
        try:
            await bot.add_chat_member(chat_id, user_id)
            return await message.answer(
                f"✅ অ্যাকাউন্ট <code>{esc(user_ref)}</code> চ্যানেলে add হয়েছে।\n"
                f"Chat ID: <code>{chat_id}</code>\n"
                "এখন <code>/watch</code> দিয়ে monitor চালু করো।"
            )
        except Exception as exc:
            log.warning("add_chat_member fail: %s", exc)

        # ২) ফলব্যাক — invite link বানিয়ে userbot দিয়ে join
        try:
            from telethon.tl.functions.messages import ImportChatInviteRequest

            link = await bot.create_chat_invite_link(chat_id)
            h = link.invite_link.rsplit("/", 1)[-1]
            await client(ImportChatInviteRequest(h))
            return await message.answer(
                f"✅ invite link দিয়ে অ্যাকাউন্ট join করানো হয়েছে।\n"
                f"Chat ID: <code>{chat_id}</code>\n"
                f"Link: {esc(link.invite_link)}"
            )
        except Exception as exc:
            return await message.answer(
                "❌ add করা যায়নি: <code>"
                f"{esc(exc)}</code>\n\n"
                "বটকে চ্যানেলের admin বানাও এবং 'Invite Users' permission দাও।"
            )

    @router.message(Command("invite"), AdminOnly())
    async def cmd_invite(message, command):
        args = (command.args or "").split()
        if not args:
            return await message.answer("ব্যবহার: <code>/invite @your_channel</code>")
        try:
            entity = await client.get_entity(args[0])
            chat_id = normalize(entity)
            link = await bot.create_chat_invite_link(chat_id)
            await message.answer(
                f"Invite link: {esc(link.invite_link)}\nChat ID: <code>{chat_id}</code>"
            )
        except Exception as exc:
            await message.answer(f"❌ <code>{esc(exc)}</code>")

    # ── watch list ──────────────────────────────────────────────────────────
    @router.message(Command("watch"), AdminOnly())
    async def cmd_watch(message, command):
        args = (command.args or "").split()
        if not args:
            return await message.answer("ব্যবহার: <code>/watch @your_channel</code>")
        try:
            entity = await client.get_entity(args[0])
            chat_id = normalize(entity)
        except Exception as exc:
            return await message.answer(f"❌ চ্যানেল পাওয়া যায়নি: <code>{esc(exc)}</code>")
        state.add_watched(
            chat_id,
            {
                "title": getattr(entity, "title", args[0]),
                "username": getattr(entity, "username", None),
            },
        )
        await state.save()
        await message.answer(
            f"✅ এখন মনিটর হচ্ছে: <b>{esc(getattr(entity, 'title', args[0]))}</b>\n"
            f"Chat ID: <code>{chat_id}</code>"
        )

    @router.message(Command("unwatch"), AdminOnly())
    async def cmd_unwatch(message, command):
        args = (command.args or "").split()
        if not args:
            return await message.answer("ব্যবহার: <code>/unwatch @your_channel</code>")
        try:
            entity = await client.get_entity(args[0])
            chat_id = normalize(entity)
        except Exception as exc:
            await message.answer(f"❌ <code>{esc(exc)}</code>")
        removed = state.remove_watched(chat_id)
        await state.save()
        await message.answer(
            "✅ মনিটর বন্ধ করা হয়েছে।" if removed else "⚠️ এই চ্যানেল মনিটরে ছিল না।"
        )

    @router.message(Command("watchlist"), AdminOnly())
    async def cmd_watchlist(message):
        watched = state.data.get("watched", {})
        if not watched:
            return await message.answer("এখন কোনো চ্যানেল মনিটরে নেই।")
        lines = ["📡 <b>মনিটর করা চ্যানেল:</b>"]
        for cid, meta in watched.items():
            title = (meta or {}).get("title") or (meta or {}).get("username") or "?"
            lines.append(f"• {esc(title)} — <code>{cid}</code>")
        await message.answer("\n".join(lines))

    # ── সুইচ ────────────────────────────────────────────────────────────────
    @router.message(Command("on", "off"), AdminOnly())
    async def cmd_switch(message, command):
        on = (command.command or "").split("@")[0] == "on"
        state.data["enabled"] = on
        await state.save()
        await message.answer("▶️ চালু করা হয়েছে।" if on else "⏸️ বন্ধ করা হয়েছে।")

    @router.message(Command("vc", "vote"), AdminOnly())
    async def cmd_feature(message, command):
        name = (command.command or "").split("@")[0]
        args = (command.args or "").strip().lower()
        if args not in {"on", "off"}:
            return await message.answer(
                f"ব্যবহার: <code>/{name} on</code> অথবা <code>/{name} off</code>"
            )
        key = "vc_enabled" if name == "vc" else "vote_enabled"
        state.data[key] = args == "on"
        await state.save()
        await message.answer(f"✅ <code>{name}</code> → <b>{args}</b>")

    # ── status / last / test ────────────────────────────────────────────────
    @router.message(Command("status"), AdminOnly())
    async def cmd_status(message):
        last = state.data.get("last_run")
        lines = [
            "📊 <b>CallBot Status</b>",
            f"Pipeline: {'🟢 চালু' if state.data.get('enabled', True) else '🔴 বন্ধ'}",
            f"Voice chat: {'🟢 চালু' if state.data.get('vc_enabled', True) else '🔴 বন্ধ'}",
            f"Vote: {'🟢 চালু' if state.data.get('vote_enabled', True) else '🔴 বন্ধ'}",
            f"Monitor করা চ্যানেল: {len(state.data.get('watched', {}))}",
        ]
        if last:
            lines.append(
                f"শেষ কল: {esc(last.get('ip'))}:{esc(last.get('port'))} — "
                f"{esc(last.get('report', {}).get('vote', '-'))}"
            )
        if state.data.get("last_error"):
            lines.append(f"শেষ এরর: {esc(state.data['last_error'])}")
        await message.answer("\n".join(lines))

    @router.message(Command("last"), AdminOnly())
    async def cmd_last(message):
        watched = list(state.watched_ids())
        if not watched:
            return await message.answer("কোনো চ্যানেল মনিটরে নেই।")
        for chat_id in watched:
            try:
                msg = await client.get_messages(chat_id, limit=1)
            except Exception:
                continue
            if not msg:
                continue
            ctx = CallContext.from_message(msg[0], chat_id)
            text = (ctx.text or "")[:900]
            await message.answer(
                f"📩 <b>শেষ মেসেজ</b> (chat <code>{chat_id}</code>)\n"
                f"IP: <code>{esc(ctx.ip)}</code>\nPort: <code>{esc(ctx.port)}</code>\n"
                f"Links: {esc(', '.join(ctx.links)) or '-'}\n\n<code>{esc(text)}</code>"
            )
            return
        await message.answer("কোনো মেসেজ পাওয়া যায়নি।")

    @router.message(Command("test"), AdminOnly())
    async def cmd_test(message):
        watched = list(state.watched_ids())
        if not watched:
            return await message.answer("কোনো চ্যানেল মনিটরে নেই।")
        for chat_id in watched:
            try:
                msg = await client.get_messages(chat_id, limit=1)
            except Exception:
                continue
            if not msg:
                continue
            ctx = CallContext.from_message(msg[0], chat_id)
            if not (ctx.ip or ctx.text):
                await message.answer("শেষ মেসেজে IP/port পাওয়া যায়নি।")
                return
            await message.answer("⏳ টেস্ট চালু হচ্ছে...")
            report = await runner.run(ctx)
            detail = "\n".join(
                f"• {k}: {esc(v.get('detail', '') if isinstance(v, dict) else v)}"
                for k, v in report.items()
            )
            await message.answer(f"<b>টেস্ট রিপোর্ট</b>\n{detail}")
            return
        await message.answer("কোনো মেসেজ পাওয়া যায়নি।")

    return router


# ═══════════════════════════════════════════════════════════════════════════
#  ৭) চালানোর অংশ
# ═══════════════════════════════════════════════════════════════════════════

def setup_logging() -> None:
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)-14s | %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    try:
        file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError as exc:
        print(f"log file বানানো যায়নি: {exc}")


async def start_userbot():
    """User account (userbot) চালু করে — প্রথমবার লগইন হবে।"""
    from telethon import TelegramClient

    client = TelegramClient(str(SESSION_FILE), API_ID, API_HASH)
    if PHONE:
        result = client.start(phone=PHONE, password=PASSWORD or None)
    else:
        result = client.start()
    # Telethon 1.x-এ start() coroutine, পুরনো ভার্সনে sync — দুইটাই সামলানো
    if inspect.isawaitable(result):
        await result
    return client


async def main() -> None:
    from aiogram import Bot, Dispatcher
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode

    setup_logging()

    if not API_ID or not API_HASH:
        log.error("API_ID / API_HASH সেট করো (.env) — https://my.telegram.org থেকে পাবে")
        sys.exit(1)
    if not BOT_TOKEN:
        log.error("BOT_TOKEN সেট করো (.env) — @BotFather থেকে পাবে")
        sys.exit(1)

    state = StateStore(STATE_FILE)
    await state.load()

    # ── ১) userbot ──────────────────────────────────────────────────────────
    user = await start_userbot()
    me = await user.get_me()
    log.info("User account online → @%s (id=%s)", me.username, me.id)

    # ── ২) voice chat engine ────────────────────────────────────────────────
    calls = None
    try:
        from pytgcalls import PyTgCalls

        calls = PyTgCalls(user)
        res = calls.start()
        if inspect.isawaitable(res):
            await res
        log.info("pytgcalls ready (voice chat join/leave চালু)")
    except Exception as exc:
        log.warning("pytgcalls চালু হয়নি — voice chat কাজ করবে না: %s", exc)

    # ── ৩) bot + watcher ────────────────────────────────────────────────────
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()

    runner = ActionRunner(user, calls, state)
    watcher = CallWatcher(user, state, runner)
    await watcher.start()

    dispatcher.include_router(build_router(user, bot, state, runner))

    await bot.delete_webhook(drop_pending_updates=True)
    log.info("Bot polling started — /help দেখো")

    try:
        await asyncio.gather(
            dispatcher.start_polling(bot),
            user.run_until_disconnected(),
        )
    finally:
        if calls is not None:
            try:
                await runner.leave_vc()
            except Exception:
                pass
        await state.save()
        log.info("CallBot stopped")


# ═══════════════════════════════════════════════════════════════════════════
#  ৮) Self-test — টেলিগ্রাম ছাড়াই পাইপলাইন যাচাই (python main.py --selftest)
# ═══════════════════════════════════════════════════════════════════════════

class _FakeClient:
    def __init__(self):
        self.votes = []

    async def __call__(self, request):
        self.votes.append(request)
        return None

    async def get_me(self):
        from types import SimpleNamespace

        return SimpleNamespace(username="test_user", id=42)

    async def get_input_peer(self, chat_id):
        return chat_id


class _FakeCalls:
    def __init__(self):
        self.played = []
        self.left = []

    async def start(self):
        return None

    async def play(self, chat_id, media):
        self.played.append(chat_id)

    async def leave(self, chat_id):
        self.left.append(chat_id)


class _FakeState:
    def __init__(self):
        self.data = {"enabled": True, "vc_enabled": True, "vote_enabled": True}

    async def save(self):
        return None


class _FakeMessage:
    def __init__(self, msg_id, text, media=None, buttons=None):
        self.id = msg_id
        self.message = text
        self.media = media
        self.buttons = buttons
        self.clicked = None

    async def click(self, i=None, j=None, text=None, **kwargs):
        self.clicked = {"i": i, "j": j, "text": text}


async def selftest() -> None:
    from types import SimpleNamespace

    global VC_ENABLED, VC_CHAT_ID, VOTE_ENABLED, VOTE_OPTION_KEYWORD
    global VOTE_BUTTON_KEYWORD, DWELL_SECONDS, KNOCK_ENABLED

    VC_ENABLED = True
    VC_CHAT_ID = -1001234567890
    VOTE_ENABLED = True
    VOTE_OPTION_KEYWORD = "হ্যাঁ"
    DWELL_SECONDS = 0
    KNOCK_ENABLED = False

    client, calls, state = _FakeClient(), _FakeCalls(), _FakeState()
    runner = ActionRunner(client, calls, state)

    # ১) IP/port সহ মেসেজ → join → leave
    msg = _FakeMessage(101, "🔴 CALL LIVE\nIP: 103.21.184.15\nPORT: 5555")
    ctx = CallContext.from_message(msg, -100999)
    print(f"1) parsed → ip={ctx.ip} port={ctx.port}")
    assert (ctx.ip, ctx.port) == ("103.21.184.15", 5555), "IP/port parse ভুল!"
    report = await runner.run(ctx)
    print(f"   report → {report}")
    assert calls.played == [-1001234567890], "voice chat join হয়নি!"
    assert calls.left == [-1001234567890], "voice chat leave হয়নি!"

    # ২) পোলে ভোট
    poll = SimpleNamespace(
        answers=[
            SimpleNamespace(text="হ্যাঁ", option=b"opt-yes"),
            SimpleNamespace(text="না", option=b"opt-no"),
        ]
    )
    msg2 = _FakeMessage(102, "কল চালু হয়েছে, ভোট দাও",
                        media=SimpleNamespace(poll=poll, results=None))
    ctx2 = CallContext.from_message(msg2, -100999)
    assert ctx2.poll is not None, "poll ধরা পড়েনি!"
    vote_result = await runner.vote(ctx2)
    print(f"2) vote → {vote_result}")
    assert vote_result.get("ok"), "vote দেওয়া যায়নি!"
    assert msg2.clicked and msg2.clicked.get("text") == "হ্যাঁ", "সঠিক অপশনে ভোট হয়নি!"

    # ৩) ইনলাইন বাটনে ভোট
    VOTE_BUTTON_KEYWORD = "JOIN"
    VOTE_OPTION_KEYWORD = ""
    btn = SimpleNamespace(text="✅ JOIN NOW")
    msg3 = _FakeMessage(103, "কল", buttons=[[btn]])
    ctx3 = CallContext.from_message(msg3, -100999)
    btn_result = await runner.vote(ctx3)
    print(f"3) button → {btn_result}")
    assert btn_result.get("ok"), "বাটনে ক্লিক হয়নি!"

    # ৪) সাধারণ মেসেজ কল হিসেবে ধরা হচ্ছে না
    plain = _FakeMessage(104, "আজকের স্টক নোট")
    ctx4 = CallContext.from_message(plain, -100999)
    assert not looks_like_call(ctx4.text, False, False), "সাধারণ মেসেজ কল হিসেবে ধরা হয়েছে!"

    print("\n✅ সব সেল্ফ-টেস্ট পাস করেছে — পাইপলাইন ঠিক আছে।")


if __name__ == "__main__":
    try:
        if "--selftest" in sys.argv:
            asyncio.run(selftest())
        else:
            asyncio.run(main())
    except KeyboardInterrupt:
        print("\nবন্ধ করা হলো।")
