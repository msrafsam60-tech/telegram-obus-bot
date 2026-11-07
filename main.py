"""
Telegram Multi-Client Voice Chat Bot + Super Admin Monitor + Remote Control
============================================================================
RUN main bot:         python main.py
RUN super monitor:    python main.py super

ONE FILE. TWO BOTS. TWO PROCESSES.
- main  : your original bot (unchanged features)
- super : separate bot on SUPER_ADMIN_TOKEN that reads the SAME data.db

Optional remote control (env):
    DEPLOY_ID, SUPER_CHAT_ID, HB_SECONDS  -> heartbeat + /remote CUST-xxx stop|clear|status|notify|restart
"""
import asyncio, html, io, logging, os, re, secrets, sqlite3, sys, time, tempfile
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
                             AuthTokenExpired, SendCodeUnavailable)
from pyrogram.raw.functions.messages import GetMessagesViews
from pyrogram.qrlogin import QRLogin
from pytgcalls import PyTgCalls

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot")


def _need(name):
    v = os.environ.get(name)
    if not v:
        raise SystemExit(f"❌ Missing required environment variable: {name}.")
    return v


BOT_TOKEN = _need("BOT_TOKEN")
API_ID = int(_need("API_ID"))
API_HASH = _need("API_HASH")
OWNER_ID = int(_need("OWNER_ID"))
DATA_DIR = os.environ.get("DATA_DIR", ".")
os.makedirs(DATA_DIR, exist_ok=True)
DELAY = float(os.environ.get("DELAY", "1"))
BOOT_TIME = time.time()
DEV_CONTACT = os.environ.get("DEV_CONTACT", "@your_username_here")

SUPER_TOKEN = os.environ.get("SUPER_ADMIN_TOKEN")
SUPER_ID = int(os.environ["SUPER_ADMIN_ID"]) if os.environ.get("SUPER_ADMIN_ID") else None
SUPER_CHAT = int(os.environ["SUPER_CHAT_ID"]) if os.environ.get("SUPER_CHAT_ID") else None
DEPLOY_ID = os.environ.get("DEPLOY_ID", "LOCAL-001")
HB_SECONDS = int(os.environ.get("HB_SECONDS", "30"))
REFRESH = float(os.environ.get("REFRESH_SECONDS", "3"))
PUSH_WINDOW = int(os.environ.get("PUSH_WINDOW", "300"))


def _proxy():
    u = os.environ.get("PROXY_URL", "").strip()
    if not u:
        return None
    x = urlparse(u)
    return dict(scheme=x.scheme or "socks5", hostname=x.hostname, port=x.port,
                username=x.username, password=x.password)


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
)

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
db.execute("PRAGMA journal_mode=WAL")
db.execute("PRAGMA synchronous=NORMAL")
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
CREATE TABLE IF NOT EXISTS events(
 id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, user_id INTEGER,
 kind TEXT NOT NULL, chat TEXT DEFAULT '', ok INTEGER DEFAULT 1, detail TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_id ON events(id);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);
CREATE TABLE IF NOT EXISTS bot_state(
 proc TEXT PRIMARY KEY, started INTEGER, heartbeat INTEGER, version TEXT
);
""")
try:
    db.execute("ALTER TABLE clients ADD COLUMN owner INTEGER")
except sqlite3.OperationalError:
    pass
db.execute("UPDATE clients SET owner=? WHERE owner IS NULL", (OWNER_ID,))
db.commit()
for _e, _c in db.execute("SELECT emoji, cid FROM premium").fetchall():
    PREMIUM[_e] = _c
_t = {norm(k): v for k, v in PREMIUM.items()}
PREMIUM.clear(); PREMIUM.update(_t)

_row = db.execute("SELECT value FROM settings WHERE key='bold'").fetchone()
BOLD_MODE = [_row is None or _row[0] == "1"]

RUNNING = {}
BOT_INSTANCE = None
PENDING = {}
AUTO_REACT = {}
AUTO_VIEW = set()
SEEN = {}

BLOCKED = [False]
STOP_EVENT = None   # set in main()
_RESTART_FLAG = [False]


def emit(kind, user_id=None, chat="", ok=1, detail=""):
    try:
        db.execute("INSERT INTO events(ts,user_id,kind,chat,ok,detail) VALUES(?,?,?,?,?,?)",
                   (int(time.time()), user_id, str(kind)[:40], str(chat)[:80], int(bool(ok)), str(detail)[:400]))
        db.execute("INSERT OR REPLACE INTO bot_state(proc,started,heartbeat,version) VALUES('main',"
                   "COALESCE((SELECT started FROM bot_state WHERE proc='main'),?),?,'1.0')",
                   (int(BOOT_TIME), int(time.time())))
        db.commit()
    except Exception:
        log.exception("emit failed")


async def heartbeat_loop():
    while True:
        try:
            db.execute("INSERT OR REPLACE INTO bot_state(proc,started,heartbeat,version) VALUES('main',"
                       "COALESCE((SELECT started FROM bot_state WHERE proc='main'),?),?,'1.0')",
                       (int(BOOT_TIME), int(time.time())))
            db.commit()
        except Exception:
            log.exception("heartbeat failed")
        await asyncio.sleep(15)


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
    if uid == OWNER_ID:
        return list(RUNNING.items())
    if is_vip(uid):
        return [(tid, v) for tid, v in RUNNING.items() if v[2] in (uid, OWNER_ID)]
    return [(tid, v) for tid, v in RUNNING.items() if v[2] == uid]


def new_key():
    k = "VIP-" + secrets.token_hex(6).upper()
    db.execute("INSERT INTO access_keys(key,status,created) VALUES(?,?,?)", (k, "active", int(time.time())))
    db.commit()
    emit("vip_key", None, detail=f"generated {k}")
    return k


def owner_name(oid):
    if oid == OWNER_ID:
        return "You"
    r = db.execute("SELECT name FROM users WHERE user_id=?", (oid,)).fetchone()
    return r[0] if r and r[0] else str(oid)


def log_activity(user_id, action, details=""):
    try:
        db.execute("INSERT INTO activity(user_id,action,details,created) VALUES(?,?,?,?)",
                   (int(user_id), str(action)[:80], str(details)[:300], int(time.time())))
        db.commit()
    except Exception:
        log.exception("activity log write failed")


async def notify_owner_activity(bot, user, action, details=""):
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
        await bot.send_message(
            OWNER_ID,
            "🔔 <b>User Activity Alert</b>\n\n"
            f"{title}\n👤 User: {name}\n🔗 Username: {h(username)}\n"
            f"🆔 User ID: <code>{uid}</code>\n📌 Details: <code>{h(details)[:700]}</code>"
        )
    except Exception:
        log.exception("owner activity notification failed")


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
            emit("error", uid, ok=0, detail=f"for_all: {e.__class__.__name__}: {e}")
        await asyncio.sleep(DELAY)
    secs = max(1, int(time.time() - t0))
    if ok or fail:
        emit("bulk", uid, ok=(fail == 0), detail=f"ok={ok} fail={fail} secs={secs}")
    return ok, fail, secs


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
    await stop_client(tg_id)
    app = Client(f"c{tg_id}", api_id=API_ID, api_hash=API_HASH, session_string=session_string, in_memory=True)
    calls = None
    try:
        await app.start()
        try:
            async for _ in app.get_dialogs():
                pass
        except Exception as e:
            log.warning("dialogs %s: %r", tg_id, e)
        calls = PyTgCalls(app)
        await calls.start()
        app.add_handler(MessageHandler(make_handler(owner), filters.channel))
        RUNNING[tg_id] = (app, calls, owner)
    except BaseException:
        if calls is not None:
            try: await calls.stop()
            except Exception: pass
        try:
            if getattr(app, "is_connected", False):
                await app.stop()
        except Exception: pass
        raise


async def stop_client(tg_id):
    pair = RUNNING.pop(tg_id, None)
    if pair:
        app, calls, _owner = pair
        if calls is not None:
            try: await calls.stop()
            except Exception: pass
        try:
            if getattr(app, "is_connected", False):
                await app.stop()
        except Exception: pass


async def recent_ids(app, chat, n=10):
    return [x.id async for x in app.get_chat_history(chat, limit=n)]


async def view_posts(app, chat, ids):
    await app.invoke(GetMessagesViews(peer=await app.resolve_peer(chat), id=ids, increment=True))


async def watcher():
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
                            SEEN[vkey] = set(ids)
                        else:
                            new = [i for i in ids if i not in SEEN[vkey]]
                            if new:
                                try:
                                    await view_posts(app, chat, new)
                                    SEEN[vkey].update(new)
                                    emit("watcher", owner, chat=str(chat), detail=f"viewed {len(new)} via {tg_id}")
                                except FloodWait as e:
                                    await asyncio.sleep(e.value + 1)
                                except Exception as e:
                                    log.warning("watcher view %s failed: %r", tg_id, e)
                    emoji = AUTO_REACT.get((owner, chat))
                    if emoji:
                        key = (tg_id, chat, "r")
                        if key not in SEEN:
                            SEEN[key] = set(ids)
                        else:
                            new = [i for i in ids if i not in SEEN[key]]
                            for i in new:
                                try: await app.send_reaction(chat, i, emoji)
                                except Exception as e: log.debug("react %s: %r", tg_id, e)
                                SEEN[key].add(i)
                            if new:
                                emit("watcher", owner, chat=str(chat), detail=f"reacted {len(new)} via {tg_id}")
                    await asyncio.sleep(0.5)
        except Exception as e:
            log.exception("watcher cycle failed")
            if BOT_INSTANCE:
                await _notify_error(BOT_INSTANCE, OWNER_ID, e, "watcher cycle")
        await asyncio.sleep(10)


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
    return InlineKeyboardMarkup(inline_keyboard=[[ibtn(*b) for b in r] for r in rows])


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
    e, name = label.split(" ", 1)
    return name if norm(e) in PREMIUM else label


def ANY(label):
    return F.text.in_({label, label.split(" ", 1)[1]})


def rbtn(label, style=None):
    return KeyboardButton(text=btext(label), style=style, icon_custom_emoji_id=PREMIUM.get(norm(label.split(" ", 1)[0])))


def hd(icon, title, sub=""):
    return f"✦ {icon} <b>{title.upper()}</b> ✦" + (f"\n<i>{sub}</i>" if sub else "") + "\n▬▬▬▬▬▬▬▬▬▬▬▬\n\n"


def tree(*lines, expand=False):
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
    async def __call__(self, make_request, bot, method):
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
                for emoji, cid in used_ids:
                    if PREMIUM.get(emoji) == cid:
                        PREMIUM.pop(emoji, None)
                        db.execute("DELETE FROM premium WHERE emoji=? AND cid=?", (emoji, cid))
                db.commit()
                method.text = original
                return await make_request(bot, method)
            raise


_BOLD_MAP = {}
for _i, _c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
    _BOLD_MAP[_c] = chr(0x1D400 + _i)
for _i, _c in enumerate("abcdefghijklmnopqrstuvwxyz"):
    _BOLD_MAP[_c] = chr(0x1D41A + _i)
for _i, _c in enumerate("0123456789"):
    _BOLD_MAP[_c] = chr(0x1D7CE + _i)
_DEV_ESC = html.escape(DEV_CONTACT) if DEV_CONTACT else ""
_PROTECT_RE = re.compile(r"(<[^>]+>|@[A-Za-z0-9_]{1,32}" + (r"|" + re.escape(_DEV_ESC) if _DEV_ESC else "") + r")")


def to_bold(text):
    parts = _PROTECT_RE.split(text)
    out = []
    for part in parts:
        if (part.startswith("<") and part.endswith(">")) or (_DEV_ESC and part == _DEV_ESC):
            out.append(part)
        else:
            out.append("".join(_BOLD_MAP.get(ch, ch) for ch in part))
    return "".join(out)


class BoldMW(BaseRequestMiddleware):
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
        if is_msg and event.text and event.text.split()[0].split("@")[0] == "/start":
            return await handler(event, data)
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
        if st in ("rejected", "revoked"):
            return await say("❌ Your access is not active.\n\n" + ACCESS_KEY_PROMPT, VIP_BTN)
        if st == "pending":
            return await say("⏳ Your request is still waiting for the owner's approval.\n\n" + ACCESS_KEY_PROMPT, VIP_BTN)
        db.execute("INSERT OR REPLACE INTO users(user_id,name,username,status) VALUES(?,?,?,?)",
                   (u.id, u.full_name, u.username or "", "pending"))
        db.commit()
        emit("access_request", u.id, detail=f"{u.full_name} @{u.username or ''}")
        await say("🔐 Bot authorization request sent!\n\nWaiting for the bot owner to approve you.\n\n" + ACCESS_KEY_PROMPT, VIP_BTN)
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
                label = raw.splitlines()[0][:100] if raw.startswith("/") or raw in {
                    B_CLIENTS, B_ADD, B_JVC, B_LVC, B_JOIN, B_REACT, B_AR, B_AV,
                    B_ACC, B_ADMIN, B_STATUS, B_RESTART, B_UPTIME, B_FIND,
                    B_SETTINGS, B_INFO, B_PING, B_CLEARAUTO, B_BROADCAST,
                    B_ADMTOOLS, B_PIN, B_CACHE, B_DEV, B_CUSTOM_INBOX
                } else ("media/message" if event.photo or event.video or event.document or event.voice else "")
                if label:
                    log_activity(user.id, "interaction", label)
                    emit("cmd", user.id, detail=label)
            elif isinstance(event, CallbackQuery) and event.data:
                log_activity(user.id, "button", event.data[:100])
                emit("cmd", user.id, detail=f"btn:{event.data[:80]}")
        return await handler(event, data)


dp = Dispatcher(storage=MemoryStorage())
dp.message.outer_middleware(ActivityAuditMW())
dp.callback_query.outer_middleware(ActivityAuditMW())
dp.message.outer_middleware(Guard())
dp.callback_query.outer_middleware(Guard())

_ERROR_ALERT_COOLDOWN = {}
_ERROR_ALERT_WINDOW = 60


def _safe_error_text(exc):
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
    emit("error", user_id, ok=0, detail=f"{where}: {details}")
    try:
        await bot.send_message(
            OWNER_ID,
            "🚨 <b>Bot error alert</b>\n"
            f"📍 জায়গা: <code>{h(where)}</code>\n"
            f"👤 User ID: <code>{user_id or 'unknown'}</code>\n"
            f"⚠️ সমস্যা: <code>{h(details)}</code>")
    except Exception:
        log.exception("could not send error alert to owner")


@dp.errors()
async def global_error_handler(event: ErrorEvent):
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
            await message.answer("⚠️ দুঃখিত! বটে সাময়িক সমস্যা হয়েছে। অ্যাডমিনকে জানানো হয়েছে।")
        except Exception:
            pass
    elif callback:
        user_id = callback.from_user.id
        bot = callback.bot
        try:
            await callback.answer("⚠️ সাময়িক সমস্যা হয়েছে।", show_alert=True)
        except Exception:
            pass
    if bot:
        await _notify_error(bot, user_id, exc, "aiogram update")
    return True


async def self_healing_supervisor(bot):
    while True:
        try:
            rows = db.execute("SELECT id, session, owner FROM clients").fetchall()
            for tg_id, encrypted_session, owner in rows:
                current = RUNNING.get(tg_id)
                if current is not None and getattr(current[0], "is_connected", False):
                    continue
                if current is not None:
                    await stop_client(tg_id)
                try:
                    session = fernet.decrypt(encrypted_session).decode()
                    await start_client(session, tg_id, owner or OWNER_ID)
                    emit("recovery", owner or OWNER_ID, detail=f"client {tg_id} recovered")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    emit("error", owner or OWNER_ID, ok=0, detail=f"recovery {tg_id}: {exc}")
                    await _notify_error(bot, OWNER_ID, exc, f"client recovery {tg_id}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await _notify_error(bot, OWNER_ID, exc, "self-healing supervisor")
        await asyncio.sleep(20)


async def _run_guarded_background(name, factory, bot):
    delay = 5
    while True:
        try:
            await factory()
            raise RuntimeError(f"background task {name} stopped unexpectedly")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await _notify_error(bot, OWNER_ID, exc, f"background task {name}")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 120)
        else:
            delay = 5


async def _runtime_health_monitor(bot):
    while True:
        try:
            db.execute("SELECT 1").fetchone()
            disconnected = [tid for tid, (app, _calls, _owner) in list(RUNNING.items())
                            if not getattr(app, "is_connected", False)]
            for tid in disconnected:
                exc = ConnectionError(f"Telegram client {tid} disconnected")
                await _notify_error(bot, OWNER_ID, exc, "client health check")
            db.execute("DELETE FROM events WHERE ts < ?", (int(time.time()) - 30 * 86400,))
            db.commit()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await _notify_error(bot, OWNER_ID, exc, "runtime health check")
        await asyncio.sleep(30)


@dp.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    uid = m.from_user.id
    if uid != OWNER_ID:
        row = db.execute("SELECT status FROM users WHERE user_id=?", (uid,)).fetchone()
        status = row[0] if row else None
        if status == "banned":
            return await m.answer("⛔ You are permanently banned from this bot by the owner.")
        if status != "approved":
            if status is None:
                db.execute("INSERT OR REPLACE INTO users(user_id,name,username,status) VALUES(?,?,?,?)",
                           (uid, m.from_user.full_name, m.from_user.username or "", "pending"))
                db.commit()
                emit("access_request", uid, detail=f"{m.from_user.full_name} @{m.from_user.username or ''}")
                try:
                    uname = f"@{h(m.from_user.username)}" if m.from_user.username else "—"
                    await m.bot.send_message(
                        OWNER_ID,
                        f"🔔 New access request\n\n👤 Name: {h(m.from_user.full_name)}\n🔗 Username: {uname}\n🆔 ID: {uid}",
                        reply_markup=ikb([("✅ Accept", f"adm:ap:{uid}", "success"), ("❌ Reject", f"adm:rj:{uid}", "danger")]))
                except Exception as e:
                    log.warning("owner notify failed: %r", e)
            return await m.answer(ACCESS_KEY_PROMPT, reply_markup=VIP_BTN)
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
                   "Send ONE message with premium emoji in exactly this order:\n\n"
                   f"{lst}\n\n/cancel to abort.", reply_markup=back_kb())


@dp.message(S.emoji_pack)
async def setemoji_do(m: Message, state: FSMContext):
    ents = [e for e in (m.entities or []) if e.type == "custom_emoji"]
    if not ents:
        return await m.answer("❌ No premium emoji found. Try again or /cancel.")
    for e, (slot, _d) in zip(ents, SLOTS):
        PREMIUM[norm(slot)] = e.custom_emoji_id
        db.execute("INSERT OR REPLACE INTO premium(emoji,cid) VALUES(?,?)", (norm(slot), e.custom_emoji_id))
    db.commit()
    await state.clear()
    n = min(len(ents), len(SLOTS))
    await m.answer(hd("🎉", "Premium emoji saved", f"{n} of {len(SLOTS)} set"), reply_markup=kb(m.from_user.id))


@dp.message(Command("setpack"))
async def setpack(m: Message):
    if m.from_user.id != OWNER_ID:
        return
    args = (m.text or "").split()[1:]
    names = [re.sub(r".*/(?:addemoji|addstickers)/", "", a).strip("/ ") for a in args]
    if not names:
        return await m.answer("Send:\n<code>/setpack https://t.me/addemoji/PackName</code>")
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
        return await m.answer("❌ Could not read that pack.\n" + "\n".join(errs))
    chosen = {}
    for slot, _d in SLOTS:
        if norm(slot) in by_emoji:
            chosen[norm(slot)] = by_emoji[norm(slot)]
    exact = len(chosen)
    spare = [c for c in pool if c not in chosen.values()]
    for slot, _d in SLOTS:
        if norm(slot) not in chosen and spare:
            chosen[norm(slot)] = spare.pop(0)
    for e, cid in by_emoji.items():
        if e and e not in chosen:
            chosen[e] = cid
    for e, cid in chosen.items():
        PREMIUM[e] = cid
        db.execute("INSERT OR REPLACE INTO premium(emoji,cid) VALUES(?,?)", (e, cid))
    db.commit()
    await m.answer(hd("🎉", "Premium pack applied", f"{len(chosen)} emoji ready") +
                   f"💎 Exact — <b>{exact}</b>\n⚡ Filled — <b>{len(chosen) - exact}</b>", reply_markup=kb(m.from_user.id))


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
    await m.answer("\n".join(lines))


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
        return await m.answer(hd("📱", "Accounts", "nothing here yet") + "Tap <b>➕ Add Account</b>.")
    lines = []
    for i, (c, n, p, o) in enumerate(rows, 1):
        shared = uid != OWNER_ID and o == OWNER_ID
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
    await m.answer(hd("📱", f"Accounts · {len(rows)}", f"🟢 {on} online") +
                   f"<blockquote expandable>{body}</blockquote>\n\n🧹 <code>/remove 3</code>")


@dp.message(ANY(B_ADD))
async def add_client_btn(m: Message):
    await m.answer(hd("➕", "Add account", "phone number + OTP, or QR") + "Tap a button.",
                   reply_markup=ikb([("📱 Add by Phone Number", "add_phone", "primary")],
                                    [("🔳 Add by QR (any country)", "add_qr", "primary")]))


def login_client(uid):
    return Client(f"login{uid}", api_id=API_ID, api_hash=API_HASH, in_memory=True, proxy=_proxy(),
                  device_model="Samsung Galaxy S23", system_version="Android 13",
                  app_version="10.14.5", lang_code="en")


def code_where(sent):
    t = str(getattr(sent.type, "name", sent.type))
    n = str(getattr(sent.next_type, "name", sent.next_type)) if sent.next_type else None
    where = {"APP": "📲 Telegram app", "SMS": "💬 SMS", "CALL": "📞 phone call",
             "FLASH_CALL": "📞 flash call", "MISSED_CALL": "📞 missed call",
             "FRAGMENT_SMS": "💎 Fragment SMS", "FIREBASE_SMS": "💬 SMS", "EMAIL_CODE": "📧 email"}.get(t, t)
    txt = f"Code sent via: {where}"
    if n:
        txt += f"\nNext option after /resend: {n}"
    return txt


@dp.callback_query(F.data == "add_phone")
async def add_phone(c: CallbackQuery, state: FSMContext):
    await state.set_state(S.phone)
    await c.message.answer("📱 Send the Telegram phone number with country code, e.g. +1234567890.\n\n/cancel to abort.",
                           reply_markup=back_kb())
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
        "🔳 <b>QR login</b>\n\nA QR image is coming. On the phone where this account is logged in:\n"
        "Telegram → Settings → Devices → <b>Link Desktop Device</b> → scan the QR.\n\n/cancel to abort.",
        reply_markup=back_kb())


async def qr_flow(msg: Message, state: FSMContext, p, uid):
    import qrcode
    app = p["app"]
    try:
        ql = QRLogin(app, [])
        await ql.recreate()
        for _ in range(8):
            img = qrcode.make(ql.url)
            buf = io.BytesIO(); img.save(buf, format="PNG")
            photo = await msg.answer_photo(BufferedInputFile(buf.getvalue(), "qr.png"),
                                           caption="📷 Scan this QR (expires in ~30s)")
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
        return await m.answer("Usage: /ban USER_ID")
    uid = int(args[0])
    if uid == OWNER_ID:
        return await m.answer("❌ You can't ban yourself.")
    db.execute("INSERT INTO users(user_id,name,username,status) VALUES(?,?,?,'banned') "
               "ON CONFLICT(user_id) DO UPDATE SET status='banned'", (uid, str(uid), ""))
    db.execute("DELETE FROM vip_users WHERE user_id=?", (uid,))
    db.commit()
    emit("ban", uid, ok=0, detail="banned by owner")
    try: await m.bot.send_message(uid, "⛔ You have been permanently banned from this bot by the owner.")
    except Exception: pass
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
    db.execute("UPDATE users SET status='rejected' WHERE user_id=?", (uid,))
    db.commit()
    emit("unban", uid, detail="unbanned by owner")
    try: await m.bot.send_message(uid, "✅ Ban lifted. Send /start to request access again.")
    except Exception: pass
    await m.answer(f"✅ User {uid} unbanned.")


@dp.message(Command("deleteuser"))
async def deleteuser_cmd(m: Message):
    if m.from_user.id != OWNER_ID:
        return
    args = (m.text or "").split()[1:]
    if not args or not args[0].lstrip("-").isdigit():
        return await m.answer("Usage: /deleteuser USER_ID")
    uid = int(args[0])
    if uid == OWNER_ID:
        return await m.answer("❌ Owner account cannot be deleted.")
    exists = db.execute(
        "SELECT 1 FROM users WHERE user_id=? UNION SELECT 1 FROM clients WHERE owner=? "
        "UNION SELECT 1 FROM vip_users WHERE user_id=? UNION SELECT 1 FROM auto2 WHERE owner=? LIMIT 1",
        (uid, uid, uid, uid)).fetchone()
    if not exists:
        return await m.answer("ℹ️ No records found.")
    client_ids = [r[0] for r in db.execute("SELECT id FROM clients WHERE owner=?", (uid,)).fetchall()]
    for tg_id, (_app, _calls, owner) in list(RUNNING.items()):
        if owner == uid and tg_id not in client_ids:
            client_ids.append(tg_id)
    for tg_id in client_ids:
        await stop_client(tg_id)
    pending = PENDING.pop(uid, None)
    if pending:
        task = pending.get("task")
        if task and not task.done():
            task.cancel()
        try: await pending["app"].disconnect()
        except Exception: pass
    try:
        db.execute("BEGIN")
        db.execute("DELETE FROM clients WHERE owner=?", (uid,))
        db.execute("DELETE FROM auto2 WHERE owner=?", (uid,))
        db.execute("DELETE FROM vip_users WHERE user_id=?", (uid,))
        db.execute("UPDATE access_keys SET status='revoked', used_by=NULL WHERE used_by=?", (uid,))
        db.execute("DELETE FROM users WHERE user_id=?", (uid,))
        db.commit()
    except Exception:
        db.rollback()
        return await m.answer("❌ Delete failed.")
    for key in [k for k in list(AUTO_REACT) if k[0] == uid]:
        AUTO_REACT.pop(key, None)
    for key in [k for k in list(AUTO_VIEW) if k[0] == uid]:
        AUTO_VIEW.discard(key)
    emit("deleteuser", uid, detail="deleted by owner")
    await m.answer(f"🗑️ User <code>{uid}</code> deleted.")


@dp.message(Command("remove"))
async def remove_cmd(m: Message):
    rows = client_rows(m.from_user.id)
    nums = [int(x) for x in (m.text or "").split()[1:] if x.isdigit()]
    nums = [n for n in nums if 1 <= n <= len(rows)]
    if not nums:
        return await m.answer("Usage: /remove NUMBER")
    names = []
    for n in sorted(set(nums)):
        cid, name, _p, own = rows[n - 1]
        if m.from_user.id != OWNER_ID and own != m.from_user.id:
            continue
        await stop_client(cid)
        db.execute("DELETE FROM clients WHERE id=?", (cid,))
        names.append(h(name))
        emit("account_removed", m.from_user.id, detail=f"{name} · {cid}")
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
        return await m.answer(f"⏳ Wait {e.value} seconds before retry.")
    except Exception as e:
        try: await app.disconnect()
        except Exception: pass
        return await m.answer(f"❌ Could not send code: {h(e.__class__.__name__)}: {h(e)}")
    PENDING[m.from_user.id] = dict(app=app, phone=phone, hash=sent.phone_code_hash)
    await state.set_state(S.otp)
    await m.answer(f"🔑 Code requested for {h(phone)}\n\n{h(code_where(sent))}\n\n"
                   "Type the code with dashes: <code>1-2-3-4-5</code>\n"
                   "/resend for the next method.\n\n/cancel to abort.")


@dp.message(Command("resend"), S.otp)
async def resend_code_cmd(m: Message):
    p = PENDING.get(m.from_user.id)
    if not p:
        return await m.answer("❌ No login in progress.")
    try:
        sent = await p["app"].resend_code(p["phone"], p["hash"])
    except FloodWait as e:
        return await m.answer(f"⏳ Wait {e.value} seconds.")
    except SendCodeUnavailable:
        return await m.answer("❌ No more delivery methods. Try QR login.")
    except Exception as e:
        return await m.answer(f"❌ Resend failed: {h(e.__class__.__name__)}: {h(e)}")
    p["hash"] = sent.phone_code_hash
    await m.answer(f"🔁 New code requested.\n\n{h(code_where(sent))}")


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
    emit("account_added", uid, ok=1, detail=f"{name} · Telegram ID {me.id} · {p['phone']}")
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
        return await m.answer("❌ Couldn't find a valid code.")
    try:
        await p["app"].sign_in(p["phone"], p["hash"], code)
    except SessionPasswordNeeded:
        await state.set_state(S.pwd)
        return await m.answer("🔐 2-Step Verification is enabled.\n\nSend your account password.")
    except (PhoneCodeInvalid, PhoneCodeExpired) as e:
        return await m.answer(f"❌ {e.__class__.__name__}.")
    except Exception as e:
        return await m.answer(f"❌ Login failed: {h(e)}")
    await finish_login(m, state, p)


@dp.message(S.pwd)
async def got_pwd(m: Message, state: FSMContext):
    p = PENDING.get(m.from_user.id)
    try:
        await p["app"].check_password(m.text.strip())
    except PasswordHashInvalid:
        return await m.answer("❌ Wrong password.")
    except Exception as e:
        return await m.answer(f"❌ Login failed: {h(e)}")
    try: await m.delete()
    except Exception: pass
    await finish_login(m, state, p)


@dp.message(ANY(B_JOIN))
async def join_ask(m: Message, state: FSMContext):
    await state.set_state(S.join)
    await m.answer(hd("🚀", "Join channel") + "Send the username or invite link.\n\n/cancel to abort.",
                   reply_markup=back_kb())


@dp.message(S.join)
async def join_do(m: Message, state: FSMContext):
    target = parse_target(m.text)
    await state.clear()
    await m.answer("⏳ Joining...")
    ok, fail, secs = await for_all(m.from_user.id, lambda app, _: app.join_chat(target))
    emit("join_channel", m.from_user.id, chat=str(target), ok=(fail == 0), detail=f"ok={ok} fail={fail}")
    await m.answer(report(ok, fail, secs, "Joined"), reply_markup=kb(m.from_user.id))


@dp.message(ANY(B_JVC))
async def jvc_ask(m: Message, state: FSMContext):
    await state.set_state(S.jvc)
    await m.answer(hd("🎧", "Voice join") + "Send username or Chat ID.\n\n⚠️ Invite links not supported here — use Chat ID.\n\n/cancel to abort.",
                   reply_markup=back_kb())


@dp.message(S.jvc)
async def jvc_do(m: Message, state: FSMContext):
    chat = parse_target(m.text)
    if isinstance(chat, str) and chat.startswith("https://t.me/+"):
        return await m.answer("⚠️ Invite links aren't supported here. Send the Chat ID.")
    await state.clear()
    await m.answer("⏳ Joining voice chat...")

    async def fn(app, calls):
        await app.get_chat(chat)
        await calls.play(chat)

    ok, fail, secs = await for_all(m.from_user.id, fn)
    details = f"chat={chat}; success={ok}; failed={fail}"
    log_activity(m.from_user.id, "voice_join", details)
    emit("voice_join", m.from_user.id, chat=str(chat), ok=(fail == 0), detail=details)
    await notify_owner_activity(m.bot, m.from_user, "voice_join", details)
    await m.answer(report(ok, fail, secs, "Joined VC"), reply_markup=kb(m.from_user.id))


@dp.message(ANY(B_LVC))
async def lvc_ask(m: Message, state: FSMContext):
    await state.set_state(S.lvc)
    await m.answer(hd("🔇", "Voice leave") + "Send username or Chat ID.\n\n/cancel to abort.", reply_markup=back_kb())


@dp.message(S.lvc)
async def lvc_do(m: Message, state: FSMContext):
    chat = parse_target(m.text)
    await state.clear()

    async def fn(app, calls):
        await calls.leave_call(chat)

    ok, fail, secs = await for_all(m.from_user.id, fn)
    details = f"chat={chat}; success={ok}; failed={fail}"
    log_activity(m.from_user.id, "voice_leave", details)
    emit("voice_leave", m.from_user.id, chat=str(chat), ok=(fail == 0), detail=details)
    await notify_owner_activity(m.bot, m.from_user, "voice_leave", details)
    await m.answer(report(ok, fail, secs, "Left VC"), reply_markup=kb(m.from_user.id))


@dp.message(ANY(B_REACT))
async def react_ask(m: Message, state: FSMContext):
    await state.set_state(S.react_link)
    await m.answer(hd("💫", "React") + "Send the post link, e.g. https://t.me/channel/123\n\n/cancel to abort.",
                   reply_markup=back_kb())


@dp.message(S.react_link)
async def react_link(m: Message, state: FSMContext):
    post = parse_post(m.text)
    if not post:
        return await m.answer("❌ Invalid post link.")
    await state.update_data(post=post)
    await state.set_state(S.react_emoji)
    await m.answer("Now send the emoji (e.g. 👍 💫 🔥).")


@dp.message(S.react_emoji)
async def react_do(m: Message, state: FSMContext):
    chat, mid = (await state.get_data())["post"]
    emoji = m.text.strip()
    await state.clear()

    async def fn(app, _):
        await app.get_chat(chat)
        await app.send_reaction(chat, mid, emoji)

    ok, fail, secs = await for_all(m.from_user.id, fn)
    emit("react_post", m.from_user.id, chat=str(chat), ok=(fail == 0), detail=f"post={mid} emoji={emoji}")
    await m.answer(report(ok, fail, secs, "Reacted"), reply_markup=kb(m.from_user.id))


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
    await m.answer(hd("⚡", "Auto react") + f"{auto_list(m.from_user.id)}\n\nSend channel username/Chat ID.\n"
                   "Add <code>off</code> to disable.\n\n/cancel to abort.", reply_markup=back_kb())


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
        emit("auto_react", uid, chat=str(chat), detail="disabled")
        return await m.answer("⚡ Auto React disabled.", reply_markup=kb(uid))
    await state.update_data(chat=chat)
    await state.set_state(S.ar_emoji)
    await m.answer("Send the emoji.")


@dp.message(S.ar_emoji)
async def ar_emoji(m: Message, state: FSMContext):
    uid = m.from_user.id
    chat = (await state.get_data())["chat"]
    emoji = m.text.strip()
    AUTO_REACT[(uid, chat)] = emoji
    db.execute("INSERT OR REPLACE INTO auto2 VALUES(?,'react',?,?)", (uid, str(chat), emoji)); db.commit()
    await state.clear()
    emit("auto_react", uid, chat=str(chat), detail=f"enabled {emoji}")
    await m.answer(f"✅ Auto React enabled for {chat} with {h(emoji)}", reply_markup=kb(uid))


@dp.message(ANY(B_AV))
async def av_ask(m: Message, state: FSMContext):
    await state.set_state(S.av_chat)
    await m.answer(hd("👀", "Auto view") + f"{auto_list(m.from_user.id)}\n\nSend channel username/Chat ID.\n"
                   "Add <code>off</code> to disable.\n\n/cancel to abort.", reply_markup=back_kb())


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
    emit("auto_view", uid, chat=str(chat), detail="disabled" if off else "enabled")
    await m.answer(f"👀 Auto View {'disabled' if off else 'enabled'} for {chat}", reply_markup=kb(uid))
    if off:
        for k in [k for k in SEEN if k[1] == chat and k[2] == "v"]:
            SEEN.pop(k, None)
        return
    ok = fail = 0
    err = ""
    for tg_id, (app, _c, _o) in user_clients(uid):
        try:
            SEEN[(tg_id, chat, "v")] = set(await recent_ids(app, chat))
            ok += 1
        except Exception as e:
            fail += 1
            err = repr(e)
    await m.answer(f"👀 Ready\n✅ OK: {ok}\n❌ Failed: {fail}" + (f"\n\n{h(err)}" if err else ""))


CUSTOM_INBOX_LAST_SEND = {}

@dp.message(ANY(B_CUSTOM_INBOX))
async def custom_inbox_start(m: Message, state: FSMContext):
    await state.clear()
    clients = user_clients(m.from_user.id)
    if not clients:
        return await m.answer(hd("📩", "Custom SMS Inbox") + "❌ No connected accounts online.", reply_markup=kb(m.from_user.id))
    rows = []
    for tg_id, (app, _calls, _owner) in clients[:20]:
        try:
            me = await app.get_me()
            label = f"{me.first_name or 'Account'} (@{me.username})" if me.username else f"Account · {tg_id}"
        except Exception:
            label = f"Account · {tg_id}"
        rows.append([(label[:60], f"csi:account:{tg_id}", "primary")])
    rows.append([("🌐 ALL ACCOUNTS থেকে মেসেজ পাঠান", "csi:all", "success")])
    await m.answer(hd("📩", "Custom SMS Inbox", "choose one account or all accounts") +
                   "শুধু সম্মত recipient-কে মেসেজ পাঠান।", reply_markup=ikb(*rows))


@dp.callback_query(F.data == "csi:all")
async def custom_inbox_choose_all(c: CallbackQuery, state: FSMContext):
    clients = user_clients(c.from_user.id)
    ids = [tg_id for tg_id, (app, _calls, _owner) in clients if getattr(app, "is_connected", False)]
    if not ids:
        return await c.answer("No connected accounts online.", show_alert=True)
    await state.clear()
    await state.update_data(custom_tg_ids=ids, custom_mode="all")
    await state.set_state(S.custom_target)
    await c.message.answer(f"🌐 ALL ACCOUNTS · <b>{len(ids)}</b>\n\nRecipient username দিন।\n\n/cancel to abort.",
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
        return await c.answer("Not allowed.", show_alert=True)
    app = allowed[tg_id][0]
    if not getattr(app, "is_connected", False):
        return await c.answer("Account offline.", show_alert=True)
    await state.clear()
    await state.update_data(custom_tg_id=tg_id, custom_tg_ids=[tg_id], custom_mode="single")
    await state.set_state(S.custom_target)
    await c.message.answer("👤 Recipient username দিন।\n\n/cancel to abort.", reply_markup=back_kb())
    await c.answer()


@dp.message(S.custom_target)
async def custom_inbox_target(m: Message, state: FSMContext):
    raw = (m.text or "").strip()
    username = raw.removeprefix("https://t.me/").removeprefix("http://t.me/").removeprefix("t.me/").lstrip("@").split("/")[0]
    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
        return await m.answer("❌ Valid username দিন।")
    data = await state.get_data()
    ids = data.get("custom_tg_ids") or [data.get("custom_tg_id")]
    allowed = dict(user_clients(m.from_user.id))
    active = [tid for tid in ids if tid in allowed and getattr(allowed[tid][0], "is_connected", False)]
    if not active:
        await state.clear()
        return await m.answer("❌ Accounts offline.", reply_markup=kb(m.from_user.id))
    try:
        user = await allowed[active[0]][0].get_users(username)
        if getattr(user, "is_bot", False):
            return await m.answer("❌ Personal user account only.")
        if getattr(user, "is_deleted", False):
            return await m.answer("❌ Account not available.")
    except Exception as exc:
        return await m.answer(f"❌ Username not found: <code>{h(type(exc).__name__)}</code>")
    await state.update_data(custom_target_username=username, custom_target_name="@" + username, custom_tg_ids=active)
    await state.set_state(S.custom_payload)
    await m.answer(f"✅ Recipient: <b>@{h(username)}</b>\n\nএখন একটি মেসেজ পাঠান — Text, Photo, Video, Audio, Voice, Document, Animation, Video Note বা Sticker।",
                   reply_markup=back_kb())


@dp.message(S.custom_payload)
async def custom_inbox_payload(m: Message, state: FSMContext):
    data = await state.get_data()
    kind = None
    extension = None
    if m.text and not m.text.startswith("/"):
        payload = {"kind": "text", "text": m.text}
        preview = f"📝 Text: {h(m.text[:700])}"
    else:
        candidates = [("photo", m.photo[-1] if m.photo else None, ".jpg"),
                      ("video", m.video, ".mp4"), ("audio", m.audio, ".mp3"),
                      ("voice", m.voice, ".ogg"), ("video_note", m.video_note, ".mp4"),
                      ("document", m.document, ".bin"), ("animation", m.animation, ".mp4"),
                      ("sticker", m.sticker, ".webp")]
        media_obj = None
        for ck, obj, ext in candidates:
            if obj:
                kind, media_obj, extension = ck, obj, ext
                break
        if media_obj is None:
            return await m.answer("❌ Unsupported type.")
        original_name = getattr(media_obj, "file_name", None)
        if original_name:
            ext2 = os.path.splitext(original_name)[1].lower()
            if ext2 and len(ext2) <= 10:
                extension = ext2
        if kind == "sticker":
            if getattr(media_obj, "is_video", False): extension = ".webm"
            elif getattr(media_obj, "is_animated", False): extension = ".tgs"
        caption = m.caption or ""
        payload = {"kind": kind, "file_id": media_obj.file_id,
                   "extension": extension, "caption": caption,
                   "file_size": getattr(media_obj, "file_size", None)}
        size = payload.get("file_size")
        preview = f"📎 Media: <b>{h(kind)}</b>"
        if size: preview += f"\nSize: {size / (1024 * 1024):.2f} MB"
        if caption: preview += f"\nCaption: {h(caption[:500])}"
    await state.update_data(custom_payload=payload)
    ids = data.get("custom_tg_ids") or [data.get("custom_tg_id")]
    mode_line = f"🌐 ALL · {len(ids)} accounts" if data.get("custom_mode") == "all" else f"👤 Single · {ids[0]}"
    await m.answer(hd("🔎", "Confirm message") + f"Mode: {mode_line}\nTo: <b>{h(data.get('custom_target_name'))}</b>\n\n{preview}\n\nনিশ্চিত করুন: recipient সম্মত।",
                   reply_markup=ikb([("✅ Confirm & Send", "csi:send", "success"), ("❌ Cancel", "csi:abort", "danger")]))


@dp.callback_query(F.data == "csi:abort")
async def custom_inbox_abort(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.answer("❎ Cancelled.", reply_markup=kb(c.from_user.id))
    await c.answer("Cancelled")


@dp.callback_query(F.data == "csi:send")
async def custom_inbox_send(c: CallbackQuery, state: FSMContext):
    uid = c.from_user.id
    now = time.time()
    if now - CUSTOM_INBOX_LAST_SEND.get(uid, 0) < 10:
        return await c.answer("১০ সেকেন্ড অপেক্ষা করুন।", show_alert=True)
    data = await state.get_data()
    ids = data.get("custom_tg_ids") or [data.get("custom_tg_id")]
    target_username = data.get("custom_target_username")
    payload = data.get("custom_payload") or {}
    allowed = dict(user_clients(uid))
    ids = [tid for tid in ids if tid in allowed and getattr(allowed[tid][0], "is_connected", False)]
    if not ids or not target_username or not payload:
        await state.clear()
        return await c.answer("Session expired.", show_alert=True)
    await c.answer("Sending…")
    temp_path = None
    ok, fail, errors = [], [], []
    try:
        kind = payload.get("kind")
        if kind != "text":
            file_info = await c.bot.get_file(payload["file_id"])
            extension = payload.get("extension") or ".bin"
            fd, temp_path = tempfile.mkstemp(prefix="ci_", suffix=extension)
            os.close(fd)
            await c.bot.download_file(file_info.file_path, destination=temp_path)
        for index, tg_id in enumerate(ids):
            app = allowed[tg_id][0]
            try:
                recipient = await app.get_users(target_username)
                if getattr(recipient, "is_bot", False) or getattr(recipient, "is_deleted", False):
                    raise ValueError("Not an available personal account")
                tid = recipient.id
                if kind == "text":
                    await app.send_message(tid, payload["text"], parse_mode=None, disable_web_page_preview=True)
                else:
                    caption = payload.get("caption") or None
                    if kind == "photo": await app.send_photo(tid, temp_path, caption=caption, parse_mode=None)
                    elif kind == "video": await app.send_video(tid, temp_path, caption=caption, parse_mode=None, supports_streaming=True)
                    elif kind == "audio": await app.send_audio(tid, temp_path, caption=caption, parse_mode=None)
                    elif kind == "voice": await app.send_voice(tid, temp_path, caption=caption, parse_mode=None)
                    elif kind == "video_note": await app.send_video_note(tid, temp_path)
                    elif kind == "document": await app.send_document(tid, temp_path, caption=caption, parse_mode=None)
                    elif kind == "animation": await app.send_animation(tid, temp_path, caption=caption, parse_mode=None)
                    elif kind == "sticker":
                        if extension == ".tgs": raise ValueError("TGS sticker not supported")
                        await app.send_sticker(tid, temp_path)
                ok.append(tg_id)
            except FloodWait as exc:
                fail.append(tg_id)
                errors.append(f"{tg_id}: FloodWait {int(exc.value)}s")
            except Exception as exc:
                fail.append(tg_id)
                errors.append(f"{tg_id}: {type(exc).__name__}: {exc}")
            if index < len(ids) - 1:
                await asyncio.sleep(max(1, DELAY))
        CUSTOM_INBOX_LAST_SEND[uid] = time.time()
        await state.clear()
        result = (f"✅ <b>Done</b>\n\n👤 {h(data.get('custom_target_name'))}\n"
                  f"📤 OK: <b>{len(ok)}</b>\n❌ Fail: <b>{len(fail)}</b>\n📱 Total: <b>{len(ids)}</b>")
        if errors:
            result += "\n\n⚠️ Errors:\n<code>" + h("\n".join(errors)[:2500]) + "</code>"
        emit("custom_inbox", uid, ok=(not fail), detail=f"to=@{target_username} ok={len(ok)} fail={len(fail)}")
        await c.message.answer(result, reply_markup=kb(uid))
    except Exception as exc:
        await c.message.answer(f"❌ Failed: <code>{h(type(exc).__name__)}: {h(exc)}</code>")
    finally:
        if temp_path and os.path.exists(temp_path):
            try: os.remove(temp_path)
            except OSError: pass


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
                        f"⚡ Auto React — <b>{ar}</b>", f"👀 Auto View — <b>{av}</b>"),
                   reply_markup=kb(uid))


@dp.message(ANY(B_UPTIME))
async def uptime_btn(m: Message):
    secs = int(time.time() - BOOT_TIME)
    d, rem = divmod(secs, 86400)
    hrs, rem = divmod(rem, 3600)
    mins, sec = divmod(rem, 60)
    parts = ([f"{d}d"] if d else []) + ([f"{hrs}h"] if hrs or d else []) + [f"{mins}m", f"{sec}s"]
    await m.answer(hd("🕒", "Uptime") + tree("🕒 Running — <b>" + " ".join(parts) + "</b>"),
                   reply_markup=kb(m.from_user.id))


@dp.message(ANY(B_FIND))
async def find_ask(m: Message, state: FSMContext):
    await state.set_state(S.find_user)
    await m.answer(hd("🔍", "Find user") + "Send ID or @username.\n\n/cancel to abort.", reply_markup=back_kb())


@dp.message(S.find_user)
async def find_do(m: Message, state: FSMContext):
    await state.clear()
    q = (m.text or "").strip().lstrip("@")
    if q.isdigit():
        row = db.execute("SELECT user_id, name, username, status FROM users WHERE user_id=?", (int(q),)).fetchone()
    else:
        row = db.execute("SELECT user_id, name, username, status FROM users WHERE username=? COLLATE NOCASE", (q,)).fetchone()
    if not row:
        return await m.answer("❌ No user found.", reply_markup=kb(m.from_user.id))
    uid, name, uname, status = row
    n_acc = count("SELECT COUNT(*) FROM clients WHERE owner=?", uid)
    vip = "Yes" if is_vip(uid) else "No"
    icons = {"approved": "✅", "rejected": "❌", "revoked": "🚫", "banned": "⛔", "pending": "⏳"}
    await m.answer(hd("🔍", "User found") +
                   tree(f"👤 {h(name or '—')}", f"🔗 @{h(uname) if uname else '—'}",
                        f"🆔 {uid}", f"{icons.get(status, '•')} {h(status)}",
                        f"💎 VIP: {vip}", f"📱 Accounts: {n_acc}") +
                   f"\n\n🧰 <code>/ban {uid}</code> · <code>/unban {uid}</code>", reply_markup=kb(m.from_user.id))


@dp.message(ANY(B_RESTART))
async def restart_btn(m: Message):
    uid = m.from_user.id
    allowed_ids = {tid for tid, _ in user_clients(uid)} | {r[0] for r in client_rows(uid)}
    rows = [r for r in db.execute("SELECT id, session, owner FROM clients").fetchall() if r[0] in allowed_ids]
    if not rows:
        return await m.answer("No accounts to restart.", reply_markup=kb(uid))
    await m.answer(f"⏳ Restarting {len(rows)} account(s)...")
    ok = fail = 0
    for tg_id, session_enc, owner in rows:
        try:
            await start_client(fernet.decrypt(session_enc).decode(), tg_id, owner)
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(DELAY)
    emit("restart", uid, ok=(fail == 0), detail=f"ok={ok} fail={fail}")
    await m.answer(f"🔄 Restart done.\n✅ OK: {ok}\n❌ Failed: {fail}", reply_markup=kb(uid))


@dp.message(ANY(B_BROADCAST))
async def broadcast_ask(m: Message, state: FSMContext):
    await state.set_state(S.broadcast)
    await m.answer(hd("📢", "Broadcast") + "Send the message.\n\n/cancel to abort.", reply_markup=back_kb())


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
    emit("broadcast", m.from_user.id, ok=(fail == 0), detail=f"ok={ok} fail={fail}")
    await m.answer(f"📢 Broadcast sent.\n✅ {ok}\n❌ {fail}", reply_markup=kb(m.from_user.id))


@dp.message(ANY(B_INFO))
async def my_info(m: Message):
    uid = m.from_user.id
    plan = "👑 Admin" if uid == OWNER_ID else ("💎 VIP" if is_vip(uid) else "🔐 Member")
    own = db.execute("SELECT COUNT(*) FROM clients WHERE owner=?", (uid,)).fetchone()[0]
    await m.answer(hd("🧮", "My info") +
                   tree(f"👤 {h(m.from_user.full_name)}",
                        f"🔗 @{h(m.from_user.username) if m.from_user.username else '—'}",
                        f"🆔 {uid}", plan, f"📱 My accounts: {own}"),
                   reply_markup=kb(uid))


@dp.message(ANY(B_PING))
async def ping_btn(m: Message):
    t0 = time.time()
    sent = await m.answer("🏓 Pinging...")
    ms = int((time.time() - t0) * 1000)
    try: await sent.edit_text(hd("🏓", "Pong", f"{ms} ms"))
    except Exception: pass
    await m.answer("👆 Response time above.", reply_markup=kb(m.from_user.id))


@dp.message(ANY(B_CLEARAUTO))
async def clearauto_ask(m: Message):
    uid = m.from_user.id
    n = sum(1 for (o, _c) in AUTO_REACT if o == uid) + sum(1 for (o, _c) in AUTO_VIEW if o == uid)
    if not n:
        return await m.answer("Nothing to clear.", reply_markup=kb(uid))
    await m.answer(hd("🧹", "Clear my auto settings", f"{n} rule(s)") +
                   "Are you sure?", reply_markup=ikb([("✅ Yes", "clra:yes", "danger"), ("❌ No", "clra:no")]))


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
    emit("auto_clear", uid, detail="all auto rules cleared")
    await c.message.edit_text("🧹 Cleared.")
    await c.answer("Cleared")


def v_settings(uid):
    is_admin = uid == OWNER_ID
    state_txt = "🔛 ON" if BOLD_MODE[0] else "🔚 OFF"
    txt = hd("⚙", "Settings") + tree(f"🔠 Bold — <b>{state_txt}</b>")
    if not is_admin:
        return txt + "\n\nOnly owner can change.", ikb([("🔙 Close", "set:close")])
    rows = [[("🔠 Toggle Bold", "set:bold")],
            [("💎 Emoji Manager", "set:manager"), ("💎 Set Pack (guide)", "set:spack")],
            [("♻ Reset Emoji", "set:reset")],
            [("🔙 Close", "set:close")]]
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
        try: await c.message.edit_text(txt, reply_markup=markup)
        except Exception: await c.message.answer(txt, reply_markup=markup)
        return await c.answer()
    elif act == "spack":
        return await c.answer("Send /setpack <link> in chat.", show_alert=True)
    elif act == "reset":
        PREMIUM.clear()
        db.execute("DELETE FROM premium"); db.commit()
    txt, markup = v_settings(c.from_user.id)
    try: await c.message.edit_text(txt, reply_markup=markup)
    except Exception: pass
    await c.answer("Updated")


def v_admtools():
    pend = count("SELECT COUNT(*) FROM users WHERE status='pending'")
    txt = hd("🧰", "Admin tools") + tree(f"📥 Pending — <b>{pend}</b>", f"🟢 Online — <b>{len(RUNNING)}</b>")
    rows = [[("📥 Requests", "adm:req", "primary"), ("🔑 VIP Keys", "adm:keys", "success")],
            [("👥 Users", "adm:users"), ("📱 Accounts", "adm:acc")],
            [("🔠 Toggle Bold", "set:bold")],
            [("🔙 Close", "set:close")]]
    return txt, ikb(*rows)


@dp.message(ANY(B_ADMTOOLS))
async def admtools_btn(m: Message):
    if m.from_user.id != OWNER_ID:
        return
    txt, markup = v_admtools()
    await m.answer(txt, reply_markup=markup)


@dp.message(ANY(B_PIN))
async def pin_ask(m: Message, state: FSMContext):
    if m.from_user.id != OWNER_ID:
        return
    await state.set_state(S.pin_post)
    await m.answer(hd("📌", "Pin post") + "Send post link, e.g. https://t.me/channel/123\n\n/cancel to abort.",
                   reply_markup=back_kb())


@dp.message(S.pin_post)
async def pin_do(m: Message, state: FSMContext):
    await state.clear()
    post = parse_post(m.text)
    if not post:
        return await m.answer("❌ Invalid link.", reply_markup=kb(m.from_user.id))
    chat, mid = post
    clients = user_clients(m.from_user.id)
    if not clients:
        return await m.answer("❌ No accounts.", reply_markup=kb(m.from_user.id))
    app = clients[0][1][0]
    try:
        await app.get_chat(chat)
        await app.pin_chat_message(chat, mid)
        emit("pin", m.from_user.id, chat=str(chat), detail=f"post={mid}")
        await m.answer("📌 Pinned.", reply_markup=kb(m.from_user.id))
    except Exception as e:
        await m.answer(f"❌ Pin failed: {h(e)}", reply_markup=kb(m.from_user.id))


@dp.message(ANY(B_CACHE))
async def cache_btn(m: Message):
    if m.from_user.id != OWNER_ID:
        return
    n = len(SEEN)
    SEEN.clear()
    emit("cache_clear", m.from_user.id, detail=f"cleared {n}")
    await m.answer(f"🧊 Cleared {n} cache entr{'y' if n == 1 else 'ies'}.", reply_markup=kb(m.from_user.id))


@dp.message(ANY(B_DEV))
async def dev_btn(m: Message):
    await m.answer(hd("👨‍💻", "Developer") + tree(f"📨 {h(DEV_CONTACT)}"), reply_markup=kb(m.from_user.id))


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
        nav.append(("⬅ Prev", f"emj:page:{page - 1}"))
    nav.append((f"{page + 1}/{total_pages}", "emj:noop"))
    if page < total_pages - 1:
        nav.append(("Next ➡", f"emj:page:{page + 1}"))
    rows.append(nav)
    rows.append([("🔙 Close", "set:close")])
    txt = (hd("💎", "Emoji manager", "🟢 = premium set · ⚪ = default") + "\n".join(lines) +
           "\n\nTap a row, then send one premium emoji.")
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
        try: await c.message.edit_text(txt, reply_markup=markup)
        except Exception: pass
        return await c.answer()
    if act == "pick":
        emoji = parts[2]
        await state.set_state(S.emoji_one)
        await state.update_data(slot_emoji=emoji)
        cur = PREMIUM.get(norm(emoji))
        await c.message.answer(f"💎 Setting for {emoji}\nCurrent: {'set' if cur else 'default'}\n\nSend ONE premium emoji.",
                               reply_markup=back_kb())
        return await c.answer()


@dp.message(S.emoji_one)
async def emoji_one_do(m: Message, state: FSMContext):
    data = await state.get_data()
    slot = data.get("slot_emoji")
    ents = [e for e in (m.entities or []) if e.type == "custom_emoji"]
    if not ents:
        return await m.answer("❌ Not a premium emoji.")
    cid = ents[0].custom_emoji_id
    PREMIUM[norm(slot)] = cid
    db.execute("INSERT OR REPLACE INTO premium(emoji,cid) VALUES(?,?)", (norm(slot), cid)); db.commit()
    await state.clear()
    await m.answer(f"✅ Saved for {slot}.", reply_markup=kb(m.from_user.id))
    txt, markup = v_emoji_manager(0)
    await m.answer(txt, reply_markup=markup)


@dp.message(ANY(B_ACC))
async def access(m: Message):
    uid = m.from_user.id
    if uid == OWNER_ID:
        n = db.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
        return await m.answer(hd("👑", "Access", "full control") + tree("👑 <b>Admin</b>", f"📱 Accounts — <b>{n}</b>"))
    own = db.execute("SELECT COUNT(*) FROM clients WHERE owner=?", (uid,)).fetchone()[0]
    if is_vip(uid):
        adm = db.execute("SELECT COUNT(*) FROM clients WHERE owner=?", (OWNER_ID,)).fetchone()[0]
        return await m.answer(hd("💎", "Access", "vip unlocked") + tree("💎 <b>VIP</b>", f"📱 Yours — <b>{own}</b>", f"👑 Admin — <b>{adm}</b>"))
    await m.answer(hd("🔐", "Access", "standard") + tree("🔐 <b>Member</b>", f"📱 Yours — <b>{own}</b>") + "\n\nHave a VIP key? Unlock all admin accounts.", reply_markup=VIP_BTN)


@dp.callback_query(F.data == "vip_enter")
async def vip_enter(c: CallbackQuery, state: FSMContext):
    await state.set_state(S.vip_key)
    await c.message.answer(hd("🔑", "VIP key") + "/cancel to abort.", reply_markup=back_kb())
    await c.answer()


@dp.message(S.vip_key)
async def vip_redeem(m: Message, state: FSMContext):
    key = (m.text or "").strip().upper()
    row = db.execute("SELECT status FROM access_keys WHERE key=?", (key,)).fetchone()
    try: await m.delete()
    except Exception: pass
    if not row or row[0] != "active":
        return await m.answer("❌ Invalid key, or /cancel.")
    u = m.from_user
    db.execute("UPDATE access_keys SET status='used', used_by=?, used_at=? WHERE key=?", (u.id, int(time.time()), key))
    db.execute("INSERT OR REPLACE INTO vip_users(user_id,key) VALUES(?,?)", (u.id, key))
    db.execute("INSERT INTO users(user_id,name,username,status) VALUES(?,?,?,'approved') "
               "ON CONFLICT(user_id) DO UPDATE SET status='approved'", (u.id, u.full_name, u.username or ""))
    db.commit()
    await state.clear()
    n = db.execute("SELECT COUNT(*) FROM clients WHERE owner=?", (OWNER_ID,)).fetchone()[0]
    emit("vip_used", u.id, detail=f"key {key} activated")
    await m.answer(hd("🎉", "VIP activated") + tree("💎 <b>VIP</b>", f"👑 Admin accounts — <b>{n}</b>"), reply_markup=kb(u.id))
    try: await m.bot.send_message(OWNER_ID, f"💎 Key <code>{key}</code> used by {h(u.full_name)} | 🆔 {u.id}")
    except Exception: pass


def count(sql, *a):
    return db.execute(sql, a).fetchone()[0]


def v_panel():
    pend = count("SELECT COUNT(*) FROM users WHERE status='pending'")
    appr = count("SELECT COUNT(*) FROM users WHERE status='approved'")
    vips = count("SELECT COUNT(*) FROM vip_users")
    txt = (hd("👑", "Admin console", "control everything") +
           tree(f"👥 Users — <b>{appr}</b>", f"💎 VIP — <b>{vips}</b>", f"⏳ Pending — <b>{pend}</b>",
                f"📱 Accounts — <b>{count('SELECT COUNT(*) FROM clients')}</b>",
                f"🟢 Online — <b>{len(RUNNING)}</b>"))
    return txt, ikb([(f"📥 Requests · {pend}", "adm:req", "primary"), ("🔑 VIP Keys", "adm:keys", "success")],
                    [("👥 Users", "adm:users"), ("📱 Accounts", "adm:acc")],
                    [("📈 User Activity", "adm:activity", "success")])


def v_req():
    rows = db.execute("SELECT user_id, name, username FROM users WHERE status='pending'").fetchall()
    txt = hd("📥", "Requests") + ("\n".join(f"• {h(n or '—')} | 🆔 {i}" for i, n, _u in rows) if rows else "No pending.")
    btns = [[(f"✅ {(n or str(i))[:14]}", f"adm:ap:{i}", "success"), (f"❌ {(n or str(i))[:14]}", f"adm:rj:{i}", "danger")]
            for i, n, _u in rows[:30]]
    return txt, ikb(*btns, [("🔙 Back", "adm:home")])


def v_users():
    rows = db.execute("SELECT user_id, name, status FROM users WHERE status!='pending' ORDER BY status").fetchall()
    icons = {"approved": "✅", "rejected": "❌", "revoked": "🚫", "banned": "⛔"}
    txt = hd("👥", "Users") + ("\n".join(f"{icons.get(s, '•')} {h(n or '—')} | 🆔 {i}" for i, n, s in rows) if rows else "No users.")
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
    body = "\n\n".join(parts) or "No accounts."
    if len(body) > 3500:
        body = body[:3500] + "\n…"
    return hd("📱", f"All accounts · {len(rows)}") + f"<blockquote expandable>{body}</blockquote>", ikb([("🔙 Back", "adm:home")])


def v_activity():
    rows = db.execute("""
        SELECT u.user_id, COALESCE(u.name, CAST(u.user_id AS TEXT)),
               COUNT(a.id), MAX(a.created)
        FROM users u LEFT JOIN activity a ON a.user_id=u.user_id
        GROUP BY u.user_id ORDER BY COALESCE(MAX(a.created),0) DESC LIMIT 40
    """).fetchall()
    txt = hd("📈", "User activity")
    btns = []
    for uid, name, total, _last in rows:
        btns.append([(f"{(name or str(uid))[:18]} · {total} events", f"adm:actuser:{uid}")])
    if not btns:
        txt += "No activity yet."
    return txt, ikb(*btns, [("🔙 Back", "adm:home")])


def v_activity_user(uid):
    row = db.execute("SELECT name, username, status FROM users WHERE user_id=?", (uid,)).fetchone()
    name, username, status = row if row else (str(uid), "", "unknown")
    added = count("SELECT COUNT(*) FROM activity WHERE user_id=? AND action='account_added'", uid)
    joins = count("SELECT COUNT(*) FROM activity WHERE user_id=? AND action='voice_join'", uid)
    leaves = count("SELECT COUNT(*) FROM activity WHERE user_id=? AND action='voice_leave'", uid)
    total_accounts = count("SELECT COUNT(*) FROM clients WHERE owner=?", uid)
    events = db.execute("SELECT action,details,created FROM activity WHERE user_id=? ORDER BY created DESC LIMIT 15", (uid,)).fetchall()
    lines = [f"• <b>{h(a)}</b> — {h(d)}\n  <i>{time.strftime('%Y-%m-%d %H:%M', time.localtime(ts))}</i>" for a, d, ts in events]
    body = "\n".join(lines) if lines else "No activity."
    txt = (hd("👤", "Activity details") +
           tree(f"👤 {h(name)}", f"🔗 @{h(username) if username else '—'}", f"🆔 {uid}",
                f"Status: {h(status)}", f"Accounts: <b>{total_accounts}</b>",
                f"Added: <b>{added}</b>", f"VC joins: <b>{joins}</b>", f"VC leaves: <b>{leaves}</b>",
                f"Total events: <b>{count('SELECT COUNT(*) FROM activity WHERE user_id=?', uid)}</b>") +
           "\n\n<b>Recent</b>\n" + body)
    if len(txt) > 3900: txt = txt[:3900] + "\n…"
    return txt, ikb([("🔙 All activity", "adm:activity")], [("🏠 Admin home", "adm:home")])


def v_keys(new=None):
    rows = db.execute("SELECT key, status, used_by FROM access_keys ORDER BY created DESC LIMIT 30").fetchall()
    lines = []
    for k, st, ub in rows:
        if st == "active": lines.append(f"🟢 <code>{k}</code> — unused")
        elif st == "used": lines.append(f"💎 <code>{k}</code> — {h(owner_name(ub))} ({ub})")
        else: lines.append(f"⛔ <code>{k}</code> — revoked")
    head = f"🎉 <b>New key</b>\n<code>{new}</code>\n\n" if new else ""
    txt = head + hd("🔑", "VIP keys") + ("\n".join(lines) or "No keys.")
    btns = [[("➕ Generate New Key", "adm:kg", "success")]]
    for k, st, _ub in rows[:20]:
        if st != "revoked":
            btns.append([(f"🧹 Revoke {k}", f"adm:kr:{k}", "danger")])
    btns.append([("🔙 Back", "adm:home")])
    return txt, ikb(*btns)


async def show(c: CallbackQuery, view):
    text, markup = view
    try: await c.message.edit_text(text, reply_markup=markup)
    except Exception: await c.message.answer(text, reply_markup=markup)


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
        try: await c.answer(f"⚠️ {type(e).__name__}: {e}"[:190], show_alert=True)
        except Exception: pass


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
        emit(f"user_{status}", uid, detail="by owner via panel")
        msg = {"ap": "✅ Approved! Send /start to begin.",
               "rj": "❌ Rejected.",
               "rv": "🚫 Access revoked.",
               "bn": "⛔ Permanently banned.",
               "ub": "✅ Ban lifted. Send /start."}[act]
        try: await c.bot.send_message(uid, msg)
        except Exception: pass
        head = c.message.text or ""
        if head.startswith("🔔"):
            await c.message.edit_text(f"{h(head)}\n\n{ {'ap': '✅ Accepted', 'rj': '❌ Rejected', 'rv': '🚫 Revoked'}[act] }")
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
            try: await c.bot.send_message(r[0], "🚫 VIP revoked.")
            except Exception: pass
        db.commit()
        emit("key_revoked", r[0] if r else None, detail=f"key {key} revoked")
        await show(c, v_keys())
        return await c.answer("Revoked")
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


# =====================================================================
#  SUPER ADMIN BOT  (running as: python main.py super)
# =====================================================================
def s_one(sql, *a):
    r = db.execute(sql, a).fetchone()
    return r[0] if r else 0


def s_stats():
    now = int(time.time())
    started = s_one("SELECT started FROM bot_state WHERE proc='main'") or 0
    hb = s_one("SELECT heartbeat FROM bot_state WHERE proc='main'") or 0
    up = now - started if started else 0
    d, rem = divmod(max(0, up), 86400)
    hrs, rem = divmod(rem, 3600)
    mn, _ = divmod(rem, 60)
    return dict(
        users=s_one("SELECT COUNT(*) FROM users WHERE status='approved'"),
        vip=s_one("SELECT COUNT(*) FROM vip_users"),
        pend=s_one("SELECT COUNT(*) FROM users WHERE status='pending'"),
        clients=s_one("SELECT COUNT(*) FROM clients"),
        events=s_one("SELECT COUNT(*) FROM events"),
        vjoin=s_one("SELECT COUNT(*) FROM events WHERE kind='voice_join'"),
        vleave=s_one("SELECT COUNT(*) FROM events WHERE kind='voice_leave'"),
        errs=s_one("SELECT COUNT(*) FROM events WHERE kind='error'"),
        newacc=s_one("SELECT COUNT(*) FROM events WHERE kind='account_added'"),
        cmds=s_one("SELECT COUNT(*) FROM events WHERE kind='cmd'"),
        watch=s_one("SELECT COUNT(*) FROM events WHERE kind='watcher'"),
        alive=bool(hb) and (now - hb) < 60,
        uptime=f"{d}d {hrs}h {mn}m" if started else "not running",
        last_event=s_one("SELECT MAX(ts) FROM events") or 0,
    )


def s_dash_text():
    s = s_stats()
    alive = "🟢 ALIVE" if s["alive"] else "🔴 DOWN ⚠️"
    last = time.strftime("%H:%M:%S", time.localtime(s["last_event"])) if s["last_event"] else "—"
    return (
        "👑 <b>SUPER ADMIN MONITOR</b>\n▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n\n"
        f"📡 Deploy: <code>{h(DEPLOY_ID)}</code>\n"
        f"🟢 Main bot: <b>{alive}</b>\n"
        f"🕒 Uptime: <b>{s['uptime']}</b>\n"
        f"📨 Last event: <b>{last}</b>\n\n"
        f"👥 Approved: <b>{s['users']}</b>\n"
        f"💎 VIP: <b>{s['vip']}</b>  ·  ⏳ Pending: <b>{s['pend']}</b>\n"
        f"📱 Accounts: <b>{s['clients']}</b>\n\n"
        f"🎧 VC joins: <b>{s['vjoin']}</b>  ·  🔇 leaves: <b>{s['vleave']}</b>\n"
        f"➕ Accounts added: <b>{s['newacc']}</b>\n"
        f"🖱 Commands: <b>{s['cmds']}</b>\n"
        f"👀 Watcher: <b>{s['watch']}</b>\n"
        f"❌ Errors: <b>{s['errs']}</b>\n"
        f"📜 Total events: <b>{s['events']}</b>\n\n"
        f"<i>auto-refresh every {int(REFRESH)}s · {time.strftime('%H:%M:%S')}</i>"
    )


def s_dash_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Refresh", callback_data="r"),
         InlineKeyboardButton(text="📜 Live feed", callback_data="feed")],
        [InlineKeyboardButton(text="❌ Errors", callback_data="errs"),
         InlineKeyboardButton(text="👥 Users", callback_data="users")],
        [InlineKeyboardButton(text="📱 Accounts", callback_data="accs"),
         InlineKeyboardButton(text="📡 Deployments", callback_data="deps")],
        [InlineKeyboardButton(text="⛔ STOP this bot", callback_data="stopbot")],
    ])


def s_recent(n=15, kind=None):
    q = "SELECT ts,user_id,kind,detail,ok FROM events"
    a = ()
    if kind:
        q += " WHERE kind=?"; a = (kind,)
    q += " ORDER BY id DESC LIMIT ?"; a += (n,)
    return db.execute(q, a).fetchall()


def s_feed_text():
    rows = s_recent(20)
    body = "\n".join(
        f"{'✅' if ok else '❌'} <code>{time.strftime('%H:%M:%S', time.localtime(ts))}</code> "
        f"<b>{h(kind)}</b> · {uid or '—'} · {h(detail)[:90]}"
        for ts, uid, kind, detail, ok in rows)
    return "📜 <b>Live feed (latest 20)</b>\n\n" + (body or "no events yet")


def s_errors_text():
    rows = s_recent(10, "error")
    body = "\n".join(f"• <code>{time.strftime('%H:%M', time.localtime(ts))}</code> {h(detail)[:180]}"
                     for ts, uid, kind, detail, ok in rows)
    return "❌ <b>Recent errors</b>\n\n" + (body or "no errors 🎉")


def s_users_text():
    rows = db.execute("SELECT user_id, name, username, status FROM users ORDER BY "
                      "CASE status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END, user_id").fetchall()
    icons = {"approved": "✅", "pending": "⏳", "rejected": "❌", "revoked": "🚫", "banned": "⛔"}
    body = "\n".join(f"{icons.get(st, '•')} {h(n or '—')} | @{h(u) if u else '—'} | <code>{i}</code>"
                     for i, n, u, st in rows[:40])
    return f"👥 <b>Users ({len(rows)})</b>\n\n" + (body or "no users")


def s_accs_text():
    rows = db.execute("SELECT id, name, owner FROM clients ORDER BY owner, name").fetchall()
    groups = {}
    for cid, n, o in rows:
        groups.setdefault(o, []).append((cid, n))
    parts = []
    for o, items in groups.items():
        parts.append(f"👥 owner <code>{o}</code> ({len(items)})\n" +
                     "\n".join(f"   • {h(n)} · <code>{c}</code>" for c, n in items))
    body = "\n\n".join(parts) or "no accounts yet"
    if len(body) > 3800:
        body = body[:3800] + "\n…"
    return f"📱 <b>All accounts ({len(rows)})</b>\n\n{body}"


def s_deps_text():
    rows = db.execute("SELECT proc, heartbeat FROM bot_state WHERE proc LIKE 'deploy:%'").fetchall()
    now = int(time.time())
    lines = []
    for proc, hb in rows:
        dep = proc.split(":", 1)[1]
        on = (now - (hb or 0)) < 90
        lines.append(f"{'🟢' if on else '🔴'} <b>{h(dep)}</b> · last seen {now - (hb or now)}s ago")
    return "📡 <b>Deployments</b>\n\n" + ("\n".join(lines) or "no remote deployments seen")


async def super_main():
    if not SUPER_TOKEN or not SUPER_ID:
        raise SystemExit("❌ SUPER_ADMIN_TOKEN and SUPER_ADMIN_ID must be set in env to run: python main.py super.")
    sbot = Bot(SUPER_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    sdp = Dispatcher()
    db.execute("INSERT OR REPLACE INTO bot_state(proc,started,heartbeat,version) VALUES('super',?,?,'1.0')",
               (int(time.time()), int(time.time())))
    db.commit()
    dash = {}
    last_seen = [0]
    PUSH_KINDS = {"account_added", "error", "voice_join", "voice_leave", "boot", "bulk",
                  "access_request", "ban", "deleteuser", "vip_used", "vip_key"}

    async def refresh_loop():
        while True:
            try:
                key = (s_stats()["events"], s_stats()["alive"], s_stats()["clients"])
                for cid, (mid, last) in list(dash.items()):
                    if last == key: continue
                    try:
                        await sbot.edit_message_text(chat_id=cid, message_id=mid, text=s_dash_text(), reply_markup=s_dash_kb())
                        dash[cid] = (mid, key)
                    except Exception: pass
            except Exception: pass
            await asyncio.sleep(REFRESH)

    async def push_loop():
        cutoff = int(time.time()) - PUSH_WINDOW
        last_seen[0] = s_one("SELECT COALESCE(MAX(id),0) FROM events WHERE ts>=?", cutoff)
        while True:
            try:
                q = ("SELECT id,ts,user_id,kind,chat,detail,ok FROM events WHERE id>? AND kind IN ("
                     + ",".join("?" * len(PUSH_KINDS)) + ") ORDER BY id LIMIT 50")
                rows = db.execute(q, (last_seen[0], *PUSH_KINDS)).fetchall()
                for eid, ts, uid, kind, chat, detail, ok in rows:
                    last_seen[0] = eid
                    icon = {"account_added": "➕", "error": "❌", "voice_join": "🎧", "voice_leave": "🔇",
                            "boot": "🟢", "bulk": "📦", "access_request": "🙋", "ban": "⛔",
                            "deleteuser": "🗑", "vip_used": "💎", "vip_key": "🔑"}.get(kind, "•")
                    text = (f"{icon} <b>{h(kind)}</b>\n👤 <code>{uid or '—'}</code>\n"
                            f"📝 {h(detail)[:300]}\n🕒 {time.strftime('%H:%M:%S', time.localtime(ts))}")
                    if chat: text += f"\n💬 <code>{h(chat)}</code>"
                    try: await sbot.send_message(SUPER_ID, text)
                    except Exception: pass
            except Exception: pass
            await asyncio.sleep(2)

    @sdp.message(CommandStart())
    async def s_start(m: Message):
        if m.from_user.id != SUPER_ID:
            return await m.answer("⛔ This monitor is private.")
        sent = await m.answer(s_dash_text(), reply_markup=s_dash_kb())
        dash[m.chat.id] = (sent.message_id, (s_stats()["events"], s_stats()["alive"], s_stats()["clients"]))
        await m.answer(f"🔴 Live monitoring on. Auto-edits every {int(REFRESH)}s.\n\n"
                       "Commands:\n/stats /tail /errors /users /accounts /deps\n\n"
                       "Remote control (if SUPER_CHAT_ID is set on the target):\n"
                       "<code>/remote CUST-001 stop</code>\n"
                       "<code>/remote CUST-001 clear</code>\n"
                       "<code>/remote CUST-001 status</code>\n"
                       "<code>/remote CUST-001 notify hello</code>\n"
                       "<code>/remote CUST-001 restart</code>")

    @sdp.message(Command("stats"))
    async def s_stats_cmd(m: Message):
        if m.from_user.id != SUPER_ID: return
        sent = await m.answer(s_dash_text(), reply_markup=s_dash_kb())
        dash[m.chat.id] = (sent.message_id, (s_stats()["events"], s_stats()["alive"], s_stats()["clients"]))

    @sdp.message(Command("tail"))
    async def s_tail(m: Message):
        if m.from_user.id == SUPER_ID:
            await m.answer(s_feed_text())

    @sdp.message(Command("errors"))
    async def s_errors_cmd(m: Message):
        if m.from_user.id == SUPER_ID:
            await m.answer(s_errors_text())

    @sdp.message(Command("users"))
    async def s_users_cmd(m: Message):
        if m.from_user.id == SUPER_ID:
            await m.answer(s_users_text())

    @sdp.message(Command("accounts"))
    async def s_accs_cmd(m: Message):
        if m.from_user.id == SUPER_ID:
            await m.answer(s_accs_text())

    @sdp.message(Command("deps"))
    async def s_deps_cmd(m: Message):
        if m.from_user.id == SUPER_ID:
            await m.answer(s_deps_text())

    @sdp.message(Command("remote"))
    async def s_remote(m: Message):
        if m.from_user.id != SUPER_ID: return
        parts = (m.text or "").split()
        if len(parts) < 3:
            return await m.answer("Usage: /remote <DEPLOY_ID> <stop|clear|status|restart|notify> [text]")
        dep, cmd = parts[1], parts[2].lower()
        if dep != DEPLOY_ID:
            return await m.answer(f"ℹ️ This monitor is bound to <code>{DEPLOY_ID}</code>, not <code>{dep}</code>.\n"
                                   "Run a separate super instance for that deployment (or use SUPER_CHAT_ID).")
        if cmd == "stop":
            BLOCKED[0] = True
            emit("shutdown", None, detail=f"remote stop requested for {DEPLOY_ID}")
            await m.answer(f"⛔ Stop queued for {DEPLOY_ID}. It will exit within ~5s.")
        elif cmd == "clear":
            BLOCKED[0] = False
            await m.answer(f"✅ {DEPLOY_ID} unblocked.")
        elif cmd == "status":
            await m.answer(f"📊 {DEPLOY_ID}\nclients={len(RUNNING)}\nuptime={int(time.time()-BOOT_TIME)}s\nblocked={BLOCKED[0]}")
        elif cmd == "restart":
            for tg_id, session_enc, owner in db.execute("SELECT id, session, owner FROM clients").fetchall():
                try:
                    await start_client(fernet.decrypt(session_enc).decode(), tg_id, owner or OWNER_ID)
                except Exception:
                    pass
            emit("restart", None, detail="remote restart")
            await m.answer(f"🔄 Restart done for {DEPLOY_ID}.")
        elif cmd == "notify":
            txt = m.text.split(cmd, 1)[1].strip() or "no text"
            try:
                await BOT_INSTANCE.send_message(OWNER_ID, f"📨 {DEPLOY_ID} notice:\n{txt}")
                await m.answer("✅ notice delivered.")
            except Exception as e:
                await m.answer(f"❌ {e}")
        else:
            await m.answer("Unknown command.")

    @sdp.callback_query(F.data.in_({"r", "feed", "errs", "users", "accs", "deps", "stopbot"}))
    async def s_cb(c: CallbackQuery):
        if c.from_user.id != SUPER_ID:
            return await c.answer("⛔", show_alert=True)
        if c.data == "r":
            try: await c.message.edit_text(s_dash_text(), reply_markup=s_dash_kb())
            except Exception: pass
        elif c.data == "feed": await c.message.answer(s_feed_text())
        elif c.data == "errs": await c.message.answer(s_errors_text())
        elif c.data == "users": await c.message.answer(s_users_text())
        elif c.data == "accs": await c.message.answer(s_accs_text())
        elif c.data == "deps": await c.message.answer(s_deps_text())
        elif c.data == "stopbot":
            BLOCKED[0] = True
            emit("shutdown", None, detail=f"stopbot via super panel for {DEPLOY_ID}")
            await c.message.answer(f"⛔ Stop queued for {DEPLOY_ID}. It will exit within ~5s.")
        await c.answer("ok")

    asyncio.create_task(refresh_loop())
    asyncio.create_task(push_loop())
    await sdp.start_polling(sbot)


# ---------- main bot entrypoint ----------
async def _watchdog():
    while True:
        if BLOCKED[0]:
            log.warning("BLOCKED by super admin — shutting down main bot")
            for tid in list(RUNNING.keys()):
                try: await stop_client(tid)
                except Exception: pass
            emit("shutdown", None, detail=f"blocked for {DEPLOY_ID}")
            os._exit(0)
        await asyncio.sleep(5)


async def heartbeat_to_group(sbot):
    if not SUPER_CHAT:
        return
    await asyncio.sleep(5)
    while True:
        try:
            db.execute("INSERT OR REPLACE INTO bot_state(proc,started,heartbeat,version) VALUES(?,?,?,?)",
                       (f"deploy:{DEPLOY_ID}", int(BOOT_TIME), int(time.time()), "1.0"))
            db.commit()
            txt = (f"💓 <b>{h(DEPLOY_ID)}</b> | online\n"
                   f"📱 clients: <b>{len(RUNNING)}</b>\n"
                   f"🕒 uptime: <b>{int(time.time() - BOOT_TIME)}s</b>")
            try: await sbot.send_message(SUPER_CHAT, txt)
            except Exception: pass
        except Exception:
            log.exception("heartbeat_to_group failed")
        await asyncio.sleep(HB_SECONDS)


async def main():
    global BOT_INSTANCE
    load_auto()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    BOT_INSTANCE = bot
    bot.session.middleware(PremiumMW())
    bot.session.middleware(BoldMW())
    emit("boot", None, detail=f"main bot started for {DEPLOY_ID}")

    for tg_id, session, owner in db.execute("SELECT id, session, owner FROM clients").fetchall():
        try:
            await start_client(fernet.decrypt(session).decode(), tg_id, owner or OWNER_ID)
            log.info("client %s started", tg_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            emit("error", owner or OWNER_ID, ok=0, detail=f"startup client {tg_id}: {exc}")

    try:
        await bot.send_message(OWNER_ID, f"🟢 <b>Bot চালু হয়েছে</b> ({h(DEPLOY_ID)})")
    except Exception:
        pass

    asyncio.create_task(_run_guarded_background("auto-view-react-watcher", watcher, bot))
    asyncio.create_task(_run_guarded_background("self-healing-supervisor", lambda: self_healing_supervisor(bot), bot))
    asyncio.create_task(_run_guarded_background("runtime-health-monitor", lambda: _runtime_health_monitor(bot), bot))
    asyncio.create_task(_run_guarded_background("heartbeat", heartbeat_loop, bot))
    asyncio.create_task(_watchdog())

    if SUPER_TOKEN and SUPER_CHAT:
        try:
            sbot = Bot(SUPER_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
            asyncio.create_task(heartbeat_to_group(sbot))
        except Exception as exc:
            log.warning("group heartbeat setup failed: %r", exc)

    log.info("Main bot startup complete.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].lower() == "super":
        asyncio.run(super_main())
    else:
        asyncio.run(main())
