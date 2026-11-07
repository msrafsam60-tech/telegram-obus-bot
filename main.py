"""
Telegram Multi-Client Voice Chat Bot
=====================================
RUN: python main.py  (RUN_MODE=client or RUN_MODE=master)

Config comes from environment variables (.env file or host's Variables panel):
    BOT_TOKEN, API_ID, API_HASH, OWNER_ID   (required)
    DATA_DIR   (optional, folder for data.db / secret.key, default = current folder)
    ENC_KEY    (optional, fixes the encryption key instead of using secret.key)
    PROXY_URL  (optional, e.g. socks5://user:pass@host:port, used only for login)
    DELAY      (optional, seconds between accounts, default 1)
"""
import asyncio, html, io, logging, os, re, secrets, sqlite3, time, tempfile
from urllib.parse import urlparse
from cryptography.fernet import Fernet
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (BufferedInputFile, Message, CallbackQuery, ErrorEvent, ReplyKeyboardMarkup, KeyboardButton,
                           InlineKeyboardMarkup, InlineKeyboardButton)
from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler
from pyrogram.errors import (FloodWait, SessionPasswordNeeded, PhoneCodeInvalid,
                             PhoneCodeExpired, PasswordHashInvalid, UserAlreadyParticipant,
                             AuthTokenExpired)
from pyrogram.raw.functions.messages import GetMessagesViews
from pyrogram.qrlogin import QRLogin
from pytgcalls import PyTgCalls

# ===== ONE-FILE MASTER + CLIENT MODE =====
# Set RUN_MODE="master" on the central server, or "client" on each bot server.
import sys, hmac
from contextlib import suppress
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
import uvicorn
load_dotenv()  # load .env before reading either mode configuration

# Put credentials here for private deployments, OR set the same names in host Variables.
CONFIG_RUN_MODE = "client"  # change to "master" only on the central server
CONFIG_MASTER_BOT_TOKEN = "8791254182:AAGyEgUCnYHltJ_I2tOKsxY8N-YO7tRewiE"
CONFIG_MASTER_OWNER_ID = "6753121703"
CONFIG_MASTER_API_KEY = "Tcslbs1MkniybHdMjsUPNiD98jwa6Cle"  # use a unique secret; keep private

RUN_MODE = os.environ.get("RUN_MODE", CONFIG_RUN_MODE).strip().lower()
MASTER_BOT_TOKEN = os.environ.get("MASTER_BOT_TOKEN", CONFIG_MASTER_BOT_TOKEN)
MASTER_OWNER_ID = int(os.environ.get("MASTER_OWNER_ID", CONFIG_MASTER_OWNER_ID))
MASTER_API_KEY = os.environ.get("INSTANCE_API_KEY", CONFIG_MASTER_API_KEY)

if RUN_MODE == "master":
    if MASTER_BOT_TOKEN.startswith("PASTE_") or MASTER_OWNER_ID <= 0:
        raise SystemExit("Master mode: set MASTER_BOT_TOKEN and MASTER_OWNER_ID in CONFIG or host Variables")
    if MASTER_API_KEY.startswith("PASTE_") or len(MASTER_API_KEY) < 24:
        raise SystemExit("Master mode: set INSTANCE_API_KEY to a random secret of at least 24 characters")

    import html as _html
    import sqlite3 as _sqlite3
    import time as _time
    from typing import Optional as _Optional
    from aiogram.filters import Command as _Command, CommandStart as _CommandStart
    from aiogram.types import Message as _Message
    from pydantic import BaseModel as _BaseModel, Field as _Field

    _DATA = os.environ.get("MASTER_DATA_DIR", "master_data")
    os.makedirs(_DATA, exist_ok=True)
    _DB = os.path.join(_DATA, "master.db")
    def _conn():
        c = _sqlite3.connect(_DB, timeout=20); c.row_factory = _sqlite3.Row; return c
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS instances(instance_id TEXT PRIMARY KEY,label TEXT,version TEXT,users INTEGER DEFAULT 0,connected_accounts INTEGER DEFAULT 0,running_accounts INTEGER DEFAULT 0,uptime_seconds INTEGER DEFAULT 0,first_seen INTEGER NOT NULL,last_seen INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,instance_id TEXT NOT NULL,actor_id INTEGER,action TEXT NOT NULL,created INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_events_created ON events(created DESC);
        CREATE INDEX IF NOT EXISTS idx_events_instance ON events(instance_id,created DESC);
        """)
    class _Heartbeat(_BaseModel):
        instance_id: str = _Field(min_length=4,max_length=100)
        label: str = _Field(default="Bot Instance",max_length=80)
        version: str = _Field(default="unknown",max_length=40)
        users: int = _Field(default=0,ge=0,le=10000000)
        connected_accounts: int = _Field(default=0,ge=0,le=100000)
        running_accounts: int = _Field(default=0,ge=0,le=100000)
        uptime_seconds: int = _Field(default=0,ge=0,le=2147483647)
    class _Event(_BaseModel):
        instance_id: str = _Field(min_length=4,max_length=100)
        actor_id: _Optional[int] = None
        action: str = _Field(min_length=1,max_length=80)
    _app = FastAPI(title="Telegram Bot Master API",docs_url=None,redoc_url=None)
    def _auth(key):
        if not key or not hmac.compare_digest(key, MASTER_API_KEY): raise HTTPException(status_code=401,detail="Unauthorized")
    @_app.get("/health")
    async def _health(): return {"ok":True,"service":"master-api"}
    @_app.post("/api/heartbeat")
    async def _heartbeat(d:_Heartbeat,x_instance_key:str|None=Header(default=None)):
        _auth(x_instance_key); now=int(_time.time())
        with _conn() as c:
            c.execute("""INSERT INTO instances(instance_id,label,version,users,connected_accounts,running_accounts,uptime_seconds,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(instance_id) DO UPDATE SET label=excluded.label,version=excluded.version,users=excluded.users,connected_accounts=excluded.connected_accounts,running_accounts=excluded.running_accounts,uptime_seconds=excluded.uptime_seconds,last_seen=excluded.last_seen""",(d.instance_id,d.label,d.version,d.users,d.connected_accounts,d.running_accounts,d.uptime_seconds,now,now))
        return {"ok":True,"server_time":now}
    @_app.post("/api/event")
    async def _event(d:_Event,x_instance_key:str|None=Header(default=None)):
        _auth(x_instance_key); now=int(_time.time())
        with _conn() as c:
            if not c.execute("SELECT 1 FROM instances WHERE instance_id=?",(d.instance_id,)).fetchone(): raise HTTPException(status_code=409,detail="Heartbeat first")
            c.execute("INSERT INTO events(instance_id,actor_id,action,created) VALUES(?,?,?,?)",(d.instance_id,d.actor_id,d.action,now))
        return {"ok":True}
    _mbot=Bot(MASTER_BOT_TOKEN,default=DefaultBotProperties(parse_mode=ParseMode.HTML)); _mdp=Dispatcher()
    def _is_owner(m): return bool(m.from_user and m.from_user.id==MASTER_OWNER_ID)
    @_mdp.message(_CommandStart())
    async def _mstart(m:_Message):
        if not _is_owner(m): return await m.answer("⛔ শুধু Master Owner ব্যবহার করতে পারবেন।")
        await m.answer("🛡️ <b>MASTER ADMIN PANEL</b>\n/dashboard — রিপোর্ট\n/instances — ইনস্ট্যান্স\n/activity — কার্যক্রম\n/instance ID — বিস্তারিত")
    @_mdp.message(_Command("dashboard"))
    async def _mdash(m:_Message):
        if not _is_owner(m): return await m.answer("⛔ Admin only")
        now=int(_time.time())
        with _conn() as c:
            total=c.execute("SELECT COUNT(*) n FROM instances").fetchone()["n"]; online=c.execute("SELECT COUNT(*) n FROM instances WHERE last_seen>=?",(now-45,)).fetchone()["n"]
            sums=c.execute("SELECT COALESCE(SUM(users),0) users,COALESCE(SUM(connected_accounts),0) accounts,COALESCE(SUM(running_accounts),0) running FROM instances").fetchone(); events=c.execute("SELECT COUNT(*) n FROM events WHERE created>=?",(now-86400,)).fetchone()["n"]
        await m.answer(f"📊 <b>MASTER DASHBOARD</b>\n\nInstances: <b>{total}</b>\n🟢 Online: <b>{online}</b>\n⚫ Offline: <b>{total-online}</b>\n👥 Users reported: <b>{sums['users']}</b>\n📱 Connected accounts: <b>{sums['accounts']}</b>\n▶️ Running: <b>{sums['running']}</b>\n📈 Events (24h): <b>{events}</b>")
    @_mdp.message(_Command("instances"))
    async def _minstances(m:_Message):
        if not _is_owner(m): return await m.answer("⛔ Admin only")
        now=int(_time.time())
        with _conn() as c: rows=c.execute("SELECT * FROM instances ORDER BY last_seen DESC LIMIT 35").fetchall()
        if not rows: return await m.answer("এখনও কোনো ইনস্ট্যান্স যুক্ত হয়নি।")
        out=["🧩 <b>INSTANCES</b>"]
        for r in rows: out.append(f"\n{'🟢 ONLINE' if now-r['last_seen']<=45 else '⚫ OFFLINE'}\nID: <code>{_html.escape(r['instance_id'])}</code>\nName: {_html.escape(r['label'] or '')}\nUsers: {r['users']} | Accounts: {r['connected_accounts']} | Running: {r['running_accounts']}\nLast seen: {now-r['last_seen']}s ago")
        await m.answer("\n".join(out)[:4000])
    @_mdp.message(_Command("activity"))
    async def _mactivity(m:_Message):
        if not _is_owner(m): return await m.answer("⛔ Admin only")
        with _conn() as c: rows=c.execute("SELECT * FROM events ORDER BY created DESC LIMIT 25").fetchall()
        if not rows: return await m.answer("কোনো activity নেই।")
        out=["🕒 <b>RECENT ACTIVITY</b>"]
        for r in rows: out.append(f"\n{_time.strftime('%Y-%m-%d %H:%M:%S',_time.localtime(r['created']))} | <code>{_html.escape(r['instance_id'])}</code>\n{_html.escape(r['action'])} | User ID: <code>{r['actor_id'] or '—'}</code>")
        await m.answer("\n".join(out)[:4000])
    @_mdp.message(_Command("instance"))
    async def _minstance(m:_Message):
        if not _is_owner(m): return await m.answer("⛔ Admin only")
        p=(m.text or "").split(maxsplit=1)
        if len(p)<2: return await m.answer("ব্যবহার: /instance INSTANCE_ID")
        with _conn() as c: r=c.execute("SELECT * FROM instances WHERE instance_id=?",(p[1].strip(),)).fetchone()
        if not r: return await m.answer("ইনস্ট্যান্স পাওয়া যায়নি।")
        age=int(_time.time())-r['last_seen']; await m.answer(f"🔎 <b>INSTANCE</b>\nID: <code>{_html.escape(r['instance_id'])}</code>\nStatus: {'🟢 Online' if age<=45 else '⚫ Offline'}\nName: {_html.escape(r['label'] or '')}\nVersion: {r['version']}\nUsers: {r['users']}\nAccounts: {r['connected_accounts']}\nRunning: {r['running_accounts']}\nUptime: {r['uptime_seconds']} sec\nLast seen: {age} sec ago")
    async def _master_run():
        server=uvicorn.Server(uvicorn.Config(_app,host="0.0.0.0",port=int(os.environ.get("PORT","8000")),log_level="info"))
        at=asyncio.create_task(server.serve()); bt=asyncio.create_task(_mdp.start_polling(_mbot))
        try: await asyncio.gather(at,bt)
        finally:
            server.should_exit=True
            for t in (at,bt):
                if not t.done(): t.cancel()
            await _mbot.session.close()
    if __name__ == "__main__": asyncio.run(_master_run())
    sys.exit(0)


load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot")

# ====== MAIN BOT CONFIG: চাইলে সরাসরি এখানেই বসাও ======
# নিরাপত্তার জন্য public/shared code-এ আসল token না রেখে private deployment-এ বসানো ভালো।
CONFIG_BOT_TOKEN = "8791254182:AAGyEgUCnYHltJ_I2tOKsxY8N-YO7tRewiE"
CONFIG_API_ID = "35226105"
CONFIG_API_HASH = "PASTE_API_HASH_HERE"
CONFIG_OWNER_ID = "6753121703"

_CONFIG_VALUES = {
    "BOT_TOKEN": CONFIG_BOT_TOKEN,
    "API_ID": CONFIG_API_ID,
    "API_HASH": CONFIG_API_HASH,
    "OWNER_ID": CONFIG_OWNER_ID,
}

def _need(name):
    # Host environment variables override the editable in-code config.
    v = os.environ.get(name) or _CONFIG_VALUES.get(name)
    if not v or str(v).startswith("PASTE_"):
        raise SystemExit(f"❌ Missing required configuration: {name}. Edit CONFIG section or set host Variables.")
    return v

BOT_TOKEN = _need("BOT_TOKEN")
API_ID = int(_need("API_ID"))
API_HASH = _need("API_HASH")
OWNER_ID = int(_need("OWNER_ID"))
DATA_DIR = os.environ.get("DATA_DIR", ".")
os.makedirs(DATA_DIR, exist_ok=True)
DELAY = float(os.environ.get("DELAY", "1"))  # seconds between accounts (FloodWait protection)
BOOT_TIME = time.time()
DEV_CONTACT = os.environ.get("DEV_CONTACT", "@Rafsanvai0")  # set this in .env, shown on 👨‍💻 Developer button

# ====== Optional Central Master Monitoring ======
# Set MASTER_API_URL and INSTANCE_API_KEY on every distributed bot copy.
# MASTER_API_URL example: https://your-master-host.example.com
CONFIG_MASTER_API_URL = "telegram-obus-bot-production.up.railway.app"
CONFIG_INSTANCE_API_KEY = CONFIG_MASTER_API_KEY
CONFIG_INSTANCE_LABEL = "Telegram Bot Instance"
MASTER_API_URL = os.environ.get("MASTER_API_URL", CONFIG_MASTER_API_URL).rstrip("/")
INSTANCE_API_KEY = os.environ.get("INSTANCE_API_KEY", CONFIG_INSTANCE_API_KEY)
INSTANCE_LABEL = os.environ.get("INSTANCE_LABEL", CONFIG_INSTANCE_LABEL)
if MASTER_API_URL.startswith("https://YOUR-MASTER-HOST"):
    MASTER_API_URL = ""
if INSTANCE_API_KEY.startswith("PASTE_"):
    INSTANCE_API_KEY = ""
INSTANCE_VERSION = "2.0-master-monitor"
INSTANCE_ID_FILE = os.path.join(DATA_DIR, "instance.id")
if os.environ.get("INSTANCE_ID"):
    INSTANCE_ID = os.environ["INSTANCE_ID"].strip()
else:
    try:
        INSTANCE_ID = open(INSTANCE_ID_FILE, "r", encoding="utf-8").read().strip()
    except OSError:
        INSTANCE_ID = secrets.token_urlsafe(12)
        with open(INSTANCE_ID_FILE, "w", encoding="utf-8") as _f:
            _f.write(INSTANCE_ID)




def _proxy():
    """Optional proxy for login only. Set PROXY_URL=socks5://user:pass@host:port"""
    u = os.environ.get("PROXY_URL", "").strip()
    if not u:
        return None
    x = urlparse(u)
    return dict(scheme=x.scheme or "socks5", hostname=x.hostname, port=x.port,
                username=x.username, password=x.password)


# ====== Telegram Premium emoji (optional) ======
# ২-৩ অক্ষরের স্লট আগে থেকে ঠিক করা আছে (নিচে SLOTS), এখানে কিছু লেখার দরকার নেই।
# অ্যাডমিন /setemoji বা /setpack দিয়ে বসালে স্বয়ংক্রিয়ভাবে এখানে DB থেকে লোড হয়।
PREMIUM = {}


def h(x):
    return html.escape(str(x))


def norm(e):
    return str(e).replace("\ufe0f", "")

WELCOME_TEXT = (
    "✨ <b>WELCOME TO THE VOICE CONTROL BOT</b> ✨\n"
    "━━━━━━━━━━━━━━━━━━━━\n\n"
    "🎧 Join & leave voice chats across many accounts at once\n"
    "📩 Send one-to-one text and media from a connected account\n"
    "⚡ Auto React + 👀 Auto View on every new post\n"
    "🚀 Mass join channels/groups with one tap\n"
    "💎 VIP access system with one-time keys\n"
    "🔳 QR + phone login, works for any account\n"
    "🎨 Full premium-emoji theming\n\n"
    "📦 <b>Want this bot for your own channel/group?</b>\n"
    f"📨 Inbox {h(DEV_CONTACT)} to get your own copy, custom setup, or support.\n\n"
    "━━━━━━━━━━━━━━━━━━━━"
)  # edit this however you like — it's shown once at the top of every /start



# ---------- encryption + db ----------
KEY_FILE = os.path.join(DATA_DIR, "secret.key")
if os.environ.get("ENC_KEY"):
    KEY = os.environ["ENC_KEY"].encode()
elif os.path.exists(KEY_FILE):
    KEY = open(KEY_FILE, "rb").read()
else:
    KEY = Fernet.generate_key()
    open(KEY_FILE, "wb").write(KEY)
fernet = Fernet(KEY)

db = sqlite3.connect(os.path.join(DATA_DIR, "data.db"))
db.executescript("""
CREATE TABLE IF NOT EXISTS clients(id INTEGER PRIMARY KEY, name TEXT, phone TEXT, session BLOB, owner INTEGER);
CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY, name TEXT, username TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS auto2(owner INTEGER, kind TEXT, chat_id TEXT, emoji TEXT, PRIMARY KEY(owner, kind, chat_id));
CREATE TABLE IF NOT EXISTS access_keys(key TEXT PRIMARY KEY, status TEXT, created INTEGER, used_by INTEGER, used_at INTEGER);
CREATE TABLE IF NOT EXISTS vip_users(user_id INTEGER PRIMARY KEY, key TEXT);
CREATE TABLE IF NOT EXISTS premium(emoji TEXT PRIMARY KEY, cid TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS activity(
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
 action TEXT NOT NULL, details TEXT DEFAULT '', created INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activity_user_time ON activity(user_id, created DESC);
""")
try:
    db.execute("ALTER TABLE clients ADD COLUMN owner INTEGER")
except sqlite3.OperationalError:
    pass
db.execute("UPDATE clients SET owner=? WHERE owner IS NULL", (OWNER_ID,))
db.commit()
for _e, _c in db.execute("SELECT emoji, cid FROM premium").fetchall():   # premium emoji saved with /setemoji
    PREMIUM[_e] = _c
_t = {norm(k): v for k, v in PREMIUM.items()}
PREMIUM.clear(); PREMIUM.update(_t)

_row = db.execute("SELECT value FROM settings WHERE key='bold'").fetchone()
BOLD_MODE = [_row is None or _row[0] == "1"]   # default ON; mutable 1-item list so it's easy to flip from inline funcs

# ---------- runtime state ----------
RUNNING = {}     # tg_id -> (Client, PyTgCalls, owner_user_id)
BOT_INSTANCE = None  # set during boot; used by safe background error notifications
PENDING = {}     # bot user id -> dict(app, phone, hash, task?)
AUTO_REACT = {}  # (owner, chat_id) -> emoji
AUTO_VIEW = set()  # (owner, chat_id)
SEEN = {}        # (tg_id, chat, kind) -> set(message ids)


def load_auto():
    AUTO_REACT.clear(); AUTO_VIEW.clear()
    for owner, kind, chat, emoji in db.execute("SELECT owner, kind, chat_id, emoji FROM auto2"):
        if kind == "react":
            AUTO_REACT[(owner, int(chat))] = emoji
        else:
            AUTO_VIEW.add((owner, int(chat)))


def is_vip(uid):
    return bool(db.execute("SELECT 1 FROM vip_users WHERE user_id=?", (uid,)).fetchone())


def user_clients(uid):
    """Admin controls ALL accounts; VIP key holders control their own + the admin's accounts;
    everyone else only their own."""
    if uid == OWNER_ID:
        return list(RUNNING.items())
    if is_vip(uid):
        return [(tid, v) for tid, v in RUNNING.items() if v[2] in (uid, OWNER_ID)]
    return [(tid, v) for tid, v in RUNNING.items() if v[2] == uid]


def new_key():
    k = "VIP-" + secrets.token_hex(6).upper()
    db.execute("INSERT INTO access_keys(key,status,created) VALUES(?,?,?)", (k, "active", int(time.time())))
    db.commit()
    return k


def owner_name(oid):
    if oid == OWNER_ID:
        return "You"
    r = db.execute("SELECT name FROM users WHERE user_id=?", (oid,)).fetchone()
    return r[0] if r and r[0] else str(oid)


def _master_post_sync(path, payload):
    """Send minimal operational telemetry to the separately hosted master API."""
    if not MASTER_API_URL or not INSTANCE_API_KEY:
        return
    import json
    from urllib.request import Request, urlopen
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    req = Request(MASTER_API_URL + path, data=body, headers={
        "Content-Type": "application/json",
        "X-Instance-Key": INSTANCE_API_KEY,
    }, method="POST")
    with urlopen(req, timeout=8) as response:
        response.read(128)


async def _master_post(path, payload):
    if not MASTER_API_URL or not INSTANCE_API_KEY:
        return
    try:
        await asyncio.to_thread(_master_post_sync, path, payload)
    except Exception as exc:
        log.debug("master telemetry unavailable: %s", type(exc).__name__)


async def _master_heartbeat_loop():
    """Send online heartbeat and aggregate counts every 15 seconds."""
    while True:
        try:
            users_count = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            clients_count = db.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
            await _master_post("/api/heartbeat", {
                "instance_id": INSTANCE_ID,
                "label": INSTANCE_LABEL[:80],
                "version": INSTANCE_VERSION,
                "users": int(users_count),
                "connected_accounts": int(clients_count),
                "running_accounts": int(len(RUNNING)),
                "uptime_seconds": int(time.time() - BOOT_TIME),
            })
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.debug("master heartbeat failed: %s", type(exc).__name__)
        await asyncio.sleep(15)


def log_activity(user_id, action, details=""):
    """Persist a privacy-conscious audit event; never store OTPs, passwords or message bodies."""
    try:
        db.execute("INSERT INTO activity(user_id,action,details,created) VALUES(?,?,?,?)",
                   (int(user_id), str(action)[:80], str(details)[:300], int(time.time())))
        db.commit()
        # Only action category + actor ID are reported; no message text, OTP, phone, or session data.
        if MASTER_API_URL and INSTANCE_API_KEY:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_master_post("/api/event", {
                    "instance_id": INSTANCE_ID,
                    "actor_id": int(user_id),
                    "action": str(action)[:80],
                }))
            except RuntimeError:
                pass
    except Exception:
        log.exception("activity log write failed")


async def notify_owner_activity(bot, user, action, details=""):
    """Immediately send the owner an alert for account add / voice join / voice leave by non-admin users."""
    try:
        if not user or int(user.id) == OWNER_ID:
            return
        username = f"@{user.username}" if getattr(user, "username", None) else "(username নেই)"
        name = h(getattr(user, "full_name", None) or "Unknown")
        uid = int(user.id)
        labels = {
            "account_added": "➕ নতুন Telegram account যোগ করেছে",
            "voice_join": "🎧 Voice chat-এ Join করেছে",
            "voice_leave": "🔇 Voice chat থেকে Leave করেছে",
        }
        title = labels.get(action, h(action))
        message = (
            "🔔 <b>User Activity Alert</b>\n\n"
            f"{title}\n"
            f"👤 User: {name}\n"
            f"🔗 Username: {h(username)}\n"
            f"🆔 User ID: <code>{uid}</code>\n"
            f"📌 Details: <code>{h(details)[:700]}</code>"
        )
        await bot.send_message(OWNER_ID, message)
    except Exception:
        log.exception("owner activity notification failed")


# ---------- helpers ----------
def parse_target(t):
    t = t.strip()
    m = re.search(r"t\.me/c/(\d+)", t)
    if m: return int("-100" + m.group(1))
    if re.fullmatch(r"-?\d+", t): return int(t)
    m = re.search(r"t\.me/(\+[\w-]+|joinchat/[\w-]+)", t)
    if m: return "https://t.me/" + m.group(1)
    m = re.search(r"(?:t\.me/|@)([A-Za-z0-9_]{4,})", t)
    if m: return m.group(1)
    return t


def parse_post(t):
    m = re.search(r"t\.me/c/(\d+)/(\d+)", t)
    if m: return int("-100" + m.group(1)), int(m.group(2))
    m = re.search(r"t\.me/([A-Za-z0-9_]+)/(\d+)", t)
    if m: return m.group(1), int(m.group(2))
    return None


LAST_ERR = [""]


async def for_all(uid, fn):
    LAST_ERR[0] = ""
    ok = fail = 0
    t0 = time.time()
    for _, (app, calls, _o) in user_clients(uid):
        try:
            try:
                await fn(app, calls)
            except FloodWait as e:
                await asyncio.sleep(e.value + 1)
                await fn(app, calls)
            ok += 1
        except UserAlreadyParticipant:
            ok += 1
        except Exception as e:
            fail += 1
            LAST_ERR[0] = f"{e.__class__.__name__}: {e}"
            log.exception("client failed: %r", e)
        await asyncio.sleep(DELAY)
    return ok, fail, max(1, int(time.time() - t0))


def report(ok, fail, secs, label="Done"):
    total = max(1, ok + fail)
    filled = round(10 * ok / total)
    bar = "▰" * filled + "▱" * (10 - filled)
    return (hd("✅", f"{label} complete") +
            tree(f"✅ Success — <b>{ok}</b>", f"❌ Failed — <b>{fail}</b>", f"⏳ Time — <b>{secs}s</b>") +
            f"\n\n<code>{bar}</code> {round(100 * ok / total)}%" +
            (f"\n\n⚠️ Last error: <code>{h(LAST_ERR[0])}</code>" if fail and LAST_ERR[0] else ""))


def make_handler(owner):
    async def on_post(client, m):
        chat = m.chat.id
        emoji = AUTO_REACT.get((owner, chat)) or AUTO_REACT.get((OWNER_ID, chat))
        if emoji:
            try:
                await client.send_reaction(chat, m.id, emoji)
            except Exception as e:
                log.warning("auto-react failed for chat %s: %r", chat, e)
                if BOT_INSTANCE:
                    await _notify_error(BOT_INSTANCE, owner, e, f"auto-react chat {chat}")
        if (owner, chat) in AUTO_VIEW or (OWNER_ID, chat) in AUTO_VIEW:
            try:
                await client.invoke(GetMessagesViews(peer=await client.resolve_peer(chat), id=[m.id], increment=True))
            except Exception as e:
                log.warning("auto-view failed for chat %s: %r", chat, e)
                if BOT_INSTANCE:
                    await _notify_error(BOT_INSTANCE, owner, e, f"auto-view chat {chat}")
    return on_post


async def start_client(session_string, tg_id, owner):
    """Start one user client; clean up partially-started resources on failure."""
    await stop_client(tg_id)
    app = Client(f"c{tg_id}", api_id=API_ID, api_hash=API_HASH,
                 session_string=session_string, in_memory=True)
    calls = None
    try:
        await app.start()
        try:
            async for _ in app.get_dialogs():   # fills peer cache so chat IDs resolve after restart
                pass
        except Exception as e:
            log.warning("dialogs %s: %r", tg_id, e)
        calls = PyTgCalls(app)
        await calls.start()
        app.add_handler(MessageHandler(make_handler(owner), filters.channel))
        RUNNING[tg_id] = (app, calls, owner)
    except BaseException:
        # Never leave a half-open Pyrogram/PyTgCalls client behind.
        if calls is not None:
            try:
                await calls.stop()
            except Exception:
                log.debug("partial calls cleanup failed for %s", tg_id, exc_info=True)
        try:
            if getattr(app, "is_connected", False):
                await app.stop()
        except Exception:
            log.debug("partial client cleanup failed for %s", tg_id, exc_info=True)
        raise


async def stop_client(tg_id):
    pair = RUNNING.pop(tg_id, None)
    if pair:
        app, calls, _owner = pair
        if calls is not None:
            try:
                await calls.stop()
            except Exception:
                log.debug("PyTgCalls stop failed for %s", tg_id, exc_info=True)
        try:
            if getattr(app, "is_connected", False):
                await app.stop()
        except Exception:
            log.debug("Pyrogram stop failed for %s", tg_id, exc_info=True)


async def recent_ids(app, chat, n=10):
    return [x.id async for x in app.get_chat_history(chat, limit=n)]


async def view_posts(app, chat, ids):
    await app.invoke(GetMessagesViews(peer=await app.resolve_peer(chat), id=ids, increment=True))


async def watcher():
    """Backup for Auto View / Auto React: checks recent posts every 10s so nothing is missed."""
    await asyncio.sleep(20)
    while True:
        try:
            for owner, chat in set(AUTO_VIEW) | set(AUTO_REACT):
                clients = user_clients(owner)
                if not clients:
                    continue
                try:
                    ids = await recent_ids(clients[0][1][0], chat)
                except Exception as e:
                    log.warning("watch chat %s failed: %r", chat, e)
                    if BOT_INSTANCE:
                        await _notify_error(BOT_INSTANCE, owner, e, f"watcher fetch chat {chat}")
                    continue
                for tg_id, (app, _c, _o) in clients:
                    if (owner, chat) in AUTO_VIEW:
                        vkey = (tg_id, chat, "v")
                        if vkey not in SEEN:
                            SEEN[vkey] = set(ids)   # baseline: old posts are not viewed
                        else:
                            new = [i for i in ids if i not in SEEN[vkey]]
                            if new:
                                try:
                                    await view_posts(app, chat, new)
                                    SEEN[vkey].update(new)
                                except FloodWait as e:
                                    await asyncio.sleep(e.value + 1)
                                except Exception as e:
                                    log.warning("watcher view %s failed: %r", tg_id, e)
                                    if BOT_INSTANCE:
                                        await _notify_error(BOT_INSTANCE, owner, e, f"watcher auto-view client {tg_id}")
                    emoji = AUTO_REACT.get((owner, chat))
                    if emoji:
                        key = (tg_id, chat, "r")
                        if key not in SEEN:
                            SEEN[key] = set(ids)   # baseline: don't react to old posts
                        else:
                            for i in [i for i in ids if i not in SEEN[key]]:
                                try:
                                    await app.send_reaction(chat, i, emoji)
                                except Exception as e:
                                    log.debug("react %s: %r", tg_id, e)
                                SEEN[key].add(i)
                    await asyncio.sleep(0.5)
        except Exception as e:
            log.exception("watcher cycle failed")
            if BOT_INSTANCE:
                await _notify_error(BOT_INSTANCE, OWNER_ID, e, "watcher cycle")
        await asyncio.sleep(10)


# ---------- buttons: colors + premium emoji ----------
def split_icon(label):
    nl = norm(label)
    for e, cid in PREMIUM.items():
        if nl.startswith(e):
            return nl[len(e):].strip(), cid
    return label, None


def ibtn(label, data, style=None):
    text, icon = split_icon(label)
    return InlineKeyboardButton(text=text, callback_data=data, style=style, icon_custom_emoji_id=icon)


def ikb(*rows):
    """rows of (label, callback_data[, style]); style = primary | success | danger"""
    return InlineKeyboardMarkup(inline_keyboard=[[ibtn(*b) for b in r] for r in rows])


# Every emoji the design uses. /setemoji maps premium emoji onto these, in this order.
# নতুন ৩টা ফিচার বাটনের জন্য নতুন স্লট যোগ করা হলো: 🔄 Restart, 📊 Status, 📢 Broadcast
# A-Z: EVERY emoji the bot uses anywhere (menu buttons, headers, alerts, logs shown to users).
# /setemoji maps your premium emoji onto these, in this exact order. /setpack auto-matches by emoji
# wherever the pack has that exact emoji too, so you don't have to follow this order by hand.
SLOTS = [
    ("👑", "Admin"), ("💎", "VIP"), ("📱", "Accounts"), ("➕", "Add"), ("🎧", "Voice join"),
    ("🔇", "Voice leave"), ("🚀", "Join channel"), ("💫", "React"), ("⚡", "Auto react"),
    ("👀", "Auto view"), ("🔙", "Back"), ("✅", "Success"), ("❌", "Failed"), ("⏳", "Pending / time"),
    ("🟢", "Online"), ("🔴", "Offline"), ("🔑", "Keys"), ("👥", "Users"), ("📥", "Requests"),
    ("🚫", "Revoked / Ban"), ("🔐", "Lock"), ("🧹", "Remove"), ("🎉", "Celebrate"),
    ("🔄", "Restart"), ("📊", "Status"), ("📢", "Broadcast"), ("🔳", "QR login"),
    ("🕒", "Uptime"), ("🔍", "Find user"), ("⛔", "Admin-only block"), ("🔔", "Owner notify"),
    ("👤", "User icon"), ("🔗", "Link icon"), ("👇", "Pointer"), ("❎", "Cancelled"), ("♻", "Reset"),
    ("📞", "Phone"), ("🔒", "VIP-hidden"), ("📲", "Code via app"), ("💬", "Code via SMS"),
    ("📧", "Code via email"), ("📷", "QR photo"), ("🗑", "Removed"), ("🔁", "Resend code"),
    ("👍", "Thumbs up (example)"), ("🔥", "Fire (example)"), ("➜", "Arrow"),
    ("⚙", "Settings"), ("🧮", "My info"), ("🏓", "Ping"),
    ("🧰", "Admin tools"), ("📌", "Pin post"), ("🧊", "Clear cache"),
    ("👨‍💻", "Developer"),
    ("✦", "Header sparkle"), ("⚠", "Warning"), ("👆", "Pointer up"), ("📨", "Mail/contact"),
    ("🔛", "Toggle on"), ("🔚", "Toggle off"), ("⚪", "Not set"), ("⬅", "Prev page"), ("➡", "Next page"), ("→", "Arrow separator"),
]

B_CLIENTS, B_ADD = "📱 Accounts", "➕ Add Account"
B_JVC, B_LVC = "🎧 Voice Join", "🔇 Voice Leave"
B_JOIN, B_REACT = "🚀 Join Channel", "💫 React"
B_AR, B_AV = "⚡ Auto React", "👀 Auto View"
B_ACC, B_ADMIN, B_BACK = "💎 Access", "👑 Admin", "🔙 Back"
# নতুন ফিচার বাটনগুলো
B_RESTART = "🔄 Restart Accounts"
B_STATUS = "📊 Status"
B_BROADCAST = "📢 Broadcast"
B_UPTIME = "🕒 Uptime"
B_FIND = "🔍 Find User"
B_SETTINGS = "⚙️ Settings"
B_INFO = "🧮 My Info"
B_PING = "🏓 Ping"
B_CLEARAUTO = "🧹 Clear My Auto"
B_ADMTOOLS = "🧰 Admin Tools"
B_PIN = "📌 Pin Post"
B_CACHE = "🧊 Clear Cache"
B_DEV = "👨‍💻 Developer"
B_CUSTOM_INBOX = "📩 CUSTOM SMS INBOX"


def btext(label):
    """Premium emoji becomes the button icon, so the text keeps only the name."""
    e, name = label.split(" ", 1)
    return name if norm(e) in PREMIUM else label


def ANY(label):
    """Filter that matches a button with or without its emoji."""
    return F.text.in_({label, label.split(" ", 1)[1]})


def rbtn(label, style=None):
    return KeyboardButton(text=btext(label), style=style, icon_custom_emoji_id=PREMIUM.get(norm(label.split(" ", 1)[0])))


def hd(icon, title, sub=""):
    """Screen header: glow line + icon + bold caps title (+ italic subtitle)."""
    return f"✦ {icon} <b>{title.upper()}</b> ✦" + (f"\n<i>{sub}</i>" if sub else "") + "\n▬▬▬▬▬▬▬▬▬▬▬▬\n\n"


def tree(*lines, expand=False):
    """Clean quote card, one item per row: icon label — value"""
    lines = [l for l in lines if l]
    tag = "blockquote expandable" if expand else "blockquote"
    return f"<{tag}>" + "\n".join(lines) + "</blockquote>"


def kb(uid):
    admin = uid == OWNER_ID
    rows = [
        [rbtn(B_JVC, "success"), rbtn(B_LVC, "danger")],
        [rbtn(B_AR, "primary"), rbtn(B_AV, "primary")],
        [rbtn(B_JOIN, "success"), rbtn(B_REACT, "danger")],
        [rbtn(B_CLIENTS, "primary"), rbtn(B_ADD, "success")],
        [rbtn(B_STATUS, "primary"), rbtn(B_RESTART, "primary")],
        [rbtn(B_CUSTOM_INBOX, "success")],
        [rbtn(B_UPTIME, "primary")] + ([rbtn(B_FIND, "primary")] if admin else []),
        [rbtn(B_SETTINGS, "primary"), rbtn(B_INFO, "primary")],
        [rbtn(B_PING, "primary"), rbtn(B_CLEARAUTO, "danger")],
        [rbtn(B_ACC, "primary")] + ([rbtn(B_ADMIN, "danger"), rbtn(B_BROADCAST, "danger")] if admin else []),
    ] + ([[rbtn(B_ADMTOOLS, "primary"), rbtn(B_PIN, "primary"), rbtn(B_CACHE, "danger")]] if admin else []) + [
        [rbtn(B_DEV, "primary")],
    ]
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=rows)


def back_kb():
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[[rbtn(B_BACK, "danger")]])


class PremiumMW(BaseRequestMiddleware):
    """Turns plain emoji in outgoing messages into premium (custom) emoji.
    Self-healing: if Telegram rejects a saved emoji ID (ENTITY_TEXT_INVALID — usually
    means that id was invalid or revoked), the message is retried as plain text instead
    of crashing, and the bad id is dropped so it won't fail again."""
    async def __call__(self, make_request, bot, method):
        # GetMe, GetUpdates, and most other API calls have no .text attribute at all —
        # always use getattr() here, never method.text directly, or it crashes the whole bot.
        text_val = getattr(method, "text", None)
        original = text_val if isinstance(text_val, str) else None
        used_ids = []
        if PREMIUM and type(method).__name__ in ("SendMessage", "EditMessageText") and isinstance(text_val, str):
            pat = re.compile("(" + "|".join(re.escape(k) for k in sorted(PREMIUM, key=len, reverse=True)) + ")\ufe0f?")

            def _sub(mo):
                emoji, cid = mo.group(1), PREMIUM[mo.group(1)]
                used_ids.append((emoji, cid))
                return f'<tg-emoji emoji-id="{cid}">{emoji}</tg-emoji>'

            method.text = pat.sub(_sub, text_val)
        try:
            return await make_request(bot, method)
        except TelegramBadRequest as e:
            if "ENTITY_TEXT_INVALID" in str(e) and original is not None:
                for emoji, cid in used_ids:                 # drop every bad id so it stops breaking messages
                    if PREMIUM.get(emoji) == cid:
                        PREMIUM.pop(emoji, None)
                        db.execute("DELETE FROM premium WHERE emoji=? AND cid=?", (emoji, cid))
                db.commit()
                method.text = original                       # resend this one message as plain text, unaffected
                return await make_request(bot, method)
            raise


_BOLD_MAP = {}
for _i, _c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
    _BOLD_MAP[_c] = chr(0x1D400 + _i)          # 𝗔-𝗭  (Mathematical Bold Capital)
for _i, _c in enumerate("abcdefghijklmnopqrstuvwxyz"):
    _BOLD_MAP[_c] = chr(0x1D41A + _i)          # 𝗮-𝘇  (Mathematical Bold Small)
for _i, _c in enumerate("0123456789"):
    _BOLD_MAP[_c] = chr(0x1D7CE + _i)          # 𝟬-𝟵  (Mathematical Bold Digit)
_TAG_RE = re.compile(r"(<[^>]+>)")
_DEV_ESC = html.escape(DEV_CONTACT) if DEV_CONTACT else ""
# everything that must NEVER be bold-converted: real HTML tags + the developer contact text itself
_PROTECT_RE = re.compile(r"(<[^>]+>|@[A-Za-z0-9_]{1,32}" + (r"|" + re.escape(_DEV_ESC) if _DEV_ESC else "") + r")")


def to_bold(text):
    """Turns plain A-Z/a-z/0-9 into real Unicode bold glyphs (𝗕𝗢𝗟𝗗), WITHOUT touching
    HTML tags (<b>, <code>, <blockquote>...) or the developer contact text, so those always
    stay exactly as typed whether bold mode is ON or OFF. Other scripts (Bengali, emoji,
    punctuation) are left as-is since there is no bold Unicode variant for them."""
    parts = _PROTECT_RE.split(text)
    out = []
    for part in parts:
        if (part.startswith("<") and part.endswith(">")) or (_DEV_ESC and part == _DEV_ESC):
            out.append(part)                  # leave tags AND the dev contact untouched
        else:
            out.append("".join(_BOLD_MAP.get(ch, ch) for ch in part))
    return "".join(out)


class BoldMW(BaseRequestMiddleware):
    """Turns outgoing message text into real bold Unicode characters (𝗕𝗢𝗟𝗗). Toggle with /boldon /boldoff.
    OFF = perfectly normal text, exactly as before."""
    async def __call__(self, make_request, bot, method):
        if BOLD_MODE[0] and type(method).__name__ in ("SendMessage", "EditMessageText") and isinstance(method.text, str) and method.text.strip():
            method.text = to_bold(method.text)
        return await make_request(bot, method)


class S(StatesGroup):
    phone = State(); otp = State(); pwd = State(); qr = State()
    join = State(); jvc = State(); lvc = State()
    react_link = State(); react_emoji = State()
    ar_chat = State(); ar_emoji = State(); av_chat = State()
    vip_key = State(); emoji_pack = State(); broadcast = State(); find_user = State(); pin_post = State()
    emoji_one = State()
    custom_target = State(); custom_payload = State()


# ---------- access: request -> admin accepts ----------
ADMIN_ONLY_TEXT = {B_ADMIN, B_ADMIN.split(" ", 1)[1], B_BROADCAST, B_BROADCAST.split(" ", 1)[1],
                   B_FIND, B_FIND.split(" ", 1)[1], B_ADMTOOLS, B_ADMTOOLS.split(" ", 1)[1],
                   B_PIN, B_PIN.split(" ", 1)[1], B_CACHE, B_CACHE.split(" ", 1)[1]}
VIP_BTN = ikb([("🔑 Enter VIP Access Key", "vip_enter", "primary")])
ACCESS_KEY_PROMPT = (
    "<b>𝐇𝐚𝐯𝐞 𝐚 𝐕𝐈𝐏 𝐤𝐞𝐲? 𝐄𝐧𝐭𝐞𝐫 𝐢𝐭 𝐛𝐞𝐥𝐨𝐰.</b>\n\n"
    f"<b>DM FOR KEY:</b>{h(DEV_CONTACT)}\n\n"
    "⏳ <b>𝐘𝐨𝐮𝐫 𝐚𝐜𝐜𝐞𝐬𝐬 𝐫𝐞𝐪𝐮𝐞𝐬𝐭 𝐢𝐬 𝐰𝐚𝐢𝐭𝐢𝐧𝐠 𝐟𝐨𝐫 𝐭𝐡𝐞 𝐨𝐰𝐧𝐞𝐫'𝐬 𝐚𝐩𝐩𝐫𝐨𝐯𝐚𝐥.</b>"
)


class Guard(BaseMiddleware):
    async def __call__(self, handler, event, data):
        u = event.from_user
        if not u:
            return
        is_msg = isinstance(event, Message)

        async def say(t, markup=None):
            if is_msg:
                await event.answer(t, reply_markup=markup)
            else:
                await event.answer(re.sub(r"<[^>]+>", "", t)[:190], show_alert=True)

        if u.id == OWNER_ID:
            return await handler(event, data)
        # Let /start reach its handler so the welcome card is always shown first.
        if is_msg and event.text and event.text.split()[0].split("@")[0] == "/start":
            return await handler(event, data)
        # key entry is allowed for everyone (so a key holder can get in)
        row = db.execute("SELECT status FROM users WHERE user_id=?", (u.id,)).fetchone()
        st = row[0] if row else None
        if st == "banned":
            return await say("⛔ You are permanently banned from this bot by the owner.")
        st_now = await data["state"].get_state() if data.get("state") else None
        if st_now == S.vip_key.state or (not is_msg and (event.data or "") == "vip_enter"):
            return await handler(event, data)
        if st == "approved":
            if is_msg and event.text in ADMIN_ONLY_TEXT:
                return await say("⛔ Admin only.")
            if not is_msg and (event.data or "").startswith("adm"):
                return await say("⛔ Admin only.")
            return await handler(event, data)
        if st == "rejected":
            return await say("❌ Your access request was rejected by the bot owner.\n\n" + ACCESS_KEY_PROMPT, VIP_BTN)
        if st == "revoked":
            return await say("🚫 Your access has been revoked by the bot owner.\n\n" + ACCESS_KEY_PROMPT, VIP_BTN)
        if st == "pending":
            return await say("⏳ Your request is still waiting for the owner's approval.\n\n" + ACCESS_KEY_PROMPT, VIP_BTN)
        # new user -> send request to admin
        db.execute("INSERT OR REPLACE INTO users(user_id,name,username,status) VALUES(?,?,?,?)",
                   (u.id, u.full_name, u.username or "", "pending"))
        db.commit()
        await say("🔐 Bot authorization request sent!\n\nWaiting for the bot owner to approve you. You'll be notified here.\n\n" + ACCESS_KEY_PROMPT, VIP_BTN)
        try:
            uname = f"@{h(u.username)}" if u.username else "—"
            await data["bot"].send_message(
                OWNER_ID,
                f"🔔 New access request\n\n👤 Name: {h(u.full_name)}\n🔗 Username: {uname}\n🆔 ID: {u.id}",
                reply_markup=ikb([("✅ Accept", f"adm:ap:{u.id}", "success"), ("❌ Reject", f"adm:rj:{u.id}", "danger")]))
        except Exception as e:
            log.warning("owner notify failed: %r", e)


class ActivityAuditMW(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user:
            if isinstance(event, Message):
                raw = (event.text or event.caption or "").strip()
                # Keep only command/button labels; never store arbitrary user message content.
                label = raw.splitlines()[0][:100] if raw.startswith("/") or raw in {
                    B_CLIENTS, B_ADD, B_JVC, B_LVC, B_JOIN, B_REACT, B_AR, B_AV,
                    B_ACC, B_ADMIN, B_STATUS, B_RESTART, B_UPTIME, B_FIND,
                    B_SETTINGS, B_INFO, B_PING, B_CLEARAUTO, B_BROADCAST,
                    B_ADMTOOLS, B_PIN, B_CACHE, B_DEV, B_CUSTOM_INBOX
                } else ("media/message" if event.photo or event.video or event.document or event.voice else "")
                if label:
                    log_activity(user.id, "interaction", label)
            elif isinstance(event, CallbackQuery) and event.data:
                log_activity(user.id, "button", event.data[:100])
        return await handler(event, data)


dp = Dispatcher(storage=MemoryStorage())
dp.message.outer_middleware(ActivityAuditMW())
dp.callback_query.outer_middleware(ActivityAuditMW())
dp.message.outer_middleware(Guard())
dp.callback_query.outer_middleware(Guard())

# ---------- self-healing / global error reporting ----------
_ERROR_ALERT_COOLDOWN = {}
_ERROR_ALERT_WINDOW = 60  # seconds; prevent alert floods


def _safe_error_text(exc):
    """Keep diagnostics useful without leaking tokens, session strings or credentials."""
    raw = f"{type(exc).__name__}: {exc}"
    raw = re.sub(r"(?i)(bot\d+:[A-Za-z0-9_-]{20,})", "[BOT_TOKEN_REDACTED]", raw)
    raw = re.sub(r"(?i)(api_hash|api_key|password|session_string|enc_key)\s*[=:]\s*\S+", r"\1=[REDACTED]", raw)
    return raw[:1200]


async def _notify_error(bot, user_id, exc, where="update"):
    now = time.time()
    key = (user_id, type(exc).__name__)
    if now - _ERROR_ALERT_COOLDOWN.get(key, 0) < _ERROR_ALERT_WINDOW:
        return
    _ERROR_ALERT_COOLDOWN[key] = now
    details = _safe_error_text(exc)
    try:
        await bot.send_message(
            OWNER_ID,
            "🚨 <b>Bot error alert</b>\n"
            f"📍 জায়গা: <code>{h(where)}</code>\n"
            f"👤 User ID: <code>{user_id or 'unknown'}</code>\n"
            f"⚠️ সমস্যা: <code>{h(details)}</code>\n"
            "🛠️ স্বয়ংক্রিয় recovery চেষ্টা করা হয়েছে যেখানে নিরাপদ।"
        )
    except Exception:
        log.exception("could not send error alert to owner")


@dp.errors()
async def global_error_handler(event: ErrorEvent):
    """Catch otherwise-unhandled aiogram update errors and inform user + owner."""
    exc = event.exception
    log.exception("unhandled Telegram update error", exc_info=(type(exc), exc, exc.__traceback__))
    update = event.update
    message = update.message or update.edited_message
    callback = update.callback_query
    user_id = None
    bot = None
    if message:
        user_id = message.from_user.id if message.from_user else None
        bot = message.bot
        try:
            await message.answer("⚠️ দুঃখিত! বটে সাময়িক সমস্যা হয়েছে। সমস্যাটি অ্যাডমিনকে জানানো হয়েছে। কিছুক্ষণ পর আবার চেষ্টা করুন।")
        except Exception:
            pass
    elif callback:
        user_id = callback.from_user.id
        bot = callback.bot
        try:
            await callback.answer("⚠️ সাময়িক সমস্যা হয়েছে। অ্যাডমিনকে জানানো হয়েছে।", show_alert=True)
        except Exception:
            pass
    if bot:
        await _notify_error(bot, user_id, exc, "aiogram update")
    return True


async def self_healing_supervisor(bot):
    """Recover persisted user clients; one bad DB row/client never kills the monitor."""
    while True:
        try:
            rows = db.execute("SELECT id, session, owner FROM clients").fetchall()
            for tg_id, encrypted_session, owner in rows:
                # A stale RUNNING entry must not hide a disconnected Telegram client.
                current = RUNNING.get(tg_id)
                if current is not None and getattr(current[0], "is_connected", False):
                    continue
                if current is not None:
                    log.warning("client %s is disconnected; cleaning up before recovery", tg_id)
                    await stop_client(tg_id)
                try:
                    session = fernet.decrypt(encrypted_session).decode()
                    await start_client(session, tg_id, owner or OWNER_ID)
                    log.info("self-healing: client %s recovered", tg_id)
                    try:
                        await bot.send_message(OWNER_ID, f"✅ Auto-recovery সফল: Client <code>{tg_id}</code> আবার চালু হয়েছে।")
                    except Exception:
                        log.warning("owner recovery notification failed for client %s", tg_id)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.exception("self-healing failed for client %s", tg_id)
                    await _notify_error(bot, OWNER_ID, exc, f"client recovery {tg_id}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("self-healing supervisor cycle failed")
            await _notify_error(bot, OWNER_ID, exc, "self-healing supervisor")
        await asyncio.sleep(20)


async def _run_guarded_background(name, factory, bot):
    """Keep essential background jobs alive if an unexpected exception escapes."""
    delay = 5
    while True:
        try:
            log.info("background task starting: %s", name)
            await factory()
            # These jobs are designed to run forever; an ordinary return is unexpected.
            raise RuntimeError(f"background task {name} stopped unexpectedly")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("background task crashed: %s", name)
            await _notify_error(bot, OWNER_ID, exc, f"background task {name}")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 120)
        else:
            delay = 5


async def _runtime_health_monitor(bot):
    """Check database and Telegram clients; recovery is delegated to supervisor."""
    while True:
        try:
            db.execute("SELECT 1").fetchone()
            disconnected = [tid for tid, (app, _calls, _owner) in list(RUNNING.items())
                            if not getattr(app, "is_connected", False)]
            for tid in disconnected:
                exc = ConnectionError(f"Telegram client {tid} is disconnected; supervisor will retry")
                await _notify_error(bot, OWNER_ID, exc, "client health check")
            cutoff = time.time() - (_ERROR_ALERT_WINDOW * 4)
            for key, stamp in list(_ERROR_ALERT_COOLDOWN.items()):
                if stamp < cutoff:
                    _ERROR_ALERT_COOLDOWN.pop(key, None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("runtime health check failed")
            await _notify_error(bot, OWNER_ID, exc, "runtime health check")
        await asyncio.sleep(30)


# ---------- basic ----------
@dp.message(CommandStart())
async def start(m: Message, state: FSMContext):
    # New/unapproved users see ONLY the VIP key prompt first.
    # Welcome text and the main menu are shown after approval/key activation
    # when the user sends /start again.
    await state.clear()
    uid = m.from_user.id

    if uid != OWNER_ID:
        row = db.execute("SELECT status FROM users WHERE user_id=?", (uid,)).fetchone()
        status = row[0] if row else None

        if status == "banned":
            return await m.answer("⛔ You are permanently banned from this bot by the owner.")

        if status != "approved":
            # Register first-time users and notify the owner once.
            if status is None:
                db.execute(
                    "INSERT OR REPLACE INTO users(user_id,name,username,status) VALUES(?,?,?,?)",
                    (uid, m.from_user.full_name, m.from_user.username or "", "pending")
                )
                db.commit()
                try:
                    uname = f"@{h(m.from_user.username)}" if m.from_user.username else "—"
                    await m.bot.send_message(
                        OWNER_ID,
                        f"🔔 New access request\n\n👤 Name: {h(m.from_user.full_name)}\n🔗 Username: {uname}\n🆔 ID: {uid}",
                        reply_markup=ikb([
                            ("✅ Accept", f"adm:ap:{uid}", "success"),
                            ("❌ Reject", f"adm:rj:{uid}", "danger")
                        ])
                    )
                except Exception as e:
                    log.warning("owner notify failed: %r", e)

            # No welcome message or main menu until the user is approved.
            return await m.answer(ACCESS_KEY_PROMPT, reply_markup=VIP_BTN)

    # Approved users and the owner see the welcome message first, then menu.
    await m.answer(WELCOME_TEXT)
    plan = "👑 Admin" if uid == OWNER_ID else ("💎 VIP" if is_vip(uid) else "🔐 Member")
    total, online = len(client_rows(uid)), len(user_clients(uid))
    await m.answer(hd("🎧", "Voice control", f"welcome, {h(m.from_user.first_name)}") +
                   tree(f"{plan}", f"📱 Accounts — <b>{total}</b>", f"🟢 Online — <b>{online}</b>") +
                   "\n\nChoose an action from the menu 👇", reply_markup=kb(uid))


@dp.message(Command("cancel"))
@dp.message(ANY(B_BACK))
async def cancel(m: Message, state: FSMContext):
    p = PENDING.pop(m.from_user.id, None)
    if p:
        t = p.get("task")
        if t and not t.done():
            t.cancel()
        try: await p["app"].disconnect()
        except Exception: pass
    await state.clear()
    await m.answer("❎ Cancelled.", reply_markup=kb(m.from_user.id))


@dp.message(Command("boldon"))
async def boldon(m: Message):
    if m.from_user.id != OWNER_ID:
        return
    BOLD_MODE[0] = True
    db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('bold','1')"); db.commit()
    await m.answer("🔛 Bold messages: ON")


@dp.message(Command("boldoff"))
async def boldoff(m: Message):
    if m.from_user.id != OWNER_ID:
        return
    BOLD_MODE[0] = False
    db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('bold','0')"); db.commit()
    await m.answer("🔚 Bold messages: OFF")


@dp.message(Command("setemoji"))
async def setemoji(m: Message, state: FSMContext):
    if m.from_user.id != OWNER_ID:
        return
    await state.set_state(S.emoji_pack)
    lst = "\n".join(f"{i}. {e}  {d}" for i, (e, d) in enumerate(SLOTS, 1))
    await m.answer(hd("💎", "Premium emoji setup", "owner account needs Telegram Premium") +
                   "Send ONE message with premium emoji in exactly this order (spaces between are fine):\n\n"
                   f"{lst}\n\nSend fewer to set only the first ones. /cancel to abort.", reply_markup=back_kb())


@dp.message(S.emoji_pack)
async def setemoji_do(m: Message, state: FSMContext):
    ents = [e for e in (m.entities or []) if e.type == "custom_emoji"]
    if not ents:
        return await m.answer("❌ No premium emoji found in that message. Send them again, or /cancel.")
    for e, (slot, _d) in zip(ents, SLOTS):
        PREMIUM[norm(slot)] = e.custom_emoji_id
        db.execute("INSERT OR REPLACE INTO premium(emoji,cid) VALUES(?,?)", (norm(slot), e.custom_emoji_id))
    db.commit()
    await state.clear()
    n = min(len(ents), len(SLOTS))
    await m.answer(hd("🎉", "Premium emoji saved", f"{n} of {len(SLOTS)} set") + "Menu and messages now use them.", reply_markup=kb(m.from_user.id))


@dp.message(Command("setpack"))
async def setpack(m: Message):
    if m.from_user.id != OWNER_ID:
        return
    args = (m.text or "").split()[1:]
    names = [re.sub(r".*/(?:addemoji|addstickers)/", "", a).strip("/ ") for a in args]
    if not names:
        return await m.answer(hd("💎", "Premium emoji pack", "one link = whole bot turns premium") +
                              "Send:\n<code>/setpack https://t.me/addemoji/PackName</code>\n\n"
                              "How to get a link: tap any premium emoji → tap its pack name → Share → Copy link.\n"
                              "You can give several packs, separated by a space.")
    by_emoji, pool, errs = {}, [], []
    for n in names:
        try:
            ss = await m.bot.get_sticker_set(n)
        except Exception as e:
            errs.append(f"{h(n)}: {h(e)}")
            continue
        for st in ss.stickers:
            cid = getattr(st, "custom_emoji_id", None)
            if cid:
                by_emoji.setdefault(norm(st.emoji or ""), cid)
                if cid not in pool:
                    pool.append(cid)
    if not pool:
        return await m.answer("❌ Could not read that pack (it must be a custom EMOJI pack).\n" + "\n".join(errs))
    chosen = {}
    for slot, _d in SLOTS:                       # 1) exact emoji match
        if norm(slot) in by_emoji:
            chosen[norm(slot)] = by_emoji[norm(slot)]
    exact = len(chosen)
    spare = [c for c in pool if c not in chosen.values()]
    for slot, _d in SLOTS:                       # 2) fill the rest from unused pack emoji
        if norm(slot) not in chosen and spare:
            chosen[norm(slot)] = spare.pop(0)
    for e, cid in by_emoji.items():              # 3) every other emoji the pack covers
        if e and e not in chosen:
            chosen[e] = cid
    for e, cid in chosen.items():
        PREMIUM[e] = cid
        db.execute("INSERT OR REPLACE INTO premium(emoji,cid) VALUES(?,?)", (e, cid))
    db.commit()
    await m.answer(hd("🎉", "Premium pack applied", f"{len(chosen)} emoji ready") +
                   f"💎 Exact matches — <b>{exact}</b>\n⚡ Filled from pack — <b>{len(chosen) - exact}</b>\n\n"
                   "Menu + all messages now use them.", reply_markup=kb(m.from_user.id))


@dp.message(Command("resetemoji"))
async def resetemoji(m: Message):
    if m.from_user.id != OWNER_ID:
        return
    PREMIUM.clear()
    db.execute("DELETE FROM premium"); db.commit()
    await m.answer("♻️ Premium emoji cleared.", reply_markup=kb(m.from_user.id))


@dp.message(Command("emojiid"))
async def emojiid_help(m: Message):
    if m.from_user.id == OWNER_ID:
        await m.answer("Send me any premium emoji and I'll reply with its ID.")


def has_custom_emoji(m: Message):
    return m.from_user.id == OWNER_ID and any(e.type == "custom_emoji" for e in (m.entities or []))


@dp.message(StateFilter(None), has_custom_emoji)
async def emoji_ids(m: Message):
    lines = []
    for e in m.entities:
        if e.type == "custom_emoji":
            ch = e.extract_from(m.text)
            lines.append(f'{h(ch)} → <code>{e.custom_emoji_id}</code>')
    await m.answer("\n".join(lines) + '\n\nSaved automatically via /setemoji or /setpack — no code edit needed.')


# ---------- My Clients / Add Client ----------
def client_rows(uid):
    if uid == OWNER_ID:
        return db.execute("SELECT id, name, phone, owner FROM clients ORDER BY id").fetchall()
    if is_vip(uid):
        return db.execute("SELECT id, name, phone, owner FROM clients WHERE owner IN (?,?) ORDER BY id", (uid, OWNER_ID)).fetchall()
    return db.execute("SELECT id, name, phone, owner FROM clients WHERE owner=? ORDER BY id", (uid,)).fetchall()


@dp.message(ANY(B_CLIENTS))
async def my_clients(m: Message):
    uid = m.from_user.id
    rows = client_rows(uid)
    if not rows:
        return await m.answer(hd("📱", "Accounts", "nothing here yet") + "Tap <b>➕ Add Account</b> to log one in.")
    lines = []
    for i, (c, n, p, o) in enumerate(rows, 1):
        shared = uid != OWNER_ID and o == OWNER_ID   # admin's account seen by a VIP user
        l = f"{i}. {'🟢' if c in RUNNING else '🔴'} {h(n)} | 📞 {'hidden' if shared else h(p)} | 🆔 {c}"
        if uid == OWNER_ID:
            l += f" | 👥 {h(owner_name(o))}"
        elif shared:
            l += " | 🔒 VIP"
        lines.append(l)
    body = "\n".join(lines)
    if len(body) > 3500:
        body = body[:3500] + "\n…"
    on = sum(1 for r in rows if r[0] in RUNNING)
    await m.answer(hd("📱", f"Accounts · {len(rows)}", f"🟢 {on} online · 🔴 {len(rows) - on} offline") +
                   f"<blockquote expandable>{body}</blockquote>\n\n🧹 <code>/remove 3</code>")


@dp.message(ANY(B_ADD))
async def add_client_btn(m: Message):
    await m.answer(
        hd("➕", "Add account", "phone number + OTP, or QR (no code needed)") + "Tap a button to start.",
        reply_markup=ikb([("📱 Add by Phone Number", "add_phone", "primary")],
                         [("🔳 Add by QR (any country)", "add_qr", "primary")]))


def login_client(uid):
    return Client(f"login{uid}", api_id=API_ID, api_hash=API_HASH, in_memory=True, proxy=_proxy(),
                  device_model="Samsung Galaxy S23", system_version="Android 13",
                  app_version="10.14.5", lang_code="en")


def code_where(sent):
    t = str(getattr(sent.type, "name", sent.type))
    n = str(getattr(sent.next_type, "name", sent.next_type)) if sent.next_type else None
    where = {"APP": "📲 Telegram app (the 'Telegram' service chat on any device logged in)",
             "SMS": "💬 SMS", "CALL": "📞 phone call", "FLASH_CALL": "📞 flash call",
             "MISSED_CALL": "📞 missed call (last digits)", "FRAGMENT_SMS": "💎 Fragment SMS",
             "FIREBASE_SMS": "💬 SMS", "EMAIL_CODE": "📧 email"}.get(t, t)
    txt = f"Code sent via: {where}"
    if n:
        txt += f"\nNext option after /resend: {n}"
    return txt


@dp.callback_query(F.data == "add_phone")
async def add_phone(c: CallbackQuery, state: FSMContext):
    await state.set_state(S.phone)
    await c.message.answer(
        "📱 Add by Phone Number\n\nSend the Telegram account phone number with country code, e.g. +1234567890.\n\n"
        "Tap 🔙 Back to cancel, or send /cancel to abort.", reply_markup=back_kb())
    await c.answer()


@dp.callback_query(F.data == "add_qr")
async def add_qr(c: CallbackQuery, state: FSMContext):
    uid = c.from_user.id
    old = PENDING.pop(uid, None)
    if old:
        t = old.get("task")
        if t and not t.done():
            t.cancel()
        try: await old["app"].disconnect()
        except Exception: pass
    await state.set_state(S.qr)
    await c.answer()
    app = login_client(uid)
    try:
        await app.connect()
    except Exception as e:
        return await c.message.answer(f"❌ Connect failed: {h(e.__class__.__name__)}: {h(e)}")
    p = dict(app=app, phone="QR", hash=None)
    PENDING[uid] = p
    p["task"] = asyncio.create_task(qr_flow(c.message, state, p, uid))
    await c.message.answer(
        "🔳 <b>QR login</b>\n\nA QR image is coming. On the phone where this account is already logged in:\n"
        "Telegram → Settings → Devices → <b>Link Desktop Device</b> → scan the QR.\n\n"
        "No SMS / OTP needed, works for any country.\nSend /cancel to abort.", reply_markup=back_kb())


async def qr_flow(msg: Message, state: FSMContext, p, uid):
    import qrcode
    app = p["app"]
    try:
        ql = QRLogin(app, [])
        await ql.recreate()
        for _ in range(8):                      # ~8 QR refreshes (each QR lives ~30s)
            img = qrcode.make(ql.url)
            buf = io.BytesIO(); img.save(buf, format="PNG")
            photo = await msg.answer_photo(BufferedInputFile(buf.getvalue(), "qr.png"),
                                           caption="📷 Scan this QR (expires in ~30s, a new one will follow)")
            try:
                await ql.wait()
                break
            except (asyncio.TimeoutError, AuthTokenExpired):
                try: await photo.delete()
                except Exception: pass
                await ql.recreate()
            except SessionPasswordNeeded:
                await state.set_state(S.pwd)
                return await msg.answer("🔐 2-Step Verification is enabled.\n\nSend your account password.")
        else:
            raise TimeoutError("QR not scanned in time")
        await finish_login(msg, state, p, uid)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        PENDING.pop(uid, None)
        try: await app.disconnect()
        except Exception: pass
        await state.clear()
        await msg.answer(f"❌ QR login failed: {h(e.__class__.__name__)}: {h(e)}", reply_markup=kb(uid))


@dp.message(Command("ban"))
async def ban_cmd(m: Message):
    if m.from_user.id != OWNER_ID:
        return
    args = (m.text or "").split()[1:]
    if not args or not args[0].lstrip("-").isdigit():
        return await m.answer("Usage: /ban USER_ID\n(get the ID from 👥 Users in the admin panel, or from a forwarded message)")
    uid = int(args[0])
    if uid == OWNER_ID:
        return await m.answer("❌ You can't ban yourself.")
    db.execute("INSERT INTO users(user_id,name,username,status) VALUES(?,?,?,'banned') "
               "ON CONFLICT(user_id) DO UPDATE SET status='banned'", (uid, str(uid), ""))
    db.execute("DELETE FROM vip_users WHERE user_id=?", (uid,))
    db.commit()
    try:
        await m.bot.send_message(uid, "⛔ You have been permanently banned from this bot by the owner.")
    except Exception:
        pass
    await m.answer(f"🚫 User {uid} banned.")


@dp.message(Command("unban"))
async def unban_cmd(m: Message):
    if m.from_user.id != OWNER_ID:
        return
    args = (m.text or "").split()[1:]
    if not args or not args[0].lstrip("-").isdigit():
        return await m.answer("Usage: /unban USER_ID")
    uid = int(args[0])
    row = db.execute("SELECT status FROM users WHERE user_id=?", (uid,)).fetchone()
    if not row or row[0] != "banned":
        return await m.answer("That user isn't banned.")
    db.execute("UPDATE users SET status='rejected' WHERE user_id=?", (uid,))  # back to normal (must request access again)
    db.commit()
    try:
        await m.bot.send_message(uid, "✅ Your ban has been lifted. Send /start to request access again.")
    except Exception:
        pass
    await m.answer(f"✅ User {uid} unbanned.")


@dp.message(Command("deleteuser"))
async def deleteuser_cmd(m: Message):
    # Permanent user cleanup; owner account is protected.
    if m.from_user.id != OWNER_ID:
        return
    args = (m.text or "").split()[1:]
    if not args or not args[0].lstrip("-").isdigit():
        return await m.answer("Usage: /deleteuser USER_ID\nExample: /deleteuser 123456789")

    uid = int(args[0])
    if uid == OWNER_ID:
        return await m.answer("❌ Owner account cannot be deleted.")

    exists = db.execute(
        "SELECT 1 FROM users WHERE user_id=? UNION SELECT 1 FROM clients WHERE owner=? "
        "UNION SELECT 1 FROM vip_users WHERE user_id=? UNION SELECT 1 FROM auto2 WHERE owner=? LIMIT 1",
        (uid, uid, uid, uid)
    ).fetchone()
    if not exists:
        return await m.answer("ℹ️ এই User ID-এর কোনো রেকর্ড পাওয়া যায়নি।")

    # Stop all active Telegram clients owned by this user before deleting DB rows.
    client_ids = [r[0] for r in db.execute("SELECT id FROM clients WHERE owner=?", (uid,)).fetchall()]
    for tg_id, (_app, _calls, owner) in list(RUNNING.items()):
        if owner == uid and tg_id not in client_ids:
            client_ids.append(tg_id)
    for tg_id in client_ids:
        await stop_client(tg_id)

    # Cancel any unfinished phone/QR login session for this bot user.
    pending = PENDING.pop(uid, None)
    if pending:
        task = pending.get("task")
        if task and not task.done():
            task.cancel()
        try:
            await pending["app"].disconnect()
        except Exception:
            pass

    try:
        db.execute("BEGIN")
        db.execute("DELETE FROM clients WHERE owner=?", (uid,))
        db.execute("DELETE FROM auto2 WHERE owner=?", (uid,))
        db.execute("DELETE FROM vip_users WHERE user_id=?", (uid,))
        # Keep key history, but revoke keys previously assigned to this user.
        db.execute("UPDATE access_keys SET status='revoked', used_by=NULL WHERE used_by=?", (uid,))
        db.execute("DELETE FROM users WHERE user_id=?", (uid,))
        db.commit()
    except Exception:
        db.rollback()
        log.exception("deleteuser failed for %s", uid)
        return await m.answer("❌ ইউজার ডিলিট করা যায়নি। লগ চেক করো।")

    for key in [k for k in list(AUTO_REACT) if k[0] == uid]:
        AUTO_REACT.pop(key, None)
    for key in [k for k in list(AUTO_VIEW) if k[0] == uid]:
        AUTO_VIEW.discard(key)
    for key in [k for k in list(SEEN) if k[0] in client_ids]:
        SEEN.pop(key, None)

    await m.answer(f"🗑️ User <code>{uid}</code> এবং তার সংরক্ষিত ডেটা সফলভাবে ডিলিট করা হয়েছে।")


@dp.message(Command("remove"))
async def remove_cmd(m: Message):
    rows = client_rows(m.from_user.id)
    nums = [int(x) for x in (m.text or "").split()[1:] if x.isdigit()]
    nums = [n for n in nums if 1 <= n <= len(rows)]
    if not nums:
        return await m.answer("Usage: /remove NUMBER\nUse the number from the 📱 Accounts list. Several at once: /remove 2 5 7")
    names = []
    for n in sorted(set(nums)):
        cid, name, _p, own = rows[n - 1]
        if m.from_user.id != OWNER_ID and own != m.from_user.id:
            continue   # VIP users can't remove the admin's accounts
        await stop_client(cid)
        db.execute("DELETE FROM clients WHERE id=?", (cid,))
        names.append(h(name))
    db.commit()
    await m.answer("🗑 Removed: " + ", ".join(names))


@dp.message(S.phone)
async def got_phone(m: Message, state: FSMContext):
    phone = m.text.strip().replace(" ", "").replace("-", "")
    if not phone.startswith("+"):
        phone = "+" + phone
    old = PENDING.pop(m.from_user.id, None)
    if old:
        try: await old["app"].disconnect()
        except Exception: pass
    app = login_client(m.from_user.id)
    try:
        await app.connect()
        sent = await app.send_code(phone)
    except FloodWait as e:
        try: await app.disconnect()
        except Exception: pass
        return await m.answer(f"⏳ Telegram says wait {e.value} seconds ({e.value // 60} min) before asking a code for this number. Do NOT retry until then.")
    except Exception as e:
        try: await app.disconnect()
        except Exception: pass
        log.exception("send_code failed")
        return await m.answer(f"❌ Could not send code: {h(e.__class__.__name__)}: {h(e)}\nTry again or tap 🔙 Back.")
    PENDING[m.from_user.id] = dict(app=app, phone=phone, hash=sent.phone_code_hash)
    await state.set_state(S.otp)
    await m.answer(
        f"🔑 Code requested for {h(phone)}\n\n{h(code_where(sent))}\n\n"
        "Type the code with dashes so Telegram doesn't expire it: <code>1-2-3-4-5</code>\n"
        "Code not arriving? Send /resend to try the next method (SMS / call).\n\n"
        "Tap 🔙 Back to cancel, or send /cancel to abort.")


@dp.message(Command("resend"), S.otp)
async def resend_code_cmd(m: Message):
    p = PENDING.get(m.from_user.id)
    if not p:
        return await m.answer("❌ No login in progress. Send the phone number again.")
    try:
        sent = await p["app"].resend_code(p["phone"], p["hash"])
    except FloodWait as e:
        return await m.answer(f"⏳ Wait {e.value} seconds before resending.")
    except Exception as e:
        return await m.answer(f"❌ Resend failed: {h(e.__class__.__name__)}: {h(e)}")
    p["hash"] = sent.phone_code_hash
    await m.answer(f"🔁 New code requested.\n\n{h(code_where(sent))}\n\nType it like <code>1-2-3-4-5</code>.")


async def finish_login(m: Message, state: FSMContext, p, uid=None):
    uid = uid or m.from_user.id
    app = p["app"]
    session = await app.export_session_string()
    me = await app.get_me()
    await app.disconnect()
    name = " ".join(x for x in [me.first_name, me.last_name] if x) or "NoName"
    if p["phone"] == "QR":
        p["phone"] = ("+" + me.phone_number) if getattr(me, "phone_number", None) else "QR"
    db.execute("INSERT OR REPLACE INTO clients(id,name,phone,session,owner) VALUES(?,?,?,?,?)",
               (me.id, name, p["phone"], fernet.encrypt(session.encode()), uid))
    db.commit()
    log_activity(uid, "account_added", f"{name} · Telegram ID {me.id}")
    if uid != OWNER_ID:
        await notify_owner_activity(m.bot, m.from_user, "account_added",
                                    f"Account: {name} · Telegram ID: {me.id} · Phone: {p['phone']}")
    await start_client(session, me.id, uid)
    PENDING.pop(uid, None)
    await state.clear()
    await m.answer(f"✅ Account added!\n\n👤 Name: {h(name)}\n📞 Phone: {h(p['phone'])}\n🆔 ID: {me.id}",
                   reply_markup=kb(uid))


@dp.message(S.otp)
async def got_otp(m: Message, state: FSMContext):
    p = PENDING.get(m.from_user.id)
    code = "".join(re.findall(r"\d", m.text))
    if not p or len(code) < 5:
        return await m.answer("❌ Couldn't find a valid code. Send it again.")
    try:
        await p["app"].sign_in(p["phone"], p["hash"], code)
    except SessionPasswordNeeded:
        await state.set_state(S.pwd)
        return await m.answer("🔐 2-Step Verification is enabled.\n\nSend your account password.")
    except (PhoneCodeInvalid, PhoneCodeExpired) as e:
        return await m.answer(f"❌ {e.__class__.__name__}. Send the correct code, or /cancel and retry.")
    except Exception as e:
        return await m.answer(f"❌ Login failed: {h(e)}")
    await finish_login(m, state, p)


@dp.message(S.pwd)
async def got_pwd(m: Message, state: FSMContext):
    p = PENDING.get(m.from_user.id)
    try:
        await p["app"].check_password(m.text.strip())
    except PasswordHashInvalid:
        return await m.answer("❌ Wrong password. Try again.")
    except Exception as e:
        return await m.answer(f"❌ Login failed: {h(e)}")
    try: await m.delete()  # remove password from chat
    except Exception: pass
    await finish_login(m, state, p)


# ---------- Join Channel ----------
@dp.message(ANY(B_JOIN))
async def join_ask(m: Message, state: FSMContext):
    await state.set_state(S.join)
    await m.answer(
        hd("🚀", "Join channel") + "Send the username or invite link.\n\n"
        "• Public: @username or https://t.me/username\n• Private: https://t.me/+InviteHash\n\n"
        "All your clients will attempt to join.\n\nSend /cancel to abort.", reply_markup=back_kb())


@dp.message(S.join)
async def join_do(m: Message, state: FSMContext):
    target = parse_target(m.text)
    await state.clear()
    await m.answer("⏳ Joining...")
    ok, fail, secs = await for_all(m.from_user.id, lambda app, _: app.join_chat(target))
    await m.answer(report(ok, fail, secs, "Joined"), reply_markup=kb(m.from_user.id))


# ---------- Voice chat ----------
@dp.message(ANY(B_JVC))
async def jvc_ask(m: Message, state: FSMContext):
    await state.set_state(S.jvc)
    await m.answer(
        hd("🎧", "Voice join") + "Send the username or Chat ID.\n\n"
        "• Public: @username or https://t.me/username\n"
        "• Private: Chat ID (e.g. -1001234567890) or https://t.me/c/1234567890\n\n"
        "⚠️ For private groups/channels: use 🚀 Join Channel first, then come here with the Chat ID.\n"
        "⚠️ Invite links (t.me/+...) are not supported here — use the Chat ID instead.\n\nSend /cancel to abort.",
        reply_markup=back_kb())


@dp.message(S.jvc)
async def jvc_do(m: Message, state: FSMContext):
    chat = parse_target(m.text)
    if isinstance(chat, str) and chat.startswith("https://t.me/+"):
        return await m.answer("⚠️ Invite links aren't supported here. Send the Chat ID.")
    await state.clear()
    await m.answer("⏳ Joining voice chat...")

    async def fn(app, calls):
        await app.get_chat(chat)   # cache peer
        await calls.play(chat)     # no stream = silent listener

    ok, fail, secs = await for_all(m.from_user.id, fn)
    details = f"chat={chat}; success={ok}; failed={fail}"
    log_activity(m.from_user.id, "voice_join", details)
    await notify_owner_activity(m.bot, m.from_user, "voice_join", details)
    await m.answer(report(ok, fail, secs, "Joined VC"), reply_markup=kb(m.from_user.id))


@dp.message(ANY(B_LVC))
async def lvc_ask(m: Message, state: FSMContext):
    await state.set_state(S.lvc)
    await m.answer(hd("🔇", "Voice leave") + "Send the username or Chat ID.\n\nSend /cancel to abort.", reply_markup=back_kb())


@dp.message(S.lvc)
async def lvc_do(m: Message, state: FSMContext):
    chat = parse_target(m.text)
    await state.clear()

    async def fn(app, calls):
        await calls.leave_call(chat)

    ok, fail, secs = await for_all(m.from_user.id, fn)
    details = f"chat={chat}; success={ok}; failed={fail}"
    log_activity(m.from_user.id, "voice_leave", details)
    await notify_owner_activity(m.bot, m.from_user, "voice_leave", details)
    await m.answer(report(ok, fail, secs, "Left VC"), reply_markup=kb(m.from_user.id))


# ---------- Send Reaction ----------
@dp.message(ANY(B_REACT))
async def react_ask(m: Message, state: FSMContext):
    await state.set_state(S.react_link)
    await m.answer(hd("💫", "React") + "Send the post link, e.g. https://t.me/channel/123\n\nSend /cancel to abort.", reply_markup=back_kb())


@dp.message(S.react_link)
async def react_link(m: Message, state: FSMContext):
    post = parse_post(m.text)
    if not post:
        return await m.answer("❌ Invalid post link. Try again.")
    await state.update_data(post=post)
    await state.set_state(S.react_emoji)
    await m.answer("Now send the emoji to react with (e.g. 👍 💫 🔥).")


@dp.message(S.react_emoji)
async def react_do(m: Message, state: FSMContext):
    chat, mid = (await state.get_data())["post"]
    emoji = m.text.strip()
    await state.clear()

    async def fn(app, _):
        await app.get_chat(chat)
        await app.send_reaction(chat, mid, emoji)

    ok, fail, secs = await for_all(m.from_user.id, fn)
    await m.answer(report(ok, fail, secs, "Reacted"), reply_markup=kb(m.from_user.id))


# ---------- Auto React / Auto View ----------
def auto_list(uid):
    r = ", ".join(f"{c} {h(e)}" for (o, c), e in AUTO_REACT.items() if o == uid) or "none"
    v = ", ".join(str(c) for (o, c) in AUTO_VIEW if o == uid) or "none"
    return f"⚡ Auto React: {r}\n👀 Auto View: {v}"


async def resolve_chat_id(uid, target):
    if isinstance(target, int):
        return target
    for _, (app, _c, _o) in user_clients(uid):
        return (await app.get_chat(target)).id
    raise RuntimeError("No clients logged in")


@dp.message(ANY(B_AR))
async def ar_ask(m: Message, state: FSMContext):
    await state.set_state(S.ar_chat)
    await m.answer(hd("⚡", "Auto react") + f"{auto_list(m.from_user.id)}\n\nSend the channel username/Chat ID to enable.\n"
                   "Add <code>off</code> after it to disable, e.g. <code>@channel off</code>.\n\nSend /cancel to abort.",
                   reply_markup=back_kb())


@dp.message(S.ar_chat)
async def ar_chat(m: Message, state: FSMContext):
    uid = m.from_user.id
    off = m.text.strip().lower().endswith(" off")
    try:
        chat = await resolve_chat_id(uid, parse_target(m.text.rsplit(" ", 1)[0] if off else m.text))
    except Exception as e:
        return await m.answer(f"❌ {h(e)}")
    if off:
        AUTO_REACT.pop((uid, chat), None)
        db.execute("DELETE FROM auto2 WHERE owner=? AND kind='react' AND chat_id=?", (uid, str(chat))); db.commit()
        await state.clear()
        return await m.answer("⚡ Auto React disabled.", reply_markup=kb(uid))
    await state.update_data(chat=chat)
    await state.set_state(S.ar_emoji)
    await m.answer("Send the emoji to auto-react with.")


@dp.message(S.ar_emoji)
async def ar_emoji(m: Message, state: FSMContext):
    uid = m.from_user.id
    chat = (await state.get_data())["chat"]
    emoji = m.text.strip()
    AUTO_REACT[(uid, chat)] = emoji
    db.execute("INSERT OR REPLACE INTO auto2 VALUES(?,'react',?,?)", (uid, str(chat), emoji)); db.commit()
    await state.clear()
    await m.answer(f"✅ Auto React enabled for {chat} with {h(emoji)}", reply_markup=kb(uid))


@dp.message(ANY(B_AV))
async def av_ask(m: Message, state: FSMContext):
    await state.set_state(S.av_chat)
    await m.answer(hd("👀", "Auto view") + f"{auto_list(m.from_user.id)}\n\nSend the channel username/Chat ID to enable.\n"
                   "Add <code>off</code> to disable, e.g. <code>@channel off</code>.\n\nSend /cancel to abort.", reply_markup=back_kb())


@dp.message(S.av_chat)
async def av_chat(m: Message, state: FSMContext):
    uid = m.from_user.id
    off = m.text.strip().lower().endswith(" off")
    try:
        chat = await resolve_chat_id(uid, parse_target(m.text.rsplit(" ", 1)[0] if off else m.text))
    except Exception as e:
        return await m.answer(f"❌ {h(e)}")
    if off:
        AUTO_VIEW.discard((uid, chat))
        db.execute("DELETE FROM auto2 WHERE owner=? AND kind='view' AND chat_id=?", (uid, str(chat)))
    else:
        AUTO_VIEW.add((uid, chat))
        db.execute("INSERT OR REPLACE INTO auto2 VALUES(?,'view',?,'')", (uid, str(chat)))
    db.commit()
    await state.clear()
    await m.answer(f"👀 Auto View {'disabled' if off else 'enabled'} for {chat}", reply_markup=kb(uid))
    if off:
        for k in [k for k in SEEN if k[1] == chat and k[2] == "v"]:
            SEEN.pop(k, None)
        return
    ok = fail = 0
    err = ""
    for tg_id, (app, _c, _o) in user_clients(uid):
        try:
            SEEN[(tg_id, chat, "v")] = set(await recent_ids(app, chat))   # baseline: only NEW posts get views
            ok += 1
        except Exception as e:
            fail += 1
            err = repr(e)
    await m.answer(f"👀 Ready for new posts\n✅ OK: {ok} client(s)\n❌ Failed: {fail} client(s)"
                   + (f"\n\nError: {h(err)}\nMake sure these accounts joined the channel first." if err else
                      "\n\nEvery new post from now on will be viewed by all your clients."))


# ---------- CUSTOM SMS INBOX: one account OR all authorized connected accounts ----------
CUSTOM_INBOX_LAST_SEND = {}

@dp.message(ANY(B_CUSTOM_INBOX))
async def custom_inbox_start(m: Message, state: FSMContext):
    await state.clear()
    clients = user_clients(m.from_user.id)
    if not clients:
        return await m.answer(hd("📩", "Custom SMS Inbox") + "❌ আপনার নিয়ন্ত্রণে কোনো connected account online নেই। আগে Accounts থেকে account login করুন।", reply_markup=kb(m.from_user.id))
    rows = []
    for tg_id, (app, _calls, _owner) in clients[:20]:
        try:
            me = await app.get_me()
            label = f"{me.first_name or 'Account'} (@{me.username})" if me.username else f"{me.first_name or 'Account'} · {tg_id}"
        except Exception:
            label = f"Account · {tg_id}"
        rows.append([(label[:60], f"csi:account:{tg_id}", "primary")])
    rows.append([("🌐 ALL ACCOUNTS থেকে মেসেজ পাঠান", "csi:all", "success")])
    await m.answer(
        hd("📩", "Custom SMS Inbox", "choose one account or all accounts") +
        "কোন connected account থেকে মেসেজ পাঠাতে চান, সেটি বেছে নিন।\n\n"
        "👤 উপরের যেকোনো একটি account বেছে নিলে শুধু সেটি থেকে পাঠানো হবে।\n"
        "🌐 ALL ACCOUNTS বেছে নিলে আপনার অনুমোদিত সব connected account থেকে একই recipient-কে পাঠানো হবে।\n\n"
        "⚠️ শুধু সম্মত recipient-কে ব্যক্তিগত মেসেজ পাঠান। Telegram-এর rate limit/FloodWait হলে কোনো account থেকে পাঠানো ব্যর্থ হতে পারে।",
        reply_markup=ikb(*rows))

@dp.callback_query(F.data == "csi:all")
async def custom_inbox_choose_all(c: CallbackQuery, state: FSMContext):
    clients = user_clients(c.from_user.id)
    ids = [tg_id for tg_id, (app, _calls, _owner) in clients if getattr(app, "is_connected", False)]
    if not ids:
        return await c.answer("কোনো connected account online নেই।", show_alert=True)
    await state.clear()
    await state.update_data(custom_tg_ids=ids, custom_mode="all")
    await state.set_state(S.custom_target)
    await c.message.answer(
        f"🌐 ALL ACCOUNTS নির্বাচন করেছেন। মোট <b>{len(ids)}</b>টি connected account ব্যবহার হবে।\n\n"
        "👤 Recipient-এর Telegram username দিন (যেমন @username)।\n\n/cancel দিয়ে বাতিল করতে পারবেন।",
        reply_markup=back_kb())
    await c.answer()

@dp.callback_query(F.data.startswith("csi:account:"))
async def custom_inbox_choose_account(c: CallbackQuery, state: FSMContext):
    try:
        tg_id = int(c.data.split(":", 2)[2])
    except (ValueError, IndexError):
        return await c.answer("Invalid account", show_alert=True)
    allowed = dict(user_clients(c.from_user.id))
    if tg_id not in allowed:
        return await c.answer("এই account আপনার ব্যবহারের অনুমতিতে নেই।", show_alert=True)
    app = allowed[tg_id][0]
    if not getattr(app, "is_connected", False):
        return await c.answer("Account offline. আগে reconnect করুন।", show_alert=True)
    await state.clear()
    await state.update_data(custom_tg_id=tg_id, custom_tg_ids=[tg_id], custom_mode="single")
    await state.set_state(S.custom_target)
    await c.message.answer("👤 Recipient-এর Telegram username দিন (যেমন @username)।\n\n/cancel দিয়ে বাতিল করতে পারবেন।", reply_markup=back_kb())
    await c.answer()

@dp.message(S.custom_target)
async def custom_inbox_target(m: Message, state: FSMContext):
    raw = (m.text or "").strip()
    username = raw.removeprefix("https://t.me/").removeprefix("http://t.me/").removeprefix("t.me/").lstrip("@").split("/")[0]
    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
        return await m.answer("❌ সঠিক Telegram username দিন, যেমন @username।")
    data = await state.get_data()
    ids = data.get("custom_tg_ids") or ([data.get("custom_tg_id")] if data.get("custom_tg_id") else [])
    allowed = dict(user_clients(m.from_user.id))
    active = [tid for tid in ids if tid in allowed and getattr(allowed[tid][0], "is_connected", False)]
    if not active:
        await state.clear()
        return await m.answer("❌ নির্বাচিত account-গুলো এখন offline। আবার শুরু করুন।", reply_markup=kb(m.from_user.id))
    # Validate username against an authorized connected account. Store username so each
    # user session resolves the recipient in its own peer cache during the send step.
    try:
        user = await allowed[active[0]][0].get_users(username)
        if getattr(user, "is_bot", False):
            return await m.answer("❌ এই ফিচারে ব্যক্তিগত user account বেছে নিন, bot account নয়।")
        if getattr(user, "is_deleted", False):
            return await m.answer("❌ এই account আর ব্যবহারযোগ্য নয়। অন্য username দিন।")
    except Exception as exc:
        return await m.answer(f"❌ Username খুঁজে পাওয়া যায়নি: <code>{h(type(exc).__name__)}: {h(exc)}</code>\nআবার username দিন।")
    await state.update_data(custom_target_username=username, custom_target_name="@" + username, custom_tg_ids=active)
    await state.set_state(S.custom_payload)
    await m.answer(f"✅ Recipient: <b>@{h(username)}</b>\n\nএখন একটি মেসেজ পাঠান—Text, Photo, Video, Audio, Voice, Document, Animation, Video Note বা Sticker।\nএকটি মেসেজই পাঠানো হবে; Album/একাধিক ফাইল সমর্থিত নয়।", reply_markup=back_kb())

@dp.message(S.custom_payload)
async def custom_inbox_payload(m: Message, state: FSMContext):
    data = await state.get_data()
    media_obj = None
    kind = None
    extension = None
    if m.text and not m.text.startswith("/"):
        payload = {"kind": "text", "text": m.text}
        preview = f"📝 Text: {h(m.text[:700])}"
    else:
        candidates = [
            ("photo", m.photo[-1] if m.photo else None, ".jpg"),
            ("video", m.video, ".mp4"), ("audio", m.audio, ".mp3"),
            ("voice", m.voice, ".ogg"), ("video_note", m.video_note, ".mp4"),
            ("document", m.document, ".bin"), ("animation", m.animation, ".mp4"),
            ("sticker", m.sticker, ".webp"),
        ]
        for candidate_kind, obj, default_ext in candidates:
            if obj:
                kind, media_obj, extension = candidate_kind, obj, default_ext
                break
        if media_obj is None:
            return await m.answer("❌ এই ধরনের মেসেজ সমর্থিত নয়। Text, Photo, Video, Audio, Voice বা Document পাঠান।")
        original_name = getattr(media_obj, "file_name", None)
        if original_name:
            supplied_ext = os.path.splitext(original_name)[1].lower()
            if supplied_ext and len(supplied_ext) <= 10:
                extension = supplied_ext
        if kind == "sticker":
            if getattr(media_obj, "is_video", False): extension = ".webm"
            elif getattr(media_obj, "is_animated", False): extension = ".tgs"
        caption = m.caption or ""
        payload = {"kind": kind, "file_id": media_obj.file_id,
                   "file_name": original_name or f"telegram_media{extension}",
                   "extension": extension, "caption": caption,
                   "file_size": getattr(media_obj, "file_size", None)}
        size = payload.get("file_size")
        preview = f"📎 Media type: <b>{h(kind)}</b>"
        if size: preview += f"\nSize: <code>{size / (1024 * 1024):.2f} MB</code>"
        if caption: preview += f"\nCaption: {h(caption[:500])}"
    await state.update_data(custom_payload=payload)
    ids = data.get("custom_tg_ids") or [data.get("custom_tg_id")]
    mode_line = f"🌐 ALL ACCOUNTS · <b>{len(ids)}</b>টি account" if data.get("custom_mode") == "all" else f"👤 Single account · <code>{ids[0]}</code>"
    await m.answer(
        hd("🔎", "Confirm message") + f"Mode: {mode_line}\nTo: <b>{h(data.get('custom_target_name'))}</b>\n\n{preview}\n\n"
        "নিশ্চিত করুন: recipient আপনার কাছ থেকে এই মেসেজটি পেতে সম্মত।",
        reply_markup=ikb([("✅ Confirm & Send", "csi:send", "success"), ("❌ Cancel", "csi:abort", "danger")]))

@dp.callback_query(F.data == "csi:abort")
async def custom_inbox_abort(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.answer("❎ Custom Inbox send বাতিল করা হয়েছে।", reply_markup=kb(c.from_user.id))
    await c.answer("Cancelled")

@dp.callback_query(F.data == "csi:send")
async def custom_inbox_send(c: CallbackQuery, state: FSMContext):
    uid = c.from_user.id
    now = time.time()
    if now - CUSTOM_INBOX_LAST_SEND.get(uid, 0) < 10:
        return await c.answer("নিরাপত্তার জন্য ১০ সেকেন্ড অপেক্ষা করুন।", show_alert=True)
    data = await state.get_data()
    ids = data.get("custom_tg_ids") or ([data.get("custom_tg_id")] if data.get("custom_tg_id") else [])
    target_username = data.get("custom_target_username")
    payload = data.get("custom_payload") or {}
    allowed = dict(user_clients(uid))
    ids = [tid for tid in ids if tid in allowed and getattr(allowed[tid][0], "is_connected", False)]
    if not ids or not target_username or not payload:
        await state.clear()
        return await c.answer("Session expired বা account offline। আবার শুরু করুন।", show_alert=True)
    await c.answer("Sending…")
    temp_path = None
    ok, fail, errors = [], [], []
    try:
        kind = payload.get("kind")
        if kind != "text":
            file_info = await c.bot.get_file(payload["file_id"])
            extension = payload.get("extension") or ".bin"
            fd, temp_path = tempfile.mkstemp(prefix="custom_inbox_", suffix=extension)
            os.close(fd)
            await c.bot.download_file(file_info.file_path, destination=temp_path)
        for index, tg_id in enumerate(ids):
            app = allowed[tg_id][0]
            try:
                recipient = await app.get_users(target_username)
                if getattr(recipient, "is_bot", False) or getattr(recipient, "is_deleted", False):
                    raise ValueError("Recipient is not an available personal user account")
                target_id = recipient.id
                if kind == "text":
                    await app.send_message(target_id, payload["text"], parse_mode=None, disable_web_page_preview=True)
                else:
                    caption = payload.get("caption") or None
                    if kind == "photo": await app.send_photo(target_id, temp_path, caption=caption, parse_mode=None)
                    elif kind == "video": await app.send_video(target_id, temp_path, caption=caption, parse_mode=None, supports_streaming=True)
                    elif kind == "audio": await app.send_audio(target_id, temp_path, caption=caption, parse_mode=None)
                    elif kind == "voice": await app.send_voice(target_id, temp_path, caption=caption, parse_mode=None)
                    elif kind == "video_note": await app.send_video_note(target_id, temp_path)
                    elif kind == "document": await app.send_document(target_id, temp_path, caption=caption, parse_mode=None)
                    elif kind == "animation": await app.send_animation(target_id, temp_path, caption=caption, parse_mode=None)
                    elif kind == "sticker":
                        if extension == ".tgs": raise ValueError("Animated TGS sticker upload is not supported; send static WEBP or WEBM instead.")
                        await app.send_sticker(target_id, temp_path)
                    else: raise ValueError(f"Unsupported media type: {kind}")
                ok.append(tg_id)
            except FloodWait as exc:
                fail.append(tg_id)
                errors.append(f"{tg_id}: FloodWait {int(exc.value)}s")
            except Exception as exc:
                fail.append(tg_id)
                errors.append(f"{tg_id}: {type(exc).__name__}: {exc}")
                log.exception("custom inbox send failed for account %s", tg_id)
            if index < len(ids) - 1:
                await asyncio.sleep(max(1, DELAY))
        CUSTOM_INBOX_LAST_SEND[uid] = time.time()
        await state.clear()
        result = (f"✅ <b>Custom Inbox sending finished</b>\n\n👤 Recipient: <b>{h(data.get('custom_target_name'))}</b>\n"
                  f"📤 Successful accounts: <b>{len(ok)}</b>\n❌ Failed accounts: <b>{len(fail)}</b>\n"
                  f"📱 Total attempted: <b>{len(ids)}</b>")
        if ok: result += "\n\n✅ Success IDs: <code>" + h(", ".join(map(str, ok))[:1200]) + "</code>"
        if errors: result += "\n\n⚠️ Errors:\n<code>" + h("\n".join(errors)[:2500]) + "</code>"
        await c.message.answer(result, reply_markup=kb(uid))
    except Exception as exc:
        log.exception("custom inbox preparation failed")
        await c.message.answer(f"❌ পাঠানোর প্রস্তুতি ব্যর্থ: <code>{h(type(exc).__name__)}: {h(exc)}</code>\nআবার চেষ্টা করুন।")
    finally:
        if temp_path and os.path.exists(temp_path):
            try: os.remove(temp_path)
            except OSError: log.warning("Could not remove temporary media file: %s", temp_path)

# ---------- NEW: Status button ----------
@dp.message(ANY(B_STATUS))
async def status_btn(m: Message):
    uid = m.from_user.id
    rows = client_rows(uid)
    on = sum(1 for r in rows if r[0] in RUNNING)
    plan = "👑 Admin" if uid == OWNER_ID else ("💎 VIP" if is_vip(uid) else "🔐 Member")
    ar = sum(1 for (o, _c) in AUTO_REACT if o == uid)
    av = sum(1 for (o, _c) in AUTO_VIEW if o == uid)
    await m.answer(hd("📊", "Status", "live snapshot") +
                   tree(f"{plan}", f"📱 Accounts — <b>{len(rows)}</b>", f"🟢 Online — <b>{on}</b>",
                        f"🔴 Offline — <b>{len(rows) - on}</b>", f"⚡ Auto React chats — <b>{ar}</b>",
                        f"👀 Auto View chats — <b>{av}</b>"),
                   reply_markup=kb(uid))


# ---------- NEW: Uptime button ----------
@dp.message(ANY(B_UPTIME))
async def uptime_btn(m: Message):
    secs = int(time.time() - BOOT_TIME)
    d, rem = divmod(secs, 86400)
    hrs, rem = divmod(rem, 3600)
    mins, sec = divmod(rem, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if hrs or d: parts.append(f"{hrs}h")
    parts.append(f"{mins}m")
    parts.append(f"{sec}s")
    await m.answer(hd("🕒", "Uptime", "since the bot last started") + tree("🕒 Running — <b>" + " ".join(parts) + "</b>"),
                   reply_markup=kb(m.from_user.id))


# ---------- NEW: Find User button (admin only) ----------
@dp.message(ANY(B_FIND))
async def find_ask(m: Message, state: FSMContext):
    await state.set_state(S.find_user)
    await m.answer(hd("🔍", "Find user", "search by ID or @username") + "Send a user ID or @username.\n\nSend /cancel to abort.",
                   reply_markup=back_kb())


@dp.message(S.find_user)
async def find_do(m: Message, state: FSMContext):
    await state.clear()
    q = (m.text or "").strip().lstrip("@")
    if q.isdigit():
        row = db.execute("SELECT user_id, name, username, status FROM users WHERE user_id=?", (int(q),)).fetchone()
    else:
        row = db.execute("SELECT user_id, name, username, status FROM users WHERE username=? COLLATE NOCASE", (q,)).fetchone()
    if not row:
        return await m.answer("❌ No matching user found.", reply_markup=kb(m.from_user.id))
    uid, name, uname, status = row
    n_acc = count("SELECT COUNT(*) FROM clients WHERE owner=?", uid)
    vip = "Yes" if is_vip(uid) else "No"
    icons = {"approved": "✅", "rejected": "❌", "revoked": "🚫", "banned": "⛔", "pending": "⏳"}
    await m.answer(hd("🔍", "User found") +
                   tree(f"👤 Name — <b>{h(name or '—')}</b>", f"🔗 Username — <b>{h('@' + uname) if uname else '—'}</b>",
                        f"🆔 ID — <b>{uid}</b>", f"{icons.get(status, '•')} Status — <b>{h(status)}</b>",
                        f"💎 VIP — <b>{vip}</b>", f"📱 Accounts — <b>{n_acc}</b>") +
                   f"\n\n🧰 <code>/ban {uid}</code>  ·  <code>/unban {uid}</code>", reply_markup=kb(m.from_user.id))


# ---------- NEW: Restart Accounts button ----------
@dp.message(ANY(B_RESTART))
async def restart_btn(m: Message):
    uid = m.from_user.id
    rows = db.execute("SELECT id, session, owner FROM clients WHERE id IN (SELECT id FROM clients WHERE owner=? OR ?=?)",
                       (uid, uid, OWNER_ID)).fetchall() if uid != OWNER_ID and not is_vip(uid) else \
           db.execute("SELECT id, session, owner FROM clients").fetchall()
    # restrict to accounts this user is allowed to control
    allowed_ids = {tid for tid, _ in user_clients(uid)} | {r[0] for r in client_rows(uid)}
    rows = [r for r in rows if r[0] in allowed_ids]
    if not rows:
        return await m.answer("No accounts to restart.", reply_markup=kb(uid))
    await m.answer(f"⏳ Restarting {len(rows)} account(s)...")
    ok = fail = 0
    for tg_id, session_enc, owner in rows:
        try:
            await start_client(fernet.decrypt(session_enc).decode(), tg_id, owner)
            ok += 1
        except Exception as e:
            fail += 1
            log.warning("restart %s failed: %r", tg_id, e)
        await asyncio.sleep(DELAY)
    await m.answer(f"🔄 Restart done.\n✅ OK: {ok}\n❌ Failed: {fail}", reply_markup=kb(uid))


# ---------- NEW: Broadcast button (admin only) ----------
@dp.message(ANY(B_BROADCAST))
async def broadcast_ask(m: Message, state: FSMContext):
    await state.set_state(S.broadcast)
    await m.answer(hd("📢", "Broadcast", "sent to every approved user") +
                   "Send the message to broadcast to all approved users.\n\nSend /cancel to abort.",
                   reply_markup=back_kb())


@dp.message(S.broadcast)
async def broadcast_do(m: Message, state: FSMContext):
    await state.clear()
    users = db.execute("SELECT user_id FROM users WHERE status='approved'").fetchall()
    ok = fail = 0
    for (uid,) in users:
        try:
            await m.bot.copy_message(uid, m.chat.id, m.message_id)
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await m.answer(f"📢 Broadcast sent.\n✅ Delivered: {ok}\n❌ Failed: {fail}", reply_markup=kb(m.from_user.id))


# ---------- NEW: My Info ----------
@dp.message(ANY(B_INFO))
async def my_info(m: Message):
    uid = m.from_user.id
    plan = "👑 Admin" if uid == OWNER_ID else ("💎 VIP" if is_vip(uid) else "🔐 Member")
    own = db.execute("SELECT COUNT(*) FROM clients WHERE owner=?", (uid,)).fetchone()[0]
    await m.answer(hd("🧮", "My info") +
                   tree(f"👤 Name — <b>{h(m.from_user.full_name)}</b>",
                        f"🔗 Username — <b>{h('@' + m.from_user.username) if m.from_user.username else '—'}</b>",
                        f"🆔 ID — <b>{uid}</b>", f"{plan}", f"📱 My accounts — <b>{own}</b>"),
                   reply_markup=kb(uid))


# ---------- NEW: Ping ----------
@dp.message(ANY(B_PING))
async def ping_btn(m: Message):
    t0 = time.time()
    sent = await m.answer("🏓 Pinging...")
    ms = int((time.time() - t0) * 1000)
    try:
        await sent.edit_text(hd("🏓", "Pong", f"{ms} ms"), reply_markup=None)
    except Exception:
        pass
    await m.answer("👆 Response time above.", reply_markup=kb(m.from_user.id))


# ---------- NEW: Clear My Auto (nested confirm buttons) ----------
@dp.message(ANY(B_CLEARAUTO))
async def clearauto_ask(m: Message):
    uid = m.from_user.id
    n = sum(1 for (o, _c) in AUTO_REACT if o == uid) + sum(1 for (o, _c) in AUTO_VIEW if o == uid)
    if not n:
        return await m.answer("Nothing to clear — you have no Auto React / Auto View set.", reply_markup=kb(uid))
    await m.answer(hd("🧹", "Clear my auto settings", f"{n} rule(s) will be removed") +
                   "Are you sure?", reply_markup=ikb([("✅ Yes, clear", "clra:yes", "danger"), ("❌ No", "clra:no")]))


@dp.callback_query(F.data.startswith("clra:"))
async def clearauto_do(c: CallbackQuery):
    uid = c.from_user.id
    if c.data == "clra:no":
        await c.message.edit_text("❎ Cancelled.")
        return await c.answer()
    for k in [k for k in list(AUTO_REACT) if k[0] == uid]:
        AUTO_REACT.pop(k, None)
    for k in [k for k in list(AUTO_VIEW) if k[0] == uid]:
        AUTO_VIEW.discard(k)
    db.execute("DELETE FROM auto2 WHERE owner=?", (uid,)); db.commit()
    await c.message.edit_text("🧹 All your Auto React / Auto View rules were cleared.")
    await c.answer("Cleared")


# ---------- NEW: Settings (nested submenu) ----------
def v_settings(uid):
    is_admin = uid == OWNER_ID
    state_txt = "🔛 ON" if BOLD_MODE[0] else "🔚 OFF"
    txt = hd("⚙", "Settings") + tree(f"🔠 Bold messages — <b>{state_txt}</b>")
    if not is_admin:
        return txt + "\n\nOnly the owner can change these.", ikb([("🔙 Close", "set:close")])
    rows = [
        [("🔠 Toggle Bold", "set:bold")],
        [("💎 Emoji Manager", "set:manager"), ("💎 Set Pack (guide)", "set:spack")],
        [("♻ Reset Emoji", "set:reset")],
        [("🔙 Close", "set:close")],
    ]
    return txt, ikb(*rows)


@dp.message(ANY(B_SETTINGS))
async def settings_btn(m: Message):
    txt, markup = v_settings(m.from_user.id)
    await m.answer(txt, reply_markup=markup)


@dp.callback_query(F.data.startswith("set:"))
async def settings_cb(c: CallbackQuery):
    act = c.data.split(":", 1)[1]
    if act == "close":
        try: await c.message.delete()
        except Exception: pass
        return await c.answer()
    if c.from_user.id != OWNER_ID:
        return await c.answer("⛔ Admin only", show_alert=True)
    if act == "bold":
        BOLD_MODE[0] = not BOLD_MODE[0]
        db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('bold',?)", ("1" if BOLD_MODE[0] else "0",)); db.commit()
    elif act == "manager":
        txt, markup = v_emoji_manager(0)
        try:
            await c.message.edit_text(txt, reply_markup=markup)
        except Exception:
            await c.message.answer(txt, reply_markup=markup)
        return await c.answer()
    elif act == "spack":
        return await c.answer("Send /setpack <link> in the chat.", show_alert=True)
    elif act == "reset":
        PREMIUM.clear()
        db.execute("DELETE FROM premium"); db.commit()
    txt, markup = v_settings(c.from_user.id)
    try:
        await c.message.edit_text(txt, reply_markup=markup)
    except Exception:
        pass
    await c.answer("Updated")


# ---------- NEW: Admin Tools (nested submenu, admin only) ----------
def v_admtools():
    pend = count("SELECT COUNT(*) FROM users WHERE status='pending'")
    txt = hd("🧰", "Admin tools", "quick shortcuts") + tree(f"📥 Pending requests — <b>{pend}</b>",
                                                              f"🟢 Online accounts — <b>{len(RUNNING)}</b>")
    rows = [
        [("📥 Requests", "adm:req", "primary"), ("🔑 VIP Keys", "adm:keys", "success")],
        [("👥 Users", "adm:users"), ("📱 Accounts", "adm:acc")],
        [("🔠 Toggle Bold", "set:bold")],
        [("🔙 Close", "set:close")],
    ]
    return txt, ikb(*rows)


@dp.message(ANY(B_ADMTOOLS))
async def admtools_btn(m: Message):
    if m.from_user.id != OWNER_ID:
        return
    txt, markup = v_admtools()
    await m.answer(txt, reply_markup=markup)


# ---------- NEW: Pin Post (admin) ----------
@dp.message(ANY(B_PIN))
async def pin_ask(m: Message, state: FSMContext):
    if m.from_user.id != OWNER_ID:
        return
    await state.set_state(S.pin_post)
    await m.answer(hd("📌", "Pin post") + "Send the post link, e.g. https://t.me/channel/123\n\n"
                   "The first available logged-in account that can see it will pin it.\n\nSend /cancel to abort.",
                   reply_markup=back_kb())


@dp.message(S.pin_post)
async def pin_do(m: Message, state: FSMContext):
    await state.clear()
    post = parse_post(m.text)
    if not post:
        return await m.answer("❌ Invalid post link.", reply_markup=kb(m.from_user.id))
    chat, mid = post
    clients = user_clients(m.from_user.id)
    if not clients:
        return await m.answer("❌ No logged-in accounts available.", reply_markup=kb(m.from_user.id))
    app = clients[0][1][0]
    try:
        await app.get_chat(chat)
        await app.pin_chat_message(chat, mid)
        await m.answer("📌 Pinned.", reply_markup=kb(m.from_user.id))
    except Exception as e:
        await m.answer(f"❌ Pin failed: {h(e.__class__.__name__)}: {h(e)}", reply_markup=kb(m.from_user.id))


# ---------- NEW: Clear Cache (admin) ----------
@dp.message(ANY(B_CACHE))
async def cache_btn(m: Message):
    if m.from_user.id != OWNER_ID:
        return
    n = len(SEEN)
    SEEN.clear()
    await m.answer(f"🧊 Cleared {n} cached watcher entr{'y' if n == 1 else 'ies'}. "
                   "Auto View / Auto React will re-baseline on the next check.", reply_markup=kb(m.from_user.id))


# ---------- NEW: Developer contact ----------
@dp.message(ANY(B_DEV))
async def dev_btn(m: Message):
    await m.answer(hd("👨‍💻", "Developer", "need help or want changes made?") +
                   tree(f"📨 Contact — <b>{h(DEV_CONTACT)}</b>"),
                   reply_markup=kb(m.from_user.id))


# ---------- NEW: Emoji Manager (see all slots, change one at a time) ----------
EMJ_PAGE_SIZE = 8


def _unique_slots():
    seen = {}
    for e, d in SLOTS:
        ne = norm(e)
        if ne not in seen:
            seen[ne] = d
    return list(seen.items())


def v_emoji_manager(page=0):
    items = _unique_slots()
    total_pages = max(1, (len(items) + EMJ_PAGE_SIZE - 1) // EMJ_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = items[page * EMJ_PAGE_SIZE:(page + 1) * EMJ_PAGE_SIZE]
    lines, rows = [], []
    for e, d in chunk:
        on = norm(e) in PREMIUM
        lines.append(f"{'🟢' if on else '⚪'} {e}  {d}")
        rows.append([(f"{e} {d}", f"emj:pick:{e}")])
    nav = []
    if page > 0:
        nav.append((f"⬅ Prev", f"emj:page:{page - 1}"))
    nav.append((f"{page + 1}/{total_pages}", "emj:noop"))
    if page < total_pages - 1:
        nav.append((f"Next ➡", f"emj:page:{page + 1}"))
    rows.append(nav)
    rows.append([("🔙 Close", "set:close")])
    txt = (hd("💎", "Emoji manager", "🟢 = premium set · ⚪ = default") + "\n".join(lines) +
           "\n\nTap a row, then send one premium emoji to set it for that slot.")
    return txt, ikb(*rows)


@dp.message(Command("emojimanager"))
async def emoji_manager_cmd(m: Message):
    if m.from_user.id != OWNER_ID:
        return
    txt, markup = v_emoji_manager(0)
    await m.answer(txt, reply_markup=markup)


@dp.callback_query(F.data.startswith("emj:"))
async def emoji_manager_cb(c: CallbackQuery, state: FSMContext):
    if c.from_user.id != OWNER_ID:
        return await c.answer("⛔ Admin only", show_alert=True)
    parts = c.data.split(":")
    act = parts[1]
    if act == "noop":
        return await c.answer()
    if act == "page":
        txt, markup = v_emoji_manager(int(parts[2]))
        try:
            await c.message.edit_text(txt, reply_markup=markup)
        except Exception:
            pass
        return await c.answer()
    if act == "pick":
        emoji = parts[2]
        await state.set_state(S.emoji_one)
        await state.update_data(slot_emoji=emoji)
        cur = PREMIUM.get(norm(emoji))
        await c.message.answer(
            f"💎 Setting the premium emoji for: {emoji}\n\n"
            f"Current: {'set (id ' + h(cur) + ')' if cur else 'default (not set)'}\n\n"
            "Send ONE premium emoji now to use it here, or /cancel to abort.", reply_markup=back_kb())
        return await c.answer()


@dp.message(S.emoji_one)
async def emoji_one_do(m: Message, state: FSMContext):
    data = await state.get_data()
    slot = data.get("slot_emoji")
    ents = [e for e in (m.entities or []) if e.type == "custom_emoji"]
    if not ents:
        return await m.answer("❌ That wasn't a premium emoji. Send one premium emoji, or /cancel.")
    cid = ents[0].custom_emoji_id
    PREMIUM[norm(slot)] = cid
    db.execute("INSERT OR REPLACE INTO premium(emoji,cid) VALUES(?,?)", (norm(slot), cid)); db.commit()
    await state.clear()
    await m.answer(f"✅ Saved for {slot}.", reply_markup=kb(m.from_user.id))
    txt, markup = v_emoji_manager(0)
    await m.answer(txt, reply_markup=markup)


# ---------- Access / Admin panel ----------
@dp.message(ANY(B_ACC))
async def access(m: Message):
    uid = m.from_user.id
    if uid == OWNER_ID:
        n = db.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
        return await m.answer(hd("👑", "Access", "full control") + tree("👑 Role — <b>Admin</b>", f"📱 Accounts — <b>{n}</b>"))
    own = db.execute("SELECT COUNT(*) FROM clients WHERE owner=?", (uid,)).fetchone()[0]
    if is_vip(uid):
        adm = db.execute("SELECT COUNT(*) FROM clients WHERE owner=?", (OWNER_ID,)).fetchone()[0]
        return await m.answer(hd("💎", "Access", "vip unlocked") + tree("💎 Plan — <b>VIP</b>", f"📱 Your accounts — <b>{own}</b>", f"👑 Admin accounts — <b>{adm}</b>"))
    await m.answer(hd("🔐", "Access", "standard member") + tree("🔐 Plan — <b>Member</b>", f"📱 Your accounts — <b>{own}</b>") + "\n\nHave a VIP key? Unlock all admin accounts.", reply_markup=VIP_BTN)


@dp.callback_query(F.data == "vip_enter")
async def vip_enter(c: CallbackQuery, state: FSMContext):
    await state.set_state(S.vip_key)
    await c.message.answer(hd("🔑", "VIP key", "paste the key from the admin") + "/cancel to abort.", reply_markup=back_kb())
    await c.answer()


@dp.message(S.vip_key)
async def vip_redeem(m: Message, state: FSMContext):
    key = (m.text or "").strip().upper()
    row = db.execute("SELECT status FROM access_keys WHERE key=?", (key,)).fetchone()
    try: await m.delete()   # hide the key from chat
    except Exception: pass
    if not row or row[0] != "active":
        return await m.answer("❌ Invalid or already used key. Send a valid key, or /cancel.")
    u = m.from_user
    db.execute("UPDATE access_keys SET status='used', used_by=?, used_at=? WHERE key=?", (u.id, int(time.time()), key))
    db.execute("INSERT OR REPLACE INTO vip_users(user_id,key) VALUES(?,?)", (u.id, key))
    db.execute("INSERT INTO users(user_id,name,username,status) VALUES(?,?,?,'approved') "
               "ON CONFLICT(user_id) DO UPDATE SET status='approved'", (u.id, u.full_name, u.username or ""))
    db.commit()
    await state.clear()
    n = db.execute("SELECT COUNT(*) FROM clients WHERE owner=?", (OWNER_ID,)).fetchone()[0]
    await m.answer(hd("🎉", "VIP activated") + tree("💎 Plan — <b>VIP</b>", f"👑 Admin accounts — <b>{n}</b>"), reply_markup=kb(u.id))
    try:
        await m.bot.send_message(OWNER_ID, f"💎 Key <code>{key}</code> used by {h(u.full_name)} | 🆔 {u.id}")
    except Exception:
        pass


def count(sql, *a):
    return db.execute(sql, a).fetchone()[0]


def v_panel():
    pend = count("SELECT COUNT(*) FROM users WHERE status='pending'")
    appr = count("SELECT COUNT(*) FROM users WHERE status='approved'")
    vips = count("SELECT COUNT(*) FROM vip_users")
    txt = (hd("👑", "Admin console", "control everything from here") +
           tree(f"👥 Users — <b>{appr}</b>", f"💎 VIP — <b>{vips}</b>", f"⏳ Pending — <b>{pend}</b>",
                f"📱 Accounts — <b>{count('SELECT COUNT(*) FROM clients')}</b>",
                f"🟢 Online — <b>{len(RUNNING)}</b>"))
    return txt, ikb([(f"📥 Requests · {pend}", "adm:req", "primary"), ("🔑 VIP Keys", "adm:keys", "success")],
                    [("👥 Users", "adm:users"), ("📱 Accounts", "adm:acc")],
                    [("📈 User Activity", "adm:activity", "success")])


def v_req():
    rows = db.execute("SELECT user_id, name, username FROM users WHERE status='pending'").fetchall()
    txt = hd("📥", "Requests", "waiting for approval") + ("\n".join(f"• {h(n or '—')} | 🆔 {i}" for i, n, _u in rows) if rows else "No pending requests.")
    btns = [[(f"✅ {(n or str(i))[:14]}", f"adm:ap:{i}", "success"), (f"❌ {(n or str(i))[:14]}", f"adm:rj:{i}", "danger")]
            for i, n, _u in rows[:30]]
    return txt, ikb(*btns, [("🔙 Back", "adm:home")])


def v_users():
    rows = db.execute("SELECT user_id, name, status FROM users WHERE status!='pending' ORDER BY status").fetchall()
    icons = {"approved": "✅", "rejected": "❌", "revoked": "🚫", "banned": "⛔"}
    txt = hd("👥", "Users") + ("\n".join(f"{icons.get(s, '•')} {h(n or '—')} | 🆔 {i}" for i, n, s in rows) if rows else "No users yet.")
    btns = []
    for i, n, s in rows[:30]:
        nm = (n or str(i))[:14]
        if s == "banned":
            btns.append([(f"✅ Unban {nm}", f"adm:ub:{i}", "success")])
        elif s == "approved":
            btns.append([(f"🚫 Revoke {nm}", f"adm:rv:{i}", "danger"), (f"⛔ Ban {nm}", f"adm:bn:{i}", "danger")])
        else:
            btns.append([(f"✅ Approve {nm}", f"adm:ap:{i}", "success"), (f"⛔ Ban {nm}", f"adm:bn:{i}", "danger")])
    return txt, ikb(*btns, [("🔙 Back", "adm:home")])


def v_acc():
    rows = db.execute("SELECT id, name, owner FROM clients ORDER BY owner").fetchall()
    groups = {}
    for cid, n, o in rows:
        groups.setdefault(o, []).append((cid, n))
    parts = []
    for o, items in groups.items():
        parts.append(f"👥 {h(owner_name(o))} ({len(items)})\n" + "\n".join(
            f"   {'🟢' if c in RUNNING else '🔴'} {h(n)}" for c, n in items))
    body = "\n\n".join(parts) or "No accounts yet."
    if len(body) > 3500:
        body = body[:3500] + "\n…"
    return hd("📱", f"All accounts · {len(rows)}") + f"<blockquote expandable>{body}</blockquote>", ikb([("🔙 Back", "adm:home")])


def v_activity():
    rows = db.execute("""
        SELECT u.user_id, COALESCE(u.name, CAST(u.user_id AS TEXT)), COALESCE(u.status,'unknown'),
               COUNT(a.id), MAX(a.created)
        FROM users u LEFT JOIN activity a ON a.user_id=u.user_id
        GROUP BY u.user_id ORDER BY COALESCE(MAX(a.created),0) DESC LIMIT 40
    """).fetchall()
    txt = hd("📈", "User activity", "select a user to inspect their history")
    btns = []
    for uid, name, status, total, last in rows:
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(last)) if last else "No activity"
        btns.append([(f"{name[:18]} · {total} events", f"adm:actuser:{uid}")])
    if not btns:
        txt += "No user activity yet."
    return txt, ikb(*btns, [("🔙 Back", "adm:home")])


def v_activity_user(uid):
    row = db.execute("SELECT name, username, status FROM users WHERE user_id=?", (uid,)).fetchone()
    name, username, status = row if row else (str(uid), "", "owner/unknown")
    added = count("SELECT COUNT(*) FROM activity WHERE user_id=? AND action='account_added'", uid)
    joins = count("SELECT COUNT(*) FROM activity WHERE user_id=? AND action='voice_join'", uid)
    leaves = count("SELECT COUNT(*) FROM activity WHERE user_id=? AND action='voice_leave'", uid)
    total_accounts = count("SELECT COUNT(*) FROM clients WHERE owner=?", uid)
    events = db.execute("SELECT action,details,created FROM activity WHERE user_id=? ORDER BY created DESC LIMIT 15", (uid,)).fetchall()
    lines = []
    for action, details, stamp in events:
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stamp))
        lines.append(f"• <b>{h(action)}</b> — {h(details)}\n  <i>{when}</i>")
    body = "\\n".join(lines) if lines else "No recorded activity."
    txt = (hd("👤", "User activity details") +
           tree(f"Name — <b>{h(name)}</b>", f"Username — <b>@{h(username) if username else '—'}</b>",
                f"User ID — <code>{uid}</code>", f"Access — <b>{h(status)}</b>",
                f"Accounts currently saved — <b>{total_accounts}</b>",
                f"Accounts added (logged) — <b>{added}</b>",
                f"Voice join attempts — <b>{joins}</b>", f"Voice leave attempts — <b>{leaves}</b>",
                f"All recorded events — <b>{count('SELECT COUNT(*) FROM activity WHERE user_id=?', uid)}</b>") +
           "\\n\\n<b>Recent activity (latest 15)</b>\\n" + body)
    if len(txt) > 3900:
        txt = txt[:3900] + "\\n…"
    return txt, ikb([("🔙 All activity", "adm:activity")], [("🏠 Admin home", "adm:home")])


def v_keys(new=None):
    rows = db.execute("SELECT key, status, used_by FROM access_keys ORDER BY created DESC LIMIT 30").fetchall()
    lines = []
    for k, st, ub in rows:
        if st == "active":
            lines.append(f"🟢 <code>{k}</code> — unused")
        elif st == "used":
            lines.append(f"💎 <code>{k}</code> — {h(owner_name(ub))} ({ub})")
        else:
            lines.append(f"⛔ <code>{k}</code> — revoked")
    head = f"🎉 <b>New key</b>  · tap to copy\n<code>{new}</code>\n\n" if new else ""
    txt = head + hd("🔑", "VIP keys", "one key = one person · unlocks ALL admin accounts") + ("\n".join(lines) or "No keys yet.")
    btns = [[("➕ Generate New Key", "adm:kg", "success")]]
    for k, st, _ub in rows[:20]:
        if st != "revoked":
            btns.append([(f"🧹 Revoke {k}", f"adm:kr:{k}", "danger")])
    btns.append([("🔙 Back", "adm:home")])
    return txt, ikb(*btns)


async def show(c: CallbackQuery, view):
    text, markup = view
    try:
        await c.message.edit_text(text, reply_markup=markup)
    except Exception:
        await c.message.answer(text, reply_markup=markup)


@dp.message(ANY(B_ADMIN))
async def admin_panel(m: Message):
    if m.from_user.id != OWNER_ID:
        return await m.answer("⛔ Admin only.")
    txt, k = v_panel()
    await m.answer(txt, reply_markup=k)


@dp.callback_query(F.data.startswith("adm:"))
async def admin_cb(c: CallbackQuery):
    try:
        await _admin_cb(c)
    except Exception as e:
        log.exception("admin_cb failed")
        try:
            await c.answer(f"⚠️ {type(e).__name__}: {e}"[:190], show_alert=True)
        except Exception:
            pass


async def _admin_cb(c: CallbackQuery):
    if c.from_user.id != OWNER_ID:
        return await c.answer("⛔ Admin only", show_alert=True)
    parts = c.data.split(":")
    act = parts[1]
    if act in ("ap", "rj", "rv", "bn", "ub"):
        uid = int(parts[2])
        status = {"ap": "approved", "rj": "rejected", "rv": "revoked", "bn": "banned", "ub": "rejected"}[act]
        db.execute("UPDATE users SET status=? WHERE user_id=?", (status, uid))
        if act != "ap":
            db.execute("DELETE FROM vip_users WHERE user_id=?", (uid,))
        db.commit()
        msg = {"ap": "✅ Your access request has been approved!\n\nSend /start to begin.",
               "rj": "❌ Your access request was rejected by the bot owner.",
               "rv": "🚫 Your access has been revoked by the bot owner.",
               "bn": "⛔ You have been permanently banned from this bot by the owner.",
               "ub": "✅ Your ban has been lifted. Send /start to request access again."}[act]
        try:
            await c.bot.send_message(uid, msg)
        except Exception:
            pass
        head = c.message.text or ""
        if head.startswith("🔔"):
            result = {"ap": "✅ Accepted", "rj": "❌ Rejected", "rv": "🚫 Revoked"}[act]
            await c.message.edit_text(f"{h(head)}\n\n{result}")
        elif head.startswith("📥"):
            await show(c, v_req())
        else:
            await show(c, v_users())
        return await c.answer("Done")
    if act == "kg":
        await show(c, v_keys(new_key()))
        return await c.answer("Key created")
    if act == "kr":
        key = parts[2]
        r = db.execute("SELECT used_by FROM access_keys WHERE key=?", (key,)).fetchone()
        db.execute("UPDATE access_keys SET status='revoked' WHERE key=?", (key,))
        if r and r[0]:
            db.execute("DELETE FROM vip_users WHERE user_id=?", (r[0],))
            db.execute("UPDATE users SET status='revoked' WHERE user_id=?", (r[0],))
            try: await c.bot.send_message(r[0], "🚫 Your VIP access has been revoked by the bot owner.")
            except Exception: pass
        db.commit()
        await show(c, v_keys())
        return await c.answer("Key revoked")
    if act == "activity":
        await show(c, v_activity())
        return await c.answer()
    if act == "actuser":
        await show(c, v_activity_user(int(parts[2])))
        return await c.answer()
    views = {"keys": v_keys, "home": v_panel, "req": v_req, "users": v_users, "acc": v_acc}
    if act in views:
        await show(c, views[act]())
    await c.answer()


# ---------- boot ----------
async def main():
    global BOT_INSTANCE
    load_auto()
    if MASTER_API_URL and INSTANCE_API_KEY:
        asyncio.create_task(_master_heartbeat_loop(), name="master-heartbeat")
        log.info("Central master monitoring enabled (instance=%s)", INSTANCE_ID)
    else:
        log.info("Central master monitoring disabled; set MASTER_API_URL and INSTANCE_API_KEY to enable")
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    BOT_INSTANCE = bot
    bot.session.middleware(PremiumMW())   # premium emoji can be added live with /setemoji or /setpack
    bot.session.middleware(BoldMW())      # all outgoing text bold by default, toggle with /boldon /boldoff
    for tg_id, session, owner in db.execute("SELECT id, session, owner FROM clients").fetchall():
        try:
            await start_client(fernet.decrypt(session).decode(), tg_id, owner or OWNER_ID)
            log.info("client %s started", tg_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("client %s failed to start", tg_id)
            await _notify_error(bot, OWNER_ID, exc, f"startup client {tg_id}")
    try:
        await bot.send_message(OWNER_ID, "🟢 <b>Bot চালু হয়েছে</b>\nSelf-healing monitor সক্রিয়।")
    except Exception:
        log.warning("startup notification could not be sent to owner")
    # Guarded tasks automatically restart if their own coroutine crashes.
    asyncio.create_task(
        _run_guarded_background("auto-view-react-watcher", watcher, bot),
        name="guard-auto-view-react-watcher",
    )
    asyncio.create_task(
        _run_guarded_background("self-healing-supervisor", lambda: self_healing_supervisor(bot), bot),
        name="guard-self-healing-supervisor",
    )
    asyncio.create_task(
        _run_guarded_background("runtime-health-monitor", lambda: _runtime_health_monitor(bot), bot),
        name="guard-runtime-health-monitor",
    )
    log.info("Bot startup complete; runtime self-healing monitors are active.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
