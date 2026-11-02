"""
Telegram Multi-Client Voice Chat Bot
=====================================
REQUIREMENTS (Wispbyte-এ requirements.txt ফাইলে এগুলো বসান):
    aiogram>=3.25
    kurigram
    tgcrypto
    py-tgcalls
    cryptography
    python-dotenv

RUN: python main.py   (Startup file = main.py)
"""
import asyncio, html, logging, os, re, secrets, sqlite3, time
from cryptography.fernet import Fernet
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
                           InlineKeyboardMarkup, InlineKeyboardButton)
from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler
from pyrogram.errors import (FloodWait, SessionPasswordNeeded, PhoneCodeInvalid,
                             PhoneCodeExpired, PasswordHashInvalid, UserAlreadyParticipant)
from pyrogram.raw.functions.messages import GetMessagesViews
from pytgcalls import PyTgCalls

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot")

# ====== আপনার কনফিগ (এখানেই বসানো আছে) ======
BOT_TOKEN = "9103406052:AAE0c-mYIWKZ7bR-kFVQWMqtTYq2JLUXIjs"
API_ID = 35271075
API_HASH = "e8c3704b92160afdc08a81c1156197cc"
OWNER_ID = 8455803496
DELAY = float(os.environ.get("DELAY", "1"))  # seconds between accounts (FloodWait protection)

# ====== Telegram Premium emoji (optional) ======
# Format:  "normal emoji": "custom_emoji_id"
# Every place the bot uses that emoji (messages + button icons) becomes a premium emoji.
# To get an ID: as admin, send any premium emoji to the bot -> it replies with the ID.
# Works only if the BOT OWNER account has Telegram Premium. Leave empty to use normal emoji.
PREMIUM = {
    # "📋": "5368324170671202286",
    # "🎧": "5310169226856644648",
}


def h(x):
    return html.escape(str(x))


def norm(e):
    return str(e).replace("\ufe0f", "")


# ---------- encryption + db ----------
KEY_FILE = "secret.key"
if os.environ.get("ENC_KEY"):
    KEY = os.environ["ENC_KEY"].encode()
elif os.path.exists(KEY_FILE):
    KEY = open(KEY_FILE, "rb").read()
else:
    KEY = Fernet.generate_key()
    open(KEY_FILE, "wb").write(KEY)
fernet = Fernet(KEY)

db = sqlite3.connect("data.db")
db.executescript("""
CREATE TABLE IF NOT EXISTS clients(id INTEGER PRIMARY KEY, name TEXT, phone TEXT, session BLOB, owner INTEGER);
CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY, name TEXT, username TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS auto2(owner INTEGER, kind TEXT, chat_id TEXT, emoji TEXT, PRIMARY KEY(owner, kind, chat_id));
CREATE TABLE IF NOT EXISTS access_keys(key TEXT PRIMARY KEY, status TEXT, created INTEGER, used_by INTEGER, used_at INTEGER);
CREATE TABLE IF NOT EXISTS vip_users(user_id INTEGER PRIMARY KEY, key TEXT);
CREATE TABLE IF NOT EXISTS premium(emoji TEXT PRIMARY KEY, cid TEXT);
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

# ---------- runtime state ----------
RUNNING = {}     # tg_id -> (Client, PyTgCalls, owner_user_id)
PENDING = {}     # bot user id -> dict(app, phone, hash)
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


async def for_all(uid, fn):
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
            log.warning("client failed: %r", e)
        await asyncio.sleep(DELAY)
    return ok, fail, max(1, int(time.time() - t0))


def report(ok, fail, secs, label="Done"):
    total = max(1, ok + fail)
    filled = round(10 * ok / total)
    bar = "▰" * filled + "▱" * (10 - filled)
    return (hd("✅", f"{label} complete") +
            tree(f"✅ Success — <b>{ok}</b>", f"❌ Failed — <b>{fail}</b>", f"⏳ Time — <b>{secs}s</b>") +
            f"\n\n<code>{bar}</code> {round(100 * ok / total)}%")


def make_handler(owner):
    async def on_post(client, m):
        chat = m.chat.id
        emoji = AUTO_REACT.get((owner, chat)) or AUTO_REACT.get((OWNER_ID, chat))
        if emoji:
            try:
                await client.send_reaction(chat, m.id, emoji)
            except Exception as e:
                log.debug("react: %r", e)
        if (owner, chat) in AUTO_VIEW or (OWNER_ID, chat) in AUTO_VIEW:
            try:
                await client.invoke(GetMessagesViews(peer=await client.resolve_peer(chat), id=[m.id], increment=True))
            except Exception as e:
                log.debug("view: %r", e)
    return on_post


async def start_client(session_string, tg_id, owner):
    await stop_client(tg_id)
    app = Client(f"c{tg_id}", api_id=API_ID, api_hash=API_HASH,
                 session_string=session_string, in_memory=True)
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


async def stop_client(tg_id):
    pair = RUNNING.pop(tg_id, None)
    if pair:
        try: await pair[0].stop()
        except Exception: pass


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
                    log.warning("watch %s: %r", chat, e)
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
                                    log.warning("view %s: %r", tg_id, e)
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
            log.warning("watcher: %r", e)
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
SLOTS = [("👑", "Admin"), ("💎", "VIP"), ("📱", "Accounts"), ("➕", "Add"), ("🎧", "Voice join"),
         ("🔇", "Voice leave"), ("🚀", "Join channel"), ("💫", "React"), ("⚡", "Auto react"),
         ("👀", "Auto view"), ("🔙", "Back"), ("✅", "Success"), ("❌", "Failed"), ("⏳", "Pending / time"),
         ("🟢", "Online"), ("🔴", "Offline"), ("🔑", "Keys"), ("👥", "Users"), ("📥", "Requests"),
         ("🚫", "Revoked"), ("🔐", "Lock"), ("🧹", "Remove"), ("🎉", "Celebrate")]

B_CLIENTS, B_ADD = "📱 Accounts", "➕ Add Account"
B_JVC, B_LVC = "🎧 Voice Join", "🔇 Voice Leave"
B_JOIN, B_REACT = "🚀 Join Channel", "💫 React"
B_AR, B_AV = "⚡ Auto React", "👀 Auto View"
B_ACC, B_ADMIN, B_BACK = "💎 Access", "👑 Admin", "🔙 Back"


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
        [rbtn(B_ACC, "primary")] + ([rbtn(B_ADMIN, "danger")] if admin else []),
    ]
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=rows)


def back_kb():
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[[rbtn(B_BACK, "danger")]])


class PremiumMW(BaseRequestMiddleware):
    """Turns plain emoji in outgoing messages into premium (custom) emoji."""
    async def __call__(self, make_request, bot, method):
        if PREMIUM and type(method).__name__ in ("SendMessage", "EditMessageText") and isinstance(method.text, str):
            pat = re.compile("(" + "|".join(re.escape(k) for k in sorted(PREMIUM, key=len, reverse=True)) + ")\ufe0f?")
            method.text = pat.sub(lambda mo: f'<tg-emoji emoji-id="{PREMIUM[mo.group(1)]}">{mo.group(1)}</tg-emoji>', method.text)
        return await make_request(bot, method)


class S(StatesGroup):
    phone = State(); otp = State(); pwd = State()
    join = State(); jvc = State(); lvc = State()
    react_link = State(); react_emoji = State()
    ar_chat = State(); ar_emoji = State(); av_chat = State()
    vip_key = State(); emoji_pack = State()


# ---------- access: request -> admin accepts ----------
ADMIN_ONLY_TEXT = {B_ADMIN, B_ADMIN.split(" ", 1)[1]}
VIP_BTN = ikb([("🔑 Enter VIP Access Key", "vip_enter", "primary")])


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
        # key entry is allowed for everyone (so a key holder can get in)
        st_now = await data["state"].get_state() if data.get("state") else None
        if st_now == S.vip_key.state or (not is_msg and (event.data or "") == "vip_enter"):
            return await handler(event, data)
        row = db.execute("SELECT status FROM users WHERE user_id=?", (u.id,)).fetchone()
        st = row[0] if row else None
        if st == "approved":
            if is_msg and event.text in ADMIN_ONLY_TEXT:
                return await say("⛔ Admin only.")
            if not is_msg and (event.data or "").startswith("adm"):
                return await say("⛔ Admin only.")
            return await handler(event, data)
        if st == "rejected":
            return await say("❌ Your access request was rejected by the bot owner.\n\nHave a VIP key? Enter it below.", VIP_BTN)
        if st == "revoked":
            return await say("🚫 Your access has been revoked by the bot owner.\n\nHave a VIP key? Enter it below.", VIP_BTN)
        if st == "pending":
            return await say("⏳ Your request is still waiting for the owner's approval.\n\nHave a VIP key? Enter it below.", VIP_BTN)
        # new user -> send request to admin
        db.execute("INSERT OR REPLACE INTO users(user_id,name,username,status) VALUES(?,?,?,?)",
                   (u.id, u.full_name, u.username or "", "pending"))
        db.commit()
        await say("🔐 Bot authorization request sent!\n\nWaiting for the bot owner to approve you. You'll be notified here.\n\nHave a VIP key? Enter it below.", VIP_BTN)
        try:
            uname = f"@{h(u.username)}" if u.username else "—"
            await data["bot"].send_message(
                OWNER_ID,
                f"🔔 New access request\n\n👤 Name: {h(u.full_name)}\n🔗 Username: {uname}\n🆔 ID: {u.id}",
                reply_markup=ikb([("✅ Accept", f"adm:ap:{u.id}", "success"), ("❌ Reject", f"adm:rj:{u.id}", "danger")]))
        except Exception as e:
            log.warning("owner notify failed: %r", e)


dp = Dispatcher(storage=MemoryStorage())
dp.message.outer_middleware(Guard())
dp.callback_query.outer_middleware(Guard())


# ---------- basic ----------
@dp.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    uid = m.from_user.id
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
        try: await p["app"].disconnect()
        except Exception: pass
    await state.clear()
    await m.answer("❎ Cancelled.", reply_markup=kb(m.from_user.id))


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
    await m.answer("\n".join(lines) + '\n\nPaste into PREMIUM in main.py like:\n<code>"📋": "ID"</code>')


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
        hd("➕", "Add account", "phone number + OTP login") + "Tap the button to start.",
        reply_markup=ikb([("📱 Add by Phone Number", "add_phone", "primary")]))


@dp.callback_query(F.data == "add_phone")
async def add_phone(c: CallbackQuery, state: FSMContext):
    await state.set_state(S.phone)
    await c.message.answer(
        "📱 Add by Phone Number\n\nSend the Telegram account phone number with country code, e.g. +1234567890.\n\n"
        "Tap 🔙 Back to cancel, or send /cancel to abort.", reply_markup=back_kb())
    await c.answer()


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
    phone = m.text.strip().replace(" ", "")
    app = Client(f"login{m.from_user.id}", api_id=API_ID, api_hash=API_HASH, in_memory=True)
    try:
        await app.connect()
        sent = await app.send_code(phone)
    except Exception as e:
        try: await app.disconnect()
        except Exception: pass
        return await m.answer(f"❌ Could not send code: {h(e)}\nTry again or tap 🔙 Back.")
    PENDING[m.from_user.id] = dict(app=app, phone=phone, hash=sent.phone_code_hash)
    await state.set_state(S.otp)
    await m.answer(
        f"🔑 OTP sent to {h(phone)}\n\nSend the login code. You can paste the full message — I'll extract the digits automatically.\n\n"
        "Tap 🔙 Back to cancel, or send /cancel to abort.")


async def finish_login(m: Message, state: FSMContext, p):
    app = p["app"]
    session = await app.export_session_string()
    me = await app.get_me()
    await app.disconnect()
    name = " ".join(x for x in [me.first_name, me.last_name] if x) or "NoName"
    db.execute("INSERT OR REPLACE INTO clients(id,name,phone,session,owner) VALUES(?,?,?,?,?)",
               (me.id, name, p["phone"], fernet.encrypt(session.encode()), m.from_user.id))
    db.commit()
    await start_client(session, me.id, m.from_user.id)
    PENDING.pop(m.from_user.id, None)
    await state.clear()
    await m.answer(f"✅ Account added via Phone Number!\n\n👤 Name: {h(name)}\n📞 Phone: {h(p['phone'])}\n🆔 ID: {me.id}",
                   reply_markup=kb(m.from_user.id))


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
                    [("👥 Users", "adm:users"), ("📱 Accounts", "adm:acc")])


def v_req():
    rows = db.execute("SELECT user_id, name, username FROM users WHERE status='pending'").fetchall()
    txt = hd("📥", "Requests", "waiting for approval") + ("\n".join(f"• {h(n or '—')} | 🆔 {i}" for i, n, _u in rows) if rows else "No pending requests.")
    btns = [[(f"✅ {(n or str(i))[:14]}", f"adm:ap:{i}", "success"), (f"❌ {(n or str(i))[:14]}", f"adm:rj:{i}", "danger")]
            for i, n, _u in rows[:30]]
    return txt, ikb(*btns, [("🔙 Back", "adm:home")])


def v_users():
    rows = db.execute("SELECT user_id, name, status FROM users WHERE status!='pending' ORDER BY status").fetchall()
    icons = {"approved": "✅", "rejected": "❌", "revoked": "🚫"}
    txt = hd("👥", "Users") + ("\n".join(f"{icons.get(s, '•')} {h(n or '—')} | 🆔 {i}" for i, n, s in rows) if rows else "No users yet.")
    btns = []
    for i, n, s in rows[:30]:
        nm = (n or str(i))[:14]
        btns.append([(f"🚫 Revoke {nm}", f"adm:rv:{i}", "danger")] if s == "approved"
                    else [(f"✅ Approve {nm}", f"adm:ap:{i}", "success")])
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
    if act in ("ap", "rj", "rv"):
        uid = int(parts[2])
        status = {"ap": "approved", "rj": "rejected", "rv": "revoked"}[act]
        db.execute("UPDATE users SET status=? WHERE user_id=?", (status, uid))
        if act != "ap":
            db.execute("DELETE FROM vip_users WHERE user_id=?", (uid,))
        db.commit()
        msg = {"ap": "✅ Your access request has been approved!\n\nSend /start to begin.",
               "rj": "❌ Your access request was rejected by the bot owner.",
               "rv": "🚫 Your access has been revoked by the bot owner."}[act]
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
    views = {"keys": v_keys, "home": v_panel, "req": v_req, "users": v_users, "acc": v_acc}
    if act in views:
        await show(c, views[act]())
    await c.answer()


# ---------- boot ----------
async def main():
    load_auto()
    for tg_id, session, owner in db.execute("SELECT id, session, owner FROM clients").fetchall():
        try:
            await start_client(fernet.decrypt(session).decode(), tg_id, owner or OWNER_ID)
            log.info("client %s started", tg_id)
        except Exception as e:
            log.warning("client %s failed to start: %r", tg_id, e)
    asyncio.create_task(watcher())
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    bot.session.middleware(PremiumMW())   # premium emoji can be added live with /setemoji
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
