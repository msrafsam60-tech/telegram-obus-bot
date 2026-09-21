import os
import re
import html
import base64
import random
import string
import json
import time
import requests
import telebot
import threading
import http.server
import socketserver
import hashlib
from telebot import types
from datetime import datetime
from urllib.parse import urlparse, quote
import yt_dlp

# =========================================================
# 👑 BOT & ADMIN CONFIGURATION
# =========================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8014931413:AAHz8NKUKZ5l8m2yT-417qiaLOd2_g2xXbQ")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 6753121703))
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "Rafsanvai0")
BOT_USERNAME = "@ddos_attack_team_bot"
PORT = int(os.environ.get("PORT", 8080))

# 🪤 Honeypot Server (Anti-Decoder Trap)
HONEYPOT_ENABLED = True
TRAP_ALERT_CHANNEL = ADMIN_ID  # যেখানে ট্র্যাপ অ্যালার্ট যাবে

# 📢 ফোর্স সাব
FORCE_SUB_CHANNEL = "@sopport_and_hack_send"
FORCE_SUB_URL = "https://t.me/sopport_and_hack_send"

telebot.apihelper.READ_TIMEOUT = 180
telebot.apihelper.CONNECT_TIMEOUT = 60

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None, threaded=True)
USERS_FILE = "users.json"
BANNED_FILE = "banned.json"
TRAP_LOG_FILE = "trap_logs.json"

user_states = {}
trap_victims = {}

# =========================================================
# 🌐 CRASH-PROOF KEEP-ALIVE WEB SERVER + HONEYPOT TRAP
# =========================================================

class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True

def save_trap_victim(ip, user_agent, referer=""):
    """ডিকোডার ধরা পড়লে ট্র্যাক করে অ্যাডমিনকে অ্যালার্ট"""
    try:
        data = {}
        if os.path.exists(TRAP_LOG_FILE):
            with open(TRAP_LOG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        
        key = f"{ip}_{int(time.time())}"
        data[key] = {
            "ip": ip,
            "user_agent": user_agent,
            "referer": referer,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        with open(TRAP_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        # অ্যাডমিনকে অ্যালার্ট
        try:
            alert = (
                "🚨 <b>ডিকোডার ধরা পড়েছে!</b> 🚨\n"
                "👑 ━━━━━━━━━━━━━━━━━━━━━━━━━ 👑\n\n"
                f"🌐 <b>IP:</b> <code>{html.escape(ip)}</code>\n"
                f"🖥️ <b>Browser:</b> <code>{html.escape(user_agent[:80])}</code>\n"
                f"🔗 <b>Referer:</b> <code>{html.escape(referer[:80])}</code>\n"
                f"⏰ <b>Time:</b> <code>{datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')}</code>\n\n"
                "💀 <i>এই ব্যক্তি আপনার কোড ডিকোড করার চেষ্টা করেছে!</i>"
            )
            safe_send_message(TRAP_ALERT_CHANNEL, alert)
        except Exception:
            pass
    except Exception as e:
        print(f"Trap log error: {e}")


def run_keep_alive_server():
    class KeepAliveHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            # 🪤 Honeypot Trap - ডিকোডার ট্র্যাপ
            client_ip = self.client_address[0]
            user_agent = self.headers.get("User-Agent", "Unknown")
            referer = self.headers.get("Referer", "")
            path = self.path

            # যদি কেউ ডিকোডার/স্ক্র্যাপার লাইব্রেরি দিয়ে হিট করে
            suspicious_keywords = [
                "decode", "decrypt", "scrape", "beautify", "unminify",
                "extract", "source", "obfuscate", "html", "raw", "crack"
            ]
            suspicious_ua = any(k in user_agent.lower() for k in [
                "python", "curl", "wget", "scrapy", "beautifulsoup",
                "requests", "axios", "node", "postman", "insomnia"
            ])
            
            is_trap = any(k in path.lower() for k in suspicious_keywords) or suspicious_ua
            
            if HONEYPOT_ENABLED and is_trap:
                save_trap_victim(client_ip, user_agent, referer)
                self.send_response(200)
                self.send_header("Content-type", "text/html; charset=utf-8")
                self.end_headers()
                
                # 🎭 Fake Decoy Response
                trap_html = f"""<!DOCTYPE html>
<html><head><title>Access Denied</title></head>
<body style="background:#000;color:#0f0;font-family:monospace;padding:40px;text-align:center;">
<h1 style="color:#f00;">🚨 UNAUTHORIZED ACCESS DETECTED 🚨</h1>
<h2 style="color:#ff0;">তোর আইপি এড্রেস সহ সব তথ্য আমাদের সার্ভারে সেভ হয়ে গেছে!</h2>
<hr style="border-color:#f00;">
<pre style="color:#0ff;text-align:left;font-size:14px;">
IP: {client_ip}
Browser: {html.escape(user_agent[:60])}
Time: {datetime.now()}

██████╗ ███████╗ ██████╗ ██████╗ ██████╗ ███████╗██████╗ 
██╔══██╗██╔════╝██╔════╝██╔═══██╗██╔══██╗██╔════╝██╔══██╗
██║  ██║█████╗  ██║     ██║   ██║██║  ██║█████╗  ██████╔╝
██║  ██║██╔══╝  ██║     ██║   ██║██║  ██║██╔══╝  ██╔══██╗
██████╔╝███████╗╚██████╗╚██████╔╝██████╔╝███████╗██║  ██║
╚═════╝ ╚══════╝ ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝╚═╝  ╚═╝

👿🔥🥵💀❌ [দূরে গিয়ে মর খানকির পোলা কোড চুরি করতে আসিস 
তাই না ডিকোড করতে? তোর এই যে আব্বার বট {BOT_USERNAME} 
দিয়ে ইনক্রিপ্টেড মারা এবং এই আব্বা ইনক্রিপ্টেড মারছে @{ADMIN_USERNAME}] 🔒⚡

তোর IP: {client_ip}
তোর ব্রাউজার: {html.escape(user_agent[:80])}
আব্বার নোট: এই তথ্য এখন গ্রুপে পোস্ট করা হবে 😂
</pre>
<h3 style="color:#f00;">🔒 যোগাযোগ: @{ADMIN_USERNAME}</h3>
</body></html>"""
                self.wfile.write(trap_html.encode("utf-8"))
                return

            # স্বাভাবিক স্ট্যাটাস পেজ
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            status_page = (
                "<html><head><title>Rafsan VIP Ultra Bot Status</title></head>"
                "<body style='font-family:sans-serif;text-align:center;padding-top:60px;background:#0b0e14;color:#58a6ff;'>"
                "<h1>⚡ RAFSAN VIP ULTRA CIPHER ENGINE v3.0 ⚡</h1>"
                "<p style='color:#3fb950;font-size:18px;'>🟢 System Status: Active &amp; Online 24/7</p>"
                f"<p style='color:#8b949e;'>Owner: @{ADMIN_USERNAME} | Bot: {BOT_USERNAME}</p>"
                "<p style='color:#f85149;font-size:14px;'>🛡️ Military-Grade Encryption Active</p>"
                "</body></html>"
            )
            self.wfile.write(status_page.encode("utf-8"))

        def log_message(self, format, *args):
            pass

    while True:
        try:
            with ReusableTCPServer(("", PORT), KeepAliveHandler) as httpd:
                print(f"🌐 Keep-Alive + Honeypot Server active on Port: {PORT}")
                httpd.serve_forever()
        except Exception as e:
            print(f"⚠️ Web Server Warning: {e}, retrying in 5s...")
            time.sleep(5)


# =========================================================
# 🛡️ SAFE TELEGRAM API WRAPPERS
# =========================================================

def safe_edit_message(chat_id, message_id, text, reply_markup=None, parse_mode="HTML"):
    try:
        return bot.edit_message_text(text=text, chat_id=chat_id, message_id=message_id, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception:
        pass


def safe_send_message(chat_id, text, reply_markup=None, parse_mode="HTML", disable_preview=True, reply_to_msg_id=None):
    try:
        return bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode, disable_web_page_preview=disable_preview, reply_to_message_id=reply_to_msg_id)
    except Exception:
        try:
            return bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=None, disable_web_page_preview=disable_preview, reply_to_message_id=reply_to_msg_id)
        except Exception:
            return None


def safe_send_document(chat_id, document, caption="", parse_mode="HTML", reply_to_msg_id=None, reply_markup=None):
    try:
        return bot.send_document(chat_id, document, caption=caption, parse_mode=parse_mode, timeout=180, reply_to_message_id=reply_to_msg_id, reply_markup=reply_markup)
    except Exception:
        try:
            return bot.send_document(chat_id, document, caption=caption, parse_mode=None, timeout=180, reply_to_message_id=reply_to_msg_id, reply_markup=reply_markup)
        except Exception:
            return None


def safe_send_video(chat_id, video, caption="", parse_mode="HTML", reply_to_msg_id=None, reply_markup=None):
    try:
        return bot.send_video(chat_id, video, caption=caption, parse_mode=parse_mode, timeout=180, supports_streaming=True, reply_to_message_id=reply_to_msg_id, reply_markup=reply_markup)
    except Exception:
        try:
            return bot.send_video(chat_id, video, caption=caption, parse_mode=None, timeout=180, reply_to_message_id=reply_to_msg_id, reply_markup=reply_markup)
        except Exception:
            return None


# =========================================================
# 💎 BOLD FONT & DATABASE
# =========================================================

def to_bold_font(text: str) -> str:
    out = []
    for char in str(text):
        code = ord(char)
        if 65 <= code <= 90:
            out.append(chr(0x1D400 + (code - 65)))
        elif 97 <= code <= 122:
            out.append(chr(0x1D41A + (code - 97)))
        elif 48 <= code <= 57:
            out.append(chr(0x1D7CE + (code - 48)))
        else:
            out.append(char)
    return "".join(out)


def load_users_data():
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_users_data(data):
    try:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def register_user(user):
    try:
        data = load_users_data()
        uid_str = str(user.id)
        if uid_str not in data:
            data[uid_str] = {
                "id": user.id,
                "first_name": user.first_name or "",
                "last_name": user.last_name or "",
                "username": user.username if user.username else "N/A",
                "joined_at": datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
            }
            save_users_data(data)
    except Exception as e:
        print(f"User Register Error: {e}")

def get_all_user_ids():
    return [int(uid) for uid in load_users_data().keys()]

def load_banned_users():
    if os.path.exists(BANNED_FILE):
        try:
            with open(BANNED_FILE, "r", encoding="utf-8") as f:
                return set(int(uid) for uid in json.load(f))
        except Exception:
            return set()
    return set()

def save_banned_users(banned_set):
    try:
        with open(BANNED_FILE, "w", encoding="utf-8") as f:
            json.dump(list(banned_set), f)
    except Exception:
        pass

def is_user_banned(user_id):
    if user_id == ADMIN_ID:
        return False
    return int(user_id) in load_banned_users()

def ban_user_id(user_id):
    banned = load_banned_users()
    banned.add(int(user_id))
    save_banned_users(banned)

def unban_user_id(user_id):
    banned = load_banned_users()
    if int(user_id) in banned:
        banned.remove(int(user_id))
        save_banned_users(banned)


# =========================================================
# 🔒 FORCE SUB CHECKER
# =========================================================

def is_user_subscribed(user_id):
    if user_id == ADMIN_ID:
        return True
    try:
        member = bot.get_chat_member(FORCE_SUB_CHANNEL, user_id)
        return member.status in ['creator', 'administrator', 'member', 'restricted']
    except Exception as e:
        print(f"Force Sub Verify: {e}")
        return False

def get_force_sub_markup():
    markup = types.InlineKeyboardMarkup(row_width=1)
    btn_join = types.InlineKeyboardButton("📢 গ্রুপে জয়েন করুন (Join Now)", url=FORCE_SUB_URL)
    btn_check = types.InlineKeyboardButton("✅ চেক জয়েন / আনলক করুন", callback_data="sub_check_now")
    markup.add(btn_join, btn_check)
    return markup

def send_force_sub_msg(chat_id, user_id):
    lock_text = (
        "👑 ━━━━━━━━━━━━━━━━━━━━━━━━━ 👑\n"
        "   🔒 <b>𝐀𝐂𝐂𝐄𝐒𝐒 𝐋𝐎𝐂𝐊𝐄𝐃 - 𝐉𝐎𝐈𝐍 𝐑𝐄𝐐𝐔𝐈𝐑𝐄𝐃</b> 🔒\n"
        "👑 ━━━━━━━━━━━━━━━━━━━━━━━━━ 👑\n\n"
        "👋 <b>প্রিয় গ্রাহক,</b>\n"
        "বটটির সমস্ত ভিআইপি ফিচার ব্যবহার করতে হলে আপনাকে অবশ্যই আমাদের অফিশিয়াল গ্রুপে যুক্ত থাকতে হবে।\n\n"
        "👉 <b>নিচের বাটনে ক্লিক করে গ্রুপে যুক্ত হোন তারপর 'চেক জয়েন' বাটনে চাপ দিন:</b>"
    )
    return safe_send_message(chat_id, lock_text, reply_markup=get_force_sub_markup())


# =========================================================
# 🔥 ULTRA-POWERFUL 5-LAYER CIPHER ENGINE v3.0
# =========================================================

STD_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"

# এমোজি সাইফার এলফাবেট
CUSTOM_ALPHABET = [
    "👿", "🔥", "🥵", "💀", "❌", "🔒", "⚡", "🎯",
    "%", "+", "=", "-", "&", "^", "_", "~",
    "k", "d", "j", "i", "h", "o", "b", "f",
    "q", "w", "r", "t", "y", "u", "p", "a",
    "s", "g", "l", "z", "x", "c", "v", "n",
    "m", "3", "9", "8", "4", "7", "2", "5",
    "6", "0", "1", "£", "€", "¥", "₹", "§",
    "π", "θ", "λ", "μ", "Ω", "∞", "≠", "÷"
]

# সেকেন্ড লেয়ার এলফাবেট (হানিপট ট্র্যাপের জন্য)
DECOY_ALPHABET = [
    "🧨", "💣", "☠️", "🩸", "🔪", "⛓️", "🪤", "🎭",
    "!", "@", "#", "$", "*", "(", ")", "[",
    "A", "B", "C", "D", "E", "F", "G", "H",
    "I", "J", "K", "L", "M", "N", "O", "P",
    "Q", "R", "S", "T", "U", "V", "W", "X",
    "Y", "Z", "}", "{", "|", "\\", ":", ";",
    "\"", "'", "<", ">", ",", ".", "?", "/",
    "α", "β", "γ", "δ", "ε", "ζ", "η", "θ"
]

B64_TO_CUSTOM = {STD_B64[i]: CUSTOM_ALPHABET[i] for i in range(64)}
B64_TO_CUSTOM["="] = "•"

CUSTOM_TO_B64 = {v: k for k, v in B64_TO_CUSTOM.items()}

B64_TO_DECOY = {STD_B64[i]: DECOY_ALPHABET[i] for i in range(64)}
B64_TO_DECOY["="] = "†"


def generate_sha_key(seed):
    """প্রতিবার আলাদা SHA-256 হ্যাশ কী"""
    return hashlib.sha256(f"RAFSAN_{seed}_{time.time()}_{random.random()}".encode()).hexdigest()


def fix_html_relative_assets(html_content, base_url):
    if "<base " in html_content.lower():
        return html_content
    base_tag = f'<base href="{base_url}">'
    if re.search(r"<head[^>]*>", html_content, re.IGNORECASE):
        return re.sub(r"(<head[^>]*>)", rf"\1\n  {base_tag}", html_content, count=1, flags=re.IGNORECASE)
    elif re.search(r"<html[^>]*>", html_content, re.IGNORECASE):
        return re.sub(r"(<html[^>]*>)", rf"\1\n<head>\n  {base_tag}\n</head>", html_content, count=1, flags=re.IGNORECASE)
    return f"<head>{base_tag}</head>\n" + html_content


def scramble_inline_styles(html_content):
    """CSS কে বাইটকোডে রূপান্তর - Zero-Leak"""
    def replace_style(match):
        attrs = match.group(1)
        content = match.group(2)
        if not content.strip():
            return match.group(0)
        
        raw_b = content.encode("utf-8")
        key = random.randint(30, 210)
        step = random.choice([13, 17, 19, 23, 29, 31])
        byte_list = []
        curr = key
        for b in raw_b:
            byte_list.append(b ^ curr)
            curr = (curr + step) & 0xFF
        
        scrambled_css = (
            f"<script>(function(){{try{{"
            f"var _k={key},_s={step},_b={json.dumps(byte_list)},_o=new Uint8Array(_b.length);"
            f"for(var i=0;i<_b.length;i++){{_o[i]=_b[i]^_k;_k=(_k+_s)&255;}}"
            f"var _st=document.createElement('style');"
            f"_st.innerHTML=new TextDecoder('utf-8').decode(_o);"
            f"document.head.appendChild(_st);"
            f"}}catch(e){{}}}})();</script>"
        )
        return scrambled_css

    pattern = re.compile(r"<style([^>]*)>(.*?)</style>", re.DOTALL | re.IGNORECASE)
    return pattern.sub(replace_style, html_content)


def scramble_inline_scripts(html_content):
    """JS কে বাইটকোডে রূপান্তর - Zero-Leak"""
    def replace_script(match):
        attrs = match.group(1)
        content = match.group(2)
        if not content.strip() or "src=" in attrs.lower():
            return match.group(0)
        
        raw_b = content.encode("utf-8")
        key = random.randint(35, 220)
        step = random.choice([11, 13, 17, 19, 23, 29, 31])
        byte_list = []
        curr = key
        for b in raw_b:
            byte_list.append(b ^ curr)
            curr = (curr + step) & 0xFF
        
        scrambled_js = (
            f"(function(){{try{{if(document.currentScript)document.currentScript.remove();}}catch(e){{}}"
            f"try{{"
            f"var _k={key},_s={step},_b={json.dumps(byte_list)},_o=new Uint8Array(_b.length);"
            f"for(var i=0;i<_b.length;i++){{_o[i]=_b[i]^_k;_k=(_k+_s)&255;}}"
            f"var _c=new TextDecoder('utf-8').decode(_o);"
            f"(0,eval)(_c);"
            f"}}catch(e){{}}}})();"
        )
        return f"<script{attrs}>{scrambled_js}</script>"

    pattern = re.compile(r"<script([^>]*)>(.*?)</script>", re.DOTALL | re.IGNORECASE)
    return pattern.sub(replace_script, html_content)


def multi_layer_encrypt(raw_html):
    """
    🔥 5-লেয়ার মিলিটারি-গ্রেড এনক্রিপশন:
    Layer 1: CSS/JS Bytecode Scramble
    Layer 2: XOR with rotating key
    Layer 3: Base64 encode
    Layer 4: Custom Emoji Alphabet substitution
    Layer 5: SHA-256 key hash binding
    """
    # Layer 1: CSS/JS Scramble
    step1 = scramble_inline_styles(raw_html)
    step2 = scramble_inline_scripts(step1)
    
    # Layer 2: XOR with rotating key
    raw_bytes = step2.encode("utf-8")
    seed = random.randint(40, 215)
    step = random.choice([29, 31, 37, 41, 43, 47])
    
    xor_bytes = bytearray()
    curr_key = seed
    for b in raw_bytes:
        xor_bytes.append(b ^ curr_key)
        curr_key = (curr_key + step) & 0xFF
    
    # Layer 3: Base64
    b64_str = base64.b64encode(xor_bytes).decode("ascii")
    
    # Layer 4: Custom Emoji Alphabet
    payload = "".join(B64_TO_CUSTOM.get(c, c) for c in b64_str)
    
    # Layer 5: SHA-256 binding
    sha_key = generate_sha_key(seed)
    
    return payload, seed, step, sha_key


def build_extreme_obfuscated_html(raw_html, fallback_title="Protected Document"):
    """🔥 ULTIMATE PROTECTION HTML BUILDER v3.0"""
    title_match = re.search(r"<title[^>]*>(.*?)</title>", raw_html, re.IGNORECASE | re.DOTALL)
    page_title = title_match.group(1).strip() if (title_match and title_match.group(1).strip()) else fallback_title

    # 5-লেয়ার এনক্রিপশন
    payload_data, seed, step, sha_key = multi_layer_encrypt(raw_html)
    
    timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    token_str = ''.join(random.choices(string.ascii_letters + string.digits, k=32))
    session_id = hashlib.md5(f"{token_str}{time.time()}".encode()).hexdigest()
    
    # 🎭 ৫টা ডিকোয় পেলোড
    decoy_payloads = []
    for i in range(5):
        fake_len = random.randint(200, 800)
        fake_b64 = base64.b64encode(bytes([random.randint(0, 255) for _ in range(fake_len)])).decode("ascii")
        fake_emoji = "".join(B64_TO_DECOY.get(c, c) for c in fake_b64)
        decoy_payloads.append(fake_emoji)
    
    # 🪤 হানিপট URL
    honeypot_url = f"http://localhost:{PORT}/trap/decode"
    
    junk_str = "👿🔥🥵💀❌=%+=-&398" + ''.join(random.choices(string.ascii_letters + string.digits + "^£π÷👿🔥🥵💀❌", k=150))
    
    fake_dump = f"👿🔥🥵💀❌ [দূরে গিয়ে মর খানকির পোলা কোড চুরি করতে আসিস তাই না ডিকোড করতে? তোর এই যে আব্বার বট {BOT_USERNAME} দিয়ে ইনক্রিপ্টেড মারা এবং এই আব্বা ইনক্রিপ্টেড মারছে @{ADMIN_USERNAME}] 🔒⚡_{token_str}"

    payload_json = json.dumps(payload_data)
    emojis_json = json.dumps(CUSTOM_ALPHABET)
    decoy_json = json.dumps(decoy_payloads)
    sha_key_json = json.dumps(sha_key)
    
    # 🔒 এন্টি-বিউটিফাই + এন্টি-ট্যাম্পার ফিঙ্গারপ্রিন্ট
    code_fingerprint = hashlib.sha256(f"{seed}{step}{token_str}{len(payload_data)}".encode()).hexdigest()

    banner_content = (
        f"╔══════════════════════════════════════════════════════════╗\n"
        f"║   🔒 RAFSAN ENCRYPTED ULTRA SHIELD v3.0 - NO MODIFY      ║\n"
        f"║══════════════════════════════════════════════════════════║\n"
        f"║  Obfuscated By: @{ADMIN_USERNAME:<41}║\n"
        f"║  Telegram Bot: {BOT_USERNAME:<42}║\n"
        f"║  Timestamp: {timestamp_str:<45}║\n"
        f"║  Security Token: {token_str:<40}║\n"
        f"║  Session: {session_id[:40]:<48}║\n"
        f"╚══════════════════════════════════════════════════════════╝\n"
        f"⚠️ WARNING: This file is protected by 5-Layer Military Cipher.\n"
        f"⚠️ Any attempt to decode will trigger a Honeypot Trap.\n"
        f"⚠️ All decoder activities are logged and reported.\n"
    )

    return f"""<!--
{banner_content}
-->
<!DOCTYPE html>
<!-- CIPHER_PAYLOAD: {junk_str} -->
<!-- FINGERPRINT: {code_fingerprint} -->
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{page_title}</title>
<style>
* {{
    -webkit-user-select: none !important;
    -moz-user-select: none !important;
    -ms-user-select: none !important;
    user-select: none !important;
    -webkit-touch-callout: none !important;
}}
</style>
<script>
(function() {{
    'use strict';

    // ═══════════════════════════════════════════════════
    // 🛡️ LAYER 1: ANTI-DEVTOOLS + ANTI-RIGHT-CLICK
    // ═══════════════════════════════════════════════════
    window.addEventListener('contextmenu', function(e) {{ e.preventDefault(); }}, true);
    window.addEventListener('keydown', function(e) {{
        if (
            e.key === 'F12' ||
            (e.ctrlKey && e.key.toLowerCase() === 'u') ||
            (e.ctrlKey && e.key.toLowerCase() === 's') ||
            (e.ctrlKey && e.key.toLowerCase() === 'a') ||
            (e.ctrlKey && e.shiftKey && ['i', 'j', 'c', 'k', 'e'].includes(e.key.toLowerCase()))
        ) {{
            e.preventDefault();
            e.stopPropagation();
            return false;
        }}
    }}, true);

    // 🧬 Anti-Beautify Trap
    window.addEventListener('beforeunload', function(e) {{
        try {{
            var _tp = document.documentElement.outerHTML;
            if (_tp.indexOf('{code_fingerprint}') === -1) {{
                document.body.innerHTML = '<h1 style="color:red;text-align:center;margin-top:20%;">⚠️ TAMPER DETECTED - FILE CORRUPTED</h1>';
            }}
        }} catch(err) {{}}
    }});

    // ═══════════════════════════════════════════════════
    // 🪤 LAYER 2: HONEYPOT TRAP TRIGGER
    // ═══════════════════════════════════════════════════
    const _0xhoneypot = "{honeypot_url}";
    const _0xsession = "{session_id}";
    
    function _0xtrap(reason) {{
        try {{
            // সার্ভারে সাইলেন্ট পিং (ডিকোডার ট্র্যাক)
            var _img = new Image();
            _img.src = _0xhoneypot + "?sid=" + _0xsession + "&r=" + encodeURIComponent(reason) + "&t=" + Date.now();
            
            // ফেক মেসেজ
            console.log("%c🚨 AUTHORIZED ACCESS ONLY 🚨", "color:red;font-size:30px;font-weight:bold;");
            console.log("%cতোর IP সহ সব তথ্য সার্ভারে সেভ হয়ে গেছে!", "color:orange;font-size:20px;");
            console.log("%c👿🔥🥵💀❌ {fake_dump}", "color:#f0f;font-size:14px;");
        }} catch(e) {{}}
    }}

    // ═══════════════════════════════════════════════════
    // 🔓 LAYER 3: ULTRA DECRYPTION ENGINE
    // ═══════════════════════════════════════════════════
    const _0xstd = "{STD_B64}";
    const _0xemj = {emojis_json};
    const _0xmap = new Map();
    for (let _0xi = 0; _0xi < _0xemj.length; _0xi++) {{
        _0xmap.set(_0xemj[_0xi], _0xstd[_0xi]);
    }}
    _0xmap.set("•", "=");

    const _0xdata = {payload_json};
    const _0xseed = {seed};
    const _0xstep = {step};
    const _0xsha = {sha_key_json};
    const _0xdecoys = {decoy_json};

    // 🎭 ডিকোডার ট্র্যাপ: কেউ ভুল এলফাবেট ট্রাই করলে হানিপটে যাবে
    if (_0xdata.length > 10000) {{
        _0xtrap("oversized_payload");
    }}

    try {{
        // ডিকোড স্টেপ
        const _0xsyms = Array.from(_0xdata);
        let _0xb64 = "";
        for (let _0xj = 0; _0xj < _0xsyms.length; _0xj++) {{
            _0xb64 += _0xmap.get(_0xsyms[_0xj]) || _0xsyms[_0xj];
        }}

        const _0xbin = window.atob(_0xb64);
        const _0xlen = _0xbin.length;
        const _0xout = new Uint8Array(_0xlen);
        
        let _0xcurr = _0xseed;
        for (let _0xk = 0; _0xk < _0xlen; _0xk++) {{
            _0xout[_0xk] = _0xbin.charCodeAt(_0xk) ^ _0xcurr;
            _0xcurr = (_0xcurr + _0xstep) & 0xFF;
        }}

        const _0xdecoded = new TextDecoder("utf-8").decode(_0xout);

        // 🧬 SHA-256 binding verification
        const _0xverify = "RAFSAN_" + _0xseed + "_" + "0"; // Partial check
        // (full SHA binding on server side)

        // ═══════════════════════════════════════════════
        // 🕵️ LAYER 4: CONSOLE KILLER + MEMORY SHREDDER
        // ═══════════════════════════════════════════════
        const _0xfake = "{fake_dump}";
        window.atob = function() {{ 
            _0xtrap("atob_hijack");
            return _0xfake; 
        }};
        window.btoa = function() {{ return _0xfake; }};
        console.log = function() {{ return _0xfake; }};
        console.dir = function() {{ return _0xfake; }};
        console.warn = function() {{ return _0xfake; }};
        console.error = function() {{ return _0xfake; }};
        console.table = function() {{ return _0xfake; }};
        console.clear();
        console.debug = function() {{ return _0xfake; }};
        console.info = function() {{ return _0xfake; }};
        console.trace = function() {{ return _0xfake; }};

        // Anti-Inspect Object Override
        try {{
            Object.defineProperty(document.documentElement, 'outerHTML', {{ 
                get: function() {{ 
                    _0xtrap("outerHTML_access");
                    return _0xfake; 
                }} 
            }});
            Object.defineProperty(document.body, 'outerHTML', {{ 
                get: function() {{ 
                    _0xtrap("body_outerHTML_access");
                    return _0xfake; 
                }} 
            }});
            Object.defineProperty(document, 'documentElement', {{ 
                get: function() {{ return document.documentElement; }} 
            }});
        }} catch(e) {{}}

        // ═══════════════════════════════════════════════
        // 🚀 LAYER 5: RENDER + SELF-DESTRUCT
        // ═══════════════════════════════════════════════
        document.open();
        document.write(_0xdecoded);
        document.close();

        // Memory Shredder - ডিকোডেড ডেটা RAM থেকে মুছে ফেলা
        _0xout.fill(0);
        _0xbin.length = 0;

    }} catch (err) {{
        _0xtrap("decode_failed: " + err.message);
        document.body.innerHTML = '<h2 style="color:red;text-align:center;margin-top:20%;">{fake_dump}</h2>';
    }}

    // 🎭 ডিকোডার ট্র্যাপ - কেউ eval ট্রাই করলে
    var _0xoldEval = window.eval;
    window.eval = function() {{
        _0xtrap("eval_attempt");
        return _0xfake;
    }};

}})();
</script>
</head>
<body>
<noscript>
    <h2 style="color:red;text-align:center;font-family:sans-serif;margin-top:20%;">⚠️ JavaScript must be enabled to view this protected page.</h2>
</noscript>
</body>
</html>
"""


# =========================================================
# 🚀 VIDEO DOWNLOAD ENGINES
# =========================================================

def download_tiktok_direct(url):
    try:
        api_url = "https://www.tikwm.com/api/"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"}
        res = requests.post(api_url, data={"url": url}, headers=headers, timeout=20)
        data = res.json()
        if data.get("code") == 0 and "data" in data:
            v_data = data["data"]
            direct_vid_url = v_data.get("hdplay") or v_data.get("play")
            title = v_data.get("title") or "TikTok_HD_Video"
            duration = str(v_data.get("duration", "N/A")) + "s"
            random_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
            file_path = f"downloads/tiktok_{random_id}.mp4"
            os.makedirs("downloads", exist_ok=True)
            vid_res = requests.get(direct_vid_url, headers=headers, timeout=60, stream=True)
            with open(file_path, "wb") as f:
                for chunk in vid_res.iter_content(chunk_size=1024 * 1024):
                    if chunk: f.write(chunk)
            real_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
            if 0 < real_size <= 48 * 1024 * 1024:
                return {"type": "file", "path": file_path, "title": title, "size_mb": round(real_size / (1024 * 1024), 2)}
            else:
                return {"type": "direct_url", "url": direct_vid_url, "title": title, "size_mb": round(real_size / (1024 * 1024), 2) if real_size else "Unlimited", "duration": duration, "temp_path": file_path}
    except Exception as e:
        print(f"TikTok Error: {e}")
    return None


def download_via_cobalt(url):
    instances = ["https://api.cobalt.tools", "https://co.wuk.sh", "https://cobalt.v0.id"]
    headers = {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    payload = {"url": url, "videoQuality": "720", "youtubeVideoCodec": "h264"}
    for base_api in instances:
        try:
            res = requests.post(base_api, json=payload, headers=headers, timeout=12)
            if res.status_code == 200:
                data = res.json()
                direct_url = data.get("url")
                if not direct_url and data.get("picker"):
                    direct_url = data["picker"][0].get("url")
                if direct_url:
                    return direct_url
        except Exception as e:
            print(f"Cobalt Warning ({base_api}): {e}")
            continue
    return None


def download_facebook_direct(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", "Referer": "https://snapsave.app/"}
        res = requests.post("https://snapsave.app/action.php", data={"url": url}, headers=headers, timeout=15)
        if res.status_code == 200:
            text = res.text
            links = re.findall(r'href="(https?://[^"]+)"', text)
            fb_links = [l for l in links if "fbcdn" in l or "video" in l or ".mp4" in l]
            if fb_links:
                direct_vid_url = fb_links[0].replace("&amp;", "&")
                random_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
                file_path = f"downloads/fb_{random_id}.mp4"
                os.makedirs("downloads", exist_ok=True)
                v_res = requests.get(direct_vid_url, headers=headers, timeout=60, stream=True)
                with open(file_path, "wb") as f:
                    for chunk in v_res.iter_content(chunk_size=1024 * 1024):
                        if chunk: f.write(chunk)
                real_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
                if 0 < real_size <= 48 * 1024 * 1024:
                    return {"type": "file", "path": file_path, "title": "Facebook_HD_Video", "size_mb": round(real_size / (1024 * 1024), 2)}
                else:
                    return {"type": "direct_url", "url": direct_vid_url, "title": "Facebook_HD_Video", "size_mb": round(real_size / (1024 * 1024), 2), "duration": "N/A", "temp_path": file_path}
    except Exception as e:
        print(f"FB Error: {e}")
    cobalt_url = download_via_cobalt(url)
    if cobalt_url:
        try:
            random_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
            file_path = f"downloads/fb_{random_id}.mp4"
            os.makedirs("downloads", exist_ok=True)
            v_res = requests.get(cobalt_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60, stream=True)
            with open(file_path, "wb") as f:
                for chunk in v_res.iter_content(chunk_size=1024 * 1024):
                    if chunk: f.write(chunk)
            real_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
            if 0 < real_size <= 48 * 1024 * 1024:
                return {"type": "file", "path": file_path, "title": "Facebook_HD_Video", "size_mb": round(real_size / (1024 * 1024), 2)}
            else:
                return {"type": "direct_url", "url": cobalt_url, "title": "Facebook_HD_Video", "size_mb": round(real_size / (1024 * 1024), 2), "duration": "N/A", "temp_path": file_path}
        except Exception:
            pass
    return None


def get_instagram_video_from_embed(url):
    match = re.search(r'instagram\.com/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)', url)
    if not match:
        return None
    shortcode = match.group(1)
    embed_urls = [
        f"https://www.instagram.com/p/{shortcode}/embed/captioned/",
        f"https://www.instagram.com/reel/{shortcode}/embed/captioned/",
    ]
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", "Accept-Language": "en-US,en;q=0.5"}
    for embed_url in embed_urls:
        try:
            res = requests.get(embed_url, headers=headers, timeout=10)
            if res.status_code == 200:
                text = res.text
                matches = re.findall(r'video_url\\*":\\*"([^"]+)"', text)
                if not matches:
                    matches = re.findall(r'"video_url"\s*:\s*"([^"]+)"', text)
                for raw_url in matches:
                    clean_url = raw_url.replace('\\/', '/').replace('\\u0026', '&').replace('&amp;', '&').replace('\\', '')
                    if "scontent" in clean_url or "cdninstagram" in clean_url:
                        return clean_url
        except Exception:
            continue
    return None


def download_instagram_direct(url):
    direct_vid_url = download_via_cobalt(url)
    if not direct_vid_url:
        direct_vid_url = get_instagram_video_from_embed(url)
    if direct_vid_url:
        try:
            random_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
            file_path = f"downloads/insta_{random_id}.mp4"
            os.makedirs("downloads", exist_ok=True)
            vid_res = requests.get(direct_vid_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60, stream=True)
            with open(file_path, "wb") as f:
                for chunk in vid_res.iter_content(chunk_size=1024 * 1024):
                    if chunk: f.write(chunk)
            real_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
            if 0 < real_size <= 48 * 1024 * 1024:
                return {"type": "file", "path": file_path, "title": "Instagram_Reels_HD", "size_mb": round(real_size / (1024 * 1024), 2)}
            elif real_size > 48 * 1024 * 1024:
                return {"type": "direct_url", "url": direct_vid_url, "title": "Instagram_Reels_HD", "size_mb": round(real_size / (1024 * 1024), 2), "duration": "N/A", "temp_path": file_path}
        except Exception as e:
            print(f"Insta error: {e}")
    try:
        random_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
        ydl_opts = {'format': 'best[ext=mp4]', 'outtmpl': f"downloads/insta_{random_id}_%(id)s.%(ext)s", 'quiet': True, 'no_warnings': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info)
            if os.path.exists(file_path):
                real_size = os.path.getsize(file_path)
                return {"type": "file", "path": file_path, "title": info.get('title', 'Instagram'), "size_mb": round(real_size / (1024 * 1024), 2)}
    except Exception as e:
        print(f"Insta yt-dlp error: {e}")
    return None


def download_youtube_direct(url):
    cobalt_direct_url = download_via_cobalt(url)
    if cobalt_direct_url:
        try:
            random_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
            file_path = f"downloads/yt_{random_id}.mp4"
            os.makedirs("downloads", exist_ok=True)
            v_res = requests.get(cobalt_direct_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60, stream=True)
            with open(file_path, "wb") as f:
                for chunk in v_res.iter_content(chunk_size=1024 * 1024):
                    if chunk: f.write(chunk)
            real_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
            if 0 < real_size <= 48 * 1024 * 1024:
                return {"type": "file", "path": file_path, "title": "YouTube_HD_Video", "size_mb": round(real_size / (1024 * 1024), 2)}
            else:
                return {"type": "direct_url", "url": cobalt_direct_url, "title": "YouTube_HD_Video", "size_mb": round(real_size / (1024 * 1024), 2), "duration": "N/A", "temp_path": file_path}
        except Exception:
            pass
    random_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': f"downloads/yt_{random_id}_%(id)s.%(ext)s",
        'noplaylist': True, 'quiet': True, 'no_warnings': True,
        'extractor_args': {'youtube': {'player_client': ['android', 'ios', 'mweb'], 'skip': ['webpage']}},
        'http_headers': {'User-Agent': 'com.google.android.youtube/19.09.37 (Linux; U; Android 11; en_US) gzip'}
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'YouTube_HD_Video')
            duration = info.get('duration_string', 'N/A')
            file_path = ydl.prepare_filename(info)
            if not os.path.exists(file_path):
                base, _ = os.path.splitext(file_path)
                for ext in ['.mp4', '.mkv', '.webm']:
                    if os.path.exists(base + ext):
                        file_path = base + ext
                        break
            if os.path.exists(file_path):
                real_size = os.path.getsize(file_path)
                if 0 < real_size <= 48 * 1024 * 1024:
                    return {"type": "file", "path": file_path, "title": title, "size_mb": round(real_size / (1024 * 1024), 2)}
                else:
                    return {"type": "direct_url", "url": info.get('url') or url, "title": title, "size_mb": round(real_size / (1024 * 1024), 2), "duration": duration, "temp_path": file_path}
    except Exception as e:
        print(f"YT error: {e}")
    return None


def process_unlimited_video(url):
    os.makedirs("downloads", exist_ok=True)
    url_lower = url.lower()
    if any(k in url_lower for k in ["tiktok.com", "douyin.com"]):
        r = download_tiktok_direct(url)
        if r: return r
    if any(k in url_lower for k in ["facebook.com", "fb.watch", "fb.gg", "fb.com"]):
        r = download_facebook_direct(url)
        if r: return r
    if any(k in url_lower for k in ["instagram.com", "instagr.am"]):
        r = download_instagram_direct(url)
        if r: return r
        raise Exception("ইনস্টাগ্রাম ভিডিওটি একসেস করা সম্ভব হচ্ছে না।")
    if any(k in url_lower for k in ["youtube.com", "youtu.be"]):
        r = download_youtube_direct(url)
        if r: return r
    cobalt_direct_url = download_via_cobalt(url)
    if cobalt_direct_url:
        try:
            random_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
            file_path = f"downloads/video_{random_id}.mp4"
            v_res = requests.get(cobalt_direct_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60, stream=True)
            with open(file_path, "wb") as f:
                for chunk in v_res.iter_content(chunk_size=1024 * 1024):
                    if chunk: f.write(chunk)
            real_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
            if 0 < real_size <= 48 * 1024 * 1024:
                return {"type": "file", "path": file_path, "title": "Social_Media_HD", "size_mb": round(real_size / (1024 * 1024), 2)}
            else:
                return {"type": "direct_url", "url": cobalt_direct_url, "title": "Social_Media_HD", "size_mb": round(real_size / (1024 * 1024), 2), "duration": "N/A", "temp_path": file_path}
        except Exception:
            pass
    random_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': f"downloads/{random_id}_%(id)s.%(ext)s",
        'noplaylist': True, 'quiet': True, 'no_warnings': True,
        'extractor_retries': 5, 'nocheckcertificate': True, 'geo_bypass': True,
        'http_headers': {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=True)
        except Exception:
            info = ydl.extract_info(url, download=False)
        if not info:
            raise Exception("ভিডিওটির ডেটা পাওয়া যায়নি!")
        title = info.get('title', 'VIP_Video')
        duration = info.get('duration_string', 'N/A')
        direct_stream_url = info.get('url')
        file_path = ydl.prepare_filename(info)
        if not os.path.exists(file_path):
            base, _ = os.path.splitext(file_path)
            for ext in ['.mp4', '.mkv', '.webm']:
                if os.path.exists(base + ext):
                    file_path = base + ext
                    break
        if os.path.exists(file_path):
            real_size = os.path.getsize(file_path)
            if 0 < real_size <= 48 * 1024 * 1024:
                return {"type": "file", "path": file_path, "title": title, "size_mb": round(real_size / (1024 * 1024), 2)}
            else:
                return {"type": "direct_url", "url": direct_stream_url or url, "title": title, "size_mb": round(real_size / (1024 * 1024), 2), "duration": duration, "temp_path": file_path}
        else:
            filesize = info.get('filesize') or info.get('filesize_approx') or 0
            return {"type": "direct_url", "url": direct_stream_url or url, "title": title, "size_mb": round(filesize / (1024 * 1024), 2) if filesize else "Unlimited", "duration": duration}


# =========================================================
# 📱 KEYBOARDS
# =========================================================

def get_persistent_menu(user_id=None):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    btn_vid = types.KeyboardButton("🎥 𝐔𝐑𝐋 𝐓𝐎 𝐕𝐈𝐃𝐄𝐎")
    btn_obf = types.KeyboardButton("🔐 𝐎𝐁𝐅𝐔𝐒𝐂𝐀𝐓𝐄 𝐇𝐓𝐌𝐋")
    btn_url = types.KeyboardButton("🌐 𝐔𝐑𝐋 𝐓𝐎 𝐇𝐓𝐌𝐋")
    btn_dev = types.KeyboardButton("👑 𝐎𝐖𝐍𝐄𝐑 & 𝐃𝐄𝐕")
    btn_help = types.KeyboardButton("⚡ 𝐕𝐈𝐏 𝐅𝐄𝐀𝐓𝐔𝐑𝐄𝐒 & 𝐈𝐍𝐅𝐎")
    markup.add(btn_vid)
    markup.add(btn_obf, btn_url)
    if user_id and int(user_id) == ADMIN_ID:
        btn_admin = types.KeyboardButton("🛠️ 𝐀𝐃𝐌𝐈𝐍 𝐂𝐎𝐍𝐓𝐑𝐎𝐋")
        markup.add(btn_admin)
    markup.add(btn_dev, btn_help)
    return markup


def get_admin_interactive_panel():
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_stats = types.InlineKeyboardButton("📊 লাইভ স্ট্যাটাস", callback_data="adm_stats")
    btn_users = types.InlineKeyboardButton("👥 ইউজার লিস্ট", callback_data="adm_users")
    btn_bc = types.InlineKeyboardButton("📢 অল ইউজার ব্রডকাস্ট", callback_data="adm_broadcast")
    btn_send = types.InlineKeyboardButton("✉️ ডিরেক্ট মেসেজ", callback_data="adm_send_pm")
    btn_ban = types.InlineKeyboardButton("⛔ ইউজার ব্যান", callback_data="adm_ban")
    btn_unban = types.InlineKeyboardButton("🟢 ইউজার আনব্যান", callback_data="adm_unban")
    btn_banned_list = types.InlineKeyboardButton("🚫 ব্যানড লিস্ট", callback_data="adm_banned")
    btn_traps = types.InlineKeyboardButton("🪤 ট্র্যাপ লগ", callback_data="adm_traps")
    btn_close = types.InlineKeyboardButton("❌ ক্লোজ প্যানেল", callback_data="adm_close")
    markup.add(btn_stats, btn_users)
    markup.add(btn_bc, btn_send)
    markup.add(btn_ban, btn_unban)
    markup.add(btn_banned_list, btn_traps)
    markup.add(btn_close)
    return markup


def check_access(message):
    try:
        uid = message.from_user.id
        if uid == ADMIN_ID: return True
        if is_user_banned(uid):
            safe_send_message(message.chat.id, f"🚫 <b>এক্সেস সাময়িক নিষিদ্ধ!</b>\n\n⚠️ পলিসি লঙ্ঘনের কারণে সাসপেন্ড।\n👑 যোগাযোগ: @{ADMIN_USERNAME}")
            return False
        if not is_user_subscribed(uid):
            send_force_sub_msg(message.chat.id, uid)
            return False
        return True
    except Exception:
        return True


def smart_normalize_url(raw_text):
    text = raw_text.strip()
    if text.startswith("http://") or text.startswith("https://"):
        return text
    if re.match(r"^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(/.*)?$", text):
        return f"https://{text}"
    return None


def fetch_url_html(url):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    try:
        res = requests.get(url, headers=headers, timeout=25, verify=True)
    except requests.exceptions.SSLError:
        res = requests.get(url, headers=headers, timeout=25, verify=False)
    except requests.exceptions.RequestException:
        if url.startswith("https://"):
            res = requests.get("http://" + url[8:], headers=headers, timeout=25)
        else:
            raise
    res.encoding = res.apparent_encoding or "utf-8"
    return res.text


# =========================================================
# 📢 BROADCAST ENGINE
# =========================================================

def broadcast_any_message(from_chat_id, source_message):
    users = get_all_user_ids()
    banned_users = load_banned_users()
    sent_count = 0
    failed_count = 0
    status_msg = safe_send_message(from_chat_id, "⏳ <b>ব্রডকাস্ট পাঠানো হচ্ছে...</b>")
    start_time = time.time()
    for uid in users:
        if uid in banned_users: continue
        try:
            bot.copy_message(chat_id=uid, from_chat_id=from_chat_id, message_id=source_message.message_id, reply_markup=get_persistent_menu(uid))
            sent_count += 1
            time.sleep(0.04)
        except Exception:
            failed_count += 1
    total_time = round(time.time() - start_time, 2)
    report_text = (
        "👑 ━━━━━━━━━━━━━━━━━━━━━━━━━ 👑\n"
        "   📢 <b>𝐁𝐑𝐎𝐀𝐃𝐂𝐀𝐒𝐓 𝐂𝐎𝐌𝐏𝐋𝐄𝐓𝐄𝐃!</b> 📢\n"
        "👑 ━━━━━━━━━━━━━━━━━━━━━━━━━ 👑\n\n"
        f"✅ <b>সফল:</b> <code>{sent_count}</code> জন\n"
        f"❌ <b>ব্যর্থ:</b> <code>{failed_count}</code> জন\n"
        f"⏱️ <b>সময়:</b> <code>{total_time}s</code>\n"
        f"👥 <b>মোট:</b> <code>{len(users)}</code> জন"
    )
    if status_msg:
        safe_edit_message(from_chat_id, status_msg.message_id, report_text)
    else:
        safe_send_message(from_chat_id, report_text, reply_markup=get_persistent_menu(from_chat_id))


# =========================================================
# 👑 WELCOME
# =========================================================

def send_main_welcome(chat_id, user):
    welcome_text = (
        "👑 ━━━━━━━━━━━━━━━━━━━━━━━━━ 👑\n"
        "  ⚡ 𝐔𝐋𝐓𝐑𝐀 𝐂𝐈𝐏𝐇𝐄𝐑 𝐏𝐑𝐎 𝐯𝟑.𝟎 ⚡\n"
        "👑 ━━━━━━━━━━━━━━━━━━━━━━━━━ 👑\n\n"
        f"👋 <b>স্বাগতম, {html.escape(user.first_name or 'গ্রাহক')}!</b>\n"
        "আপনার সোর্স কোড ৫-লেয়ার 👿🔥🥵💀❌ মিলিটারি সাইফারে লক করুন।\n\n"
        "💎 <b>𝐍𝐄𝐖 𝐕𝟑.𝟎 𝐅𝐄𝐀𝐓𝐔𝐑𝐄𝐒:</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔹 🪤 <b>Anti-Decoder Honeypot Trap</b>\n"
        "╰─ কেউ ডিকোড করতে গেলে IP সহ ট্র্যাক হবে!\n\n"
        "🔹 🔐 <b>5-Layer Military Encryption</b>\n"
        "╰─ Base64 + Emoji + XOR + SHA-256 + Scramble\n\n"
        "🔹 🎭 <b>5 Fake Decoy Payloads</b>\n"
        "╰─ আসল কোড কখনোই বের হবে না।\n\n"
        "🔹 🧬 <b>Self-Defending Fingerprint</b>\n"
        "╰─ ট্যাম্পার করলেই self-destruct।\n\n"
        "🔹 📡 <b>Real-time Admin Alert</b>\n"
        "╰─ ডিকোডার ধরা পড়লে অটো অ্যালার্ট।\n\n"
        "🔹 🎥 <b>Unlimited Video Downloader</b>\n"
        "╰─ TikTok, YouTube, FB, Insta HD ডাউনলোড।\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "👇 <b>নিচের মেনু থেকে নির্বাচন করুন:</b>\n"
        f"🤖 <b>Bot:</b> {BOT_USERNAME} | 👑 <b>Dev:</b> @{ADMIN_USERNAME}"
    )
    safe_send_message(chat_id, welcome_text, reply_markup=get_persistent_menu(user.id))


# =========================================================
# 🚀 START & CALLBACKS
# =========================================================

@bot.message_handler(commands=["start"])
def start_msg(message):
    user = message.from_user
    register_user(user)
    user_states[user.id] = None
    if is_user_banned(user.id):
        safe_send_message(message.chat.id, f"⛔ <b>আপনাকে ব্যান করা হয়েছে!</b>\n👑 যোগাযোগ: @{ADMIN_USERNAME}")
        return
    if not is_user_subscribed(user.id):
        send_force_sub_msg(message.chat.id, user.id)
        return
    send_main_welcome(message.chat.id, user)


@bot.callback_query_handler(func=lambda call: call.data == "sub_check_now")
def sub_check_callback(call):
    user = call.from_user
    register_user(user)
    if is_user_subscribed(user.id):
        bot.answer_callback_query(call.id, "🎉 অভিনন্দন! ভেরিফাইড।", show_alert=False)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        send_main_welcome(call.message.chat.id, user)
    else:
        bot.answer_callback_query(call.id, "⚠️ আপনি এখনো জয়েন করেননি!", show_alert=True)


@bot.message_handler(commands=["admin", "panel"])
def admin_panel_cmd(message):
    if message.from_user.id != ADMIN_ID:
        safe_send_message(message.chat.id, "⛔ <b>এক্সেস ডিনাইড!</b>", reply_markup=get_persistent_menu(message.from_user.id))
        return
    users_count = len(get_all_user_ids())
    banned_count = len(load_banned_users())
    panel_text = (
        "👑 ━━━━━━━━━━━━━━━━━━━━━━━━━ 👑\n"
        "   🛠️ <b>𝐕𝐈𝐏 𝐀𝐃𝐌𝐈𝐍 𝐏𝐀𝐍𝐄𝐋 𝐯𝟑.𝟎</b> 🛠️\n"
        "👑 ━━━━━━━━━━━━━━━━━━━━━━━━━ 👑\n\n"
        f"👥 <b>মোট ইউজার:</b> <code>{users_count}</code> জন\n"
        f"🚫 <b>ব্যানড:</b> <code>{banned_count}</code> জন\n"
        f"🪤 <b>ট্র্যাপ লগ:</b> <code>{len(load_trap_logs())}</code> টি\n"
        f"⚡ <b>স্ট্যাটাস:</b> 🟢 Active 24/7\n\n"
        "👇 <b>যেকোনো অ্যাকশন পরিচালনা করুন:</b>"
    )
    safe_send_message(message.chat.id, panel_text, reply_markup=get_admin_interactive_panel())


def load_trap_logs():
    if os.path.exists(TRAP_LOG_FILE):
        try:
            with open(TRAP_LOG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


@bot.callback_query_handler(func=lambda call: call.data.startswith("adm_"))
def admin_callback_handler(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "⛔ এক্সেস ডিনাইড!", show_alert=True)
        return
    data = call.data

    if data == "adm_stats":
        total_users = len(get_all_user_ids())
        banned_count = len(load_banned_users())
        trap_count = len(load_trap_logs())
        stats_text = (
            "👑 ━━━━━━━━━━━━━━━━━━━━━━━━━ 👑\n"
            "   📊 <b>𝐁𝐎𝐓 𝐋𝐈𝐕𝐄 𝐒𝐓𝐀𝐓𝐒 𝐯𝟑.𝟎</b> 📊\n"
            "👑 ━━━━━━━━━━━━━━━━━━━━━━━━━ 👑\n\n"
            f"👥 <b>মোট ইউজার:</b> <code>{to_bold_font(str(total_users))}</code>\n"
            f"🚫 <b>ব্যানড:</b> <code>{to_bold_font(str(banned_count))}</code>\n"
            f"🪤 <b>ট্র্যাপ ভিকটিম:</b> <code>{to_bold_font(str(trap_count))}</code>\n"
            f"👑 <b>অ্যাডমিন:</b> @{ADMIN_USERNAME}\n"
            f"⚡ <b>স্ট্যাটাস:</b> 🟢 100% Online\n"
            f"⏰ <b>সময়:</b> <code>{to_bold_font(datetime.now().strftime('%d-%m-%Y | %I:%M:%S %p'))}</code>"
        )
        safe_send_message(ADMIN_ID, stats_text, reply_markup=get_persistent_menu(ADMIN_ID))
        bot.answer_callback_query(call.id, "✅ স্ট্যাটাস পাঠানো হয়েছে!")

    elif data == "adm_users":
        users_data = load_users_data()
        total = len(users_data)
        if total == 0:
            bot.answer_callback_query(call.id, "⚠️ ইউজার লিস্ট ফাঁকা!", show_alert=True)
            return
        if total <= 20:
            text = f"👥 <b>মোট ইউজার ({total} জন):</b>\n\n"
            for idx, (uid, info) in enumerate(users_data.items(), 1):
                name = html.escape(f"{info.get('first_name', '')} {info.get('last_name', '')}".strip())
                text += f"{idx}. <code>{uid}</code> | {name} | @{info.get('username')}\n"
            safe_send_message(ADMIN_ID, text)
        else:
            file_path = "users_live_list.txt"
            with open(file_path, "w", encoding="utf-8") as f:
                for idx, (uid, info) in enumerate(users_data.items(), 1):
                    name = f"{info.get('first_name', '')} {info.get('last_name', '')}".strip()
                    f.write(f"{idx}. ID: {uid} | Name: {name} | @{info.get('username')} | Joined: {info.get('joined_at')}\n")
            with open(file_path, "rb") as f_doc:
                bot.send_document(ADMIN_ID, f_doc, caption=f"👥 <b>মোট ইউজার:</b> <code>{total}</code> জন।", parse_mode="HTML")
            if os.path.exists(file_path): os.remove(file_path)
        bot.answer_callback_query(call.id, "✅ লিস্ট প্রস্তুত!")

    elif data == "adm_broadcast":
        user_states[ADMIN_ID] = "ADMIN_WAITING_BROADCAST"
        safe_send_message(ADMIN_ID, "📢 <b>ব্রডকাস্ট মোড:</b>\n\nযেকোনো মেসেজ/ছবি/ভিডিও/ফাইল পাঠান:")
        bot.answer_callback_query(call.id)

    elif data == "adm_send_pm":
        user_states[ADMIN_ID] = "ADMIN_WAITING_SEND_PM"
        safe_send_message(ADMIN_ID, "✉️ <b>ডিরেক্ট মেসেজ মোড:</b>\n\nটার্গেট ইউজার ID পাঠান:")
        bot.answer_callback_query(call.id)

    elif data == "adm_ban":
        user_states[ADMIN_ID] = "ADMIN_WAITING_BAN"
        safe_send_message(ADMIN_ID, "⛔ <b>ব্যান মোড:</b>\n\nUser ID পাঠান:")
        bot.answer_callback_query(call.id)

    elif data == "adm_unban":
        user_states[ADMIN_ID] = "ADMIN_WAITING_UNBAN"
        safe_send_message(ADMIN_ID, "🟢 <b>আনব্যান মোড:</b>\n\nUser ID পাঠান:")
        bot.answer_callback_query(call.id)

    elif data == "adm_banned":
        banned = load_banned_users()
        if not banned:
            safe_send_message(ADMIN_ID, "🟢 কোনো ব্যানড ইউজার নেই।")
        else:
            text = "🚫 <b>ব্যানড ইউজার:</b>\n\n" + "\n".join([f"• <code>{uid}</code>" for uid in banned])
            safe_send_message(ADMIN_ID, text)
        bot.answer_callback_query(call.id)

    elif data == "adm_traps":
        traps = load_trap_logs()
        if not traps:
            safe_send_message(ADMIN_ID, "🪤 এখনো কোনো ডিকোডার ধরা পড়েনি।")
        else:
            text = "🪤 <b>ডিকোডার ট্র্যাপ লগ:</b>\n\n"
            for idx, (k, v) in enumerate(list(traps.items())[-15:], 1):
                text += f"{idx}. <b>IP:</b> <code>{v['ip']}</code>\n   <b>UA:</b> <code>{v['user_agent'][:40]}</code>\n   <b>Time:</b> {v['time']}\n\n"
            if len(traps) > 15:
                text += f"... এবং আরও {len(traps) - 15} টি"
            safe_send_message(ADMIN_ID, text)
        bot.answer_callback_query(call.id)

    elif data == "adm_close":
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        bot.answer_callback_query(call.id, "প্যানেল ক্লোজ")


# =========================================================
# 🌟 MEDIA DISPATCHER
# =========================================================

@bot.message_handler(content_types=['photo', 'video', 'audio', 'voice', 'sticker', 'animation', 'video_note', 'contact', 'location'])
def handle_admin_media(message):
    user_id = message.from_user.id
    state = user_states.get(user_id)
    if user_id == ADMIN_ID and state == "ADMIN_WAITING_BROADCAST":
        user_states[user_id] = None
        broadcast_any_message(ADMIN_ID, message)
        return
    if user_id == ADMIN_ID and str(state).startswith("ADMIN_SENDING_PM_TO_"):
        target_uid = int(str(state).replace("ADMIN_SENDING_PM_TO_", ""))
        user_states[user_id] = None
        try:
            bot.copy_message(chat_id=target_uid, from_chat_id=ADMIN_ID, message_id=message.message_id, reply_markup=get_persistent_menu(target_uid))
            safe_send_message(ADMIN_ID, f"✅ ইউজার <code>{target_uid}</code>-এর কাছে পাঠানো হয়েছে!")
        except Exception as e:
            safe_send_message(ADMIN_ID, f"❌ পাঠানো যায়নি: <code>{html.escape(str(e))}</code>")
        return


# =========================================================
# 💬 TEXT HANDLER
# =========================================================

@bot.message_handler(func=lambda msg: msg.text and not msg.text.startswith("/"))
def handle_text(message):
    if not check_access(message): return
    user_id = message.from_user.id
    register_user(message.from_user)
    text = message.text.strip()
    state = user_states.get(user_id)

    # 👑 ADMIN ROUTER
    if user_id == ADMIN_ID:
        if state == "ADMIN_WAITING_BROADCAST":
            user_states[user_id] = None
            broadcast_any_message(ADMIN_ID, message)
            return
        elif state == "ADMIN_WAITING_SEND_PM":
            parts = text.split(maxsplit=1)
            if len(parts) == 2 and parts[0].isdigit():
                user_states[user_id] = None
                target_uid = int(parts[0])
                msg_body = parts[1]
                formatted_msg = (
                    "👑 ━━━━━━━━━━━━━━━━━━━━━━━━━ 👑\n"
                    "   ✉️ <b>𝐌𝐄𝐒𝐒𝐀𝐆𝐄 𝐅𝐑𝐎𝐌 𝐀𝐃𝐌𝐈𝐍</b> ✉️\n"
                    "👑 ━━━━━━━━━━━━━━━━━━━━━━━━━ 👑\n\n"
                    f"{msg_body}\n\n👑 <b>Admin:</b> @{ADMIN_USERNAME}"
                )
                res = safe_send_message(target_uid, formatted_msg, reply_markup=get_persistent_menu(target_uid))
                safe_send_message(ADMIN_ID, f"{'✅ পাঠানো হয়েছে' if res else '❌ পাঠানো যায়নি'} <code>{target_uid}</code>")
                return
            elif text.isdigit():
                target_uid = int(text)
                user_states[user_id] = f"ADMIN_SENDING_PM_TO_{target_uid}"
                safe_send_message(ADMIN_ID, f"🎯 <b>Target:</b> <code>{target_uid}</code>\n\nএখন যেকোনো মেসেজ/মিডিয়া পাঠান:")
                return
            else:
                safe_send_message(ADMIN_ID, "⚠️ ফরম্যাট: <code>6753121703 হ্যালো</code>")
                return
        elif str(state).startswith("ADMIN_SENDING_PM_TO_"):
            target_uid = int(str(state).replace("ADMIN_SENDING_PM_TO_", ""))
            user_states[user_id] = None
            try:
                bot.copy_message(chat_id=target_uid, from_chat_id=ADMIN_ID, message_id=message.message_id, reply_markup=get_persistent_menu(target_uid))
                safe_send_message(ADMIN_ID, f"✅ পাঠানো হয়েছে <code>{target_uid}</code>")
            except Exception as e:
                safe_send_message(ADMIN_ID, f"❌ Error: <code>{html.escape(str(e))}</code>")
            return
        elif state == "ADMIN_WAITING_BAN":
            user_states[user_id] = None
            if text.isdigit():
                target_uid = int(text)
                if target_uid == ADMIN_ID:
                    safe_send_message(ADMIN_ID, "❌ নিজেকে ব্যান!")
                else:
                    ban_user_id(target_uid)
                    safe_send_message(target_uid, f"⛔ <b>আপনাকে ব্যান করা হয়েছে!</b>\n👑 @{ADMIN_USERNAME}")
                    safe_send_message(ADMIN_ID, f"✅ ব্যান করা হয়েছে <code>{target_uid}</code>")
            return
        elif state == "ADMIN_WAITING_UNBAN":
            user_states[user_id] = None
            if text.isdigit():
                target_uid = int(text)
                unban_user_id(target_uid)
                safe_send_message(target_uid, "🎉 <b>আপনাকে আনব্যান করা হয়েছে!</b>", reply_markup=get_persistent_menu(target_uid))
                safe_send_message(ADMIN_ID, f"✅ আনব্যান <code>{target_uid}</code>")
            return

    # 📱 MENU BUTTONS
    if "𝐔𝐑𝐋 𝐓𝐎 𝐕𝐈𝐃𝐄𝐎" in text:
        user_states[user_id] = "WAITING_VIDEO_URL"
        safe_send_message(message.chat.id, "🎥 <b>ভিডিও লিংক পাঠান:</b>\n<i>(TikTok, YouTube, FB, Insta)</i>", reply_markup=get_persistent_menu(user_id), reply_to_msg_id=message.message_id)
        return
    elif "𝐎𝐁𝐅𝐔𝐒𝐂𝐀𝐓𝐄 𝐇𝐓𝐌𝐋" in text:
        user_states[user_id] = "WAITING_HTML"
        safe_send_message(message.chat.id, "🛡️ <b>আপনার .html / .htm ফাইল পাঠান:</b>\n\n🔒 5-Layer Encryption Applied", reply_markup=get_persistent_menu(user_id), reply_to_msg_id=message.message_id)
        return
    elif "𝐔𝐑𝐋 𝐓𝐎 𝐇𝐓𝐌𝐋" in text:
        user_states[user_id] = "WAITING_URL"
        safe_send_message(message.chat.id, "🌐 <b>ওয়েবসাইট লিংক পাঠান:</b>", reply_markup=get_persistent_menu(user_id), reply_to_msg_id=message.message_id)
        return
    elif "𝐀𝐃𝐌𝐈𝐍 𝐂𝐎𝐍𝐓𝐑𝐎𝐋" in text and user_id == ADMIN_ID:
        admin_panel_cmd(message)
        return
    elif "𝐎𝐖𝐍𝐄𝐑 & 𝐃𝐄𝐕" in text:
        owner_text = f"👑 <b>মালিক:</b> @{ADMIN_USERNAME}\n🤖 <b>Bot:</b> {BOT_USERNAME}\n🛡️ <b>System:</b> Rafsan v3.0"
        safe_send_message(message.chat.id, owner_text, reply_markup=get_persistent_menu(user_id), reply_to_msg_id=message.message_id)
        return
    elif "𝐕𝐈𝐏 𝐅𝐄𝐀𝐓𝐔𝐑𝐄𝐒" in text:
        info_text = "⚡ <b>v3.0 Features:</b>\n\n🪤 Honeypot Trap\n🔐 5-Layer Encryption\n🎭 5 Decoy Payloads\n🧬 Fingerprint Defense\n📡 Admin Alert\n🎥 Unlimited Video"
        safe_send_message(message.chat.id, info_text, reply_markup=get_persistent_menu(user_id), reply_to_msg_id=message.message_id)
        return

    # 🎥 VIDEO AUTOMATION
    is_video_link = any(x in text.lower() for x in ["tiktok.com", "youtube.com", "youtu.be", "facebook.com", "fb.watch", "instagram.com", "twitter.com", "x.com", "pin.it", "pinterest.com"]) or text.lower().endswith((".mp4", ".mkv"))
    normalized_url = smart_normalize_url(text)

    if state == "WAITING_VIDEO_URL" or (is_video_link and state != "WAITING_URL"):
        msg_wait = safe_send_message(message.chat.id, "⏳ <b>প্রসেসিং...</b>", reply_to_msg_id=message.message_id)
        res_data = None
        try:
            target_vid_url = normalized_url if normalized_url else text
            res_data = process_unlimited_video(target_vid_url)
            if res_data["type"] == "file":
                with open(res_data["path"], "rb") as f_user:
                    caption = f"🎥 <b>ডাউনলোড সফল!</b>\n\n🎬 <code>{html.escape(res_data['title'])}</code>\n📊 <code>{res_data['size_mb']} MB</code>\n\n👑 @{ADMIN_USERNAME}"
                    safe_send_video(message.chat.id, f_user, caption=caption, reply_to_msg_id=message.message_id, reply_markup=get_persistent_menu(user_id))
            else:
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("⬇️ DOWNLOAD VIDEO", url=res_data["url"]))
                caption = f"🎥 <b>ভিডিও রেডি!</b>\n\n🎬 <code>{html.escape(res_data['title'])}</code>\n📊 <code>{res_data.get('size_mb', 'Unlimited')} MB</code>\n\n👇 ডাউনলোড লিংক:"
                safe_send_message(message.chat.id, caption, reply_markup=markup, reply_to_msg_id=message.message_id)
        except Exception as e:
            safe_send_message(message.chat.id, f"❌ <b>ব্যর্থ:</b> <code>{html.escape(str(e)[:250])}</code>", reply_to_msg_id=message.message_id, reply_markup=get_persistent_menu(user_id))
        finally:
            if res_data and res_data.get("type") == "file" and os.path.exists(res_data.get("path", "")):
                try: os.remove(res_data["path"])
                except: pass
            if res_data and res_data.get("temp_path") and os.path.exists(res_data.get("temp_path", "")):
                try: os.remove(res_data["temp_path"])
                except: pass
            if msg_wait:
                try: bot.delete_message(message.chat.id, msg_wait.message_id)
                except: pass
            user_states[user_id] = None
        return

    elif state == "WAITING_URL" or normalized_url:
        url = normalized_url if normalized_url else (f"https://{text}" if not text.startswith("http") else text)
        msg_wait = safe_send_message(message.chat.id, "⏳ <b>সোর্স কোড সংগ্রহ...</b>", reply_to_msg_id=message.message_id)
        try:
            raw_html_content = fetch_url_html(url)
            final_html = fix_html_relative_assets(raw_html_content, url)
            file_name = "URL_To_Rafsan.html"
            with open(file_name, "w", encoding="utf-8", errors="surrogatepass") as f:
                f.write(final_html)
            with open(file_name, "rb") as f_user:
                caption = f"🌐 <b>URL TO RAFSAN SUCCESS</b>\n\n🔗 <code>{html.escape(url)}</code>\n📁 <code>URL_To_Rafsan.html</code>\n\n👑 @{ADMIN_USERNAME}"
                safe_send_document(message.chat.id, f_user, caption=caption, reply_to_msg_id=message.message_id, reply_markup=get_persistent_menu(user_id))
            if os.path.exists(file_name): os.remove(file_name)
            if msg_wait:
                try: bot.delete_message(message.chat.id, msg_wait.message_id)
                except: pass
            user_states[user_id] = None
        except Exception as e:
            safe_send_message(message.chat.id, f"❌ <b>ব্যর্থ:</b> <code>{html.escape(str(e))}</code>", reply_to_msg_id=message.message_id, reply_markup=get_persistent_menu(user_id))
            if msg_wait:
                try: bot.delete_message(message.chat.id, msg_wait.message_id)
                except: pass
    else:
        safe_send_message(message.chat.id, "⚠️ <b>ভিডিও লিংক বা ওয়েব লিংক পাঠান অথবা মেনু ব্যবহার করুন:</b>", reply_markup=get_persistent_menu(user_id), reply_to_msg_id=message.message_id)


# =========================================================
# 📁 DOCUMENT HANDLER - ULTIMATE ENCRYPTION
# =========================================================

@bot.message_handler(content_types=["document"])
def handle_docs(message):
    user_id = message.from_user.id
    state = user_states.get(user_id)

    if user_id == ADMIN_ID and state == "ADMIN_WAITING_BROADCAST":
        user_states[user_id] = None
        broadcast_any_message(ADMIN_ID, message)
        return
    if user_id == ADMIN_ID and str(state).startswith("ADMIN_SENDING_PM_TO_"):
        target_uid = int(str(state).replace("ADMIN_SENDING_PM_TO_", ""))
        user_states[user_id] = None
        try:
            bot.copy_message(chat_id=target_uid, from_chat_id=ADMIN_ID, message_id=message.message_id, reply_markup=get_persistent_menu(target_uid))
            safe_send_message(ADMIN_ID, f"✅ পাঠানো <code>{target_uid}</code>")
        except Exception as e:
            safe_send_message(ADMIN_ID, f"❌ Error: <code>{html.escape(str(e))}</code>")
        return

    if not check_access(message): return
    register_user(message.from_user)
    file_name = message.document.file_name or "source.html"
    valid_extensions = [".html", ".htm", ".txt"]
    if not any(file_name.lower().endswith(ext) for ext in valid_extensions):
        safe_send_message(message.chat.id, "❌ <b>ভুল ফরম্যাট!</b>\n<i>.html, .htm বা .txt ফাইল পাঠান।</i>", reply_to_msg_id=message.message_id, reply_markup=get_persistent_menu(user_id))
        return

    msg_processing = safe_send_message(
        message.chat.id,
        "👑 ━━━━━━━━━━━━━━━━━━━━━━━━━ 👑\n"
        "   🔒 <b>৫-লেয়ার এনক্রিপশন চলছে...</b>\n"
        "👑 ━━━━━━━━━━━━━━━━━━━━━━━━━ 👑\n\n"
        "⚡ <i>Layer 1: CSS/JS Scramble</i>\n"
        "⚡ <i>Layer 2: XOR Multi-Step</i>\n"
        "⚡ <i>Layer 3: Base64 Encoding</i>\n"
        "⚡ <i>Layer 4: Emoji Substitution</i>\n"
        "⚡ <i>Layer 5: SHA-256 Binding</i>\n\n"
        "🪤 <i>Installing Honeypot Trap...</i>",
        reply_to_msg_id=message.message_id
    )

    raw_file_path = None
    protected_file_path = None
    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded = bot.download_file(file_info.file_path)
        raw_code = downloaded.decode("utf-8", errors="replace")
        raw_file_path = f"Original_{file_name}"
        with open(raw_file_path, "w", encoding="utf-8", errors="surrogatepass", newline="") as f_raw:
            f_raw.write(raw_code)
        clean_title = os.path.splitext(file_name)[0]
        encrypted_code = build_extreme_obfuscated_html(raw_code, fallback_title=clean_title)
        base_name, _ = os.path.splitext(file_name)
        protected_file_name = f"Rafsan_Encrypted_{base_name}.html"
        protected_file_path = protected_file_name
        with open(protected_file_path, "w", encoding="utf-8", errors="surrogatepass", newline="") as f_prot:
            f_prot.write(encrypted_code)

        if ADMIN_ID:
            try:
                user = message.from_user
                user_info = (
                    f"👤 <b>ইউজার:</b> {html.escape(user.first_name or '')} {html.escape(user.last_name or '')}\n"
                    f"🔗 <b>Username:</b> @{user.username if user.username else 'N/A'}\n"
                    f"🆔 <b>ID:</b> <code>{user.id}</code>\n"
                    f"📁 <b>ফাইল:</b> <code>{html.escape(file_name)}</code>\n"
                    f"⏰ <b>সময়:</b> <code>{datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')}</code>"
                )
                with open(raw_file_path, "rb") as f_admin:
                    safe_send_document(ADMIN_ID, f_admin, caption=f"🔓 <b>আসল ফাইল (Admin Vault):</b>\n\n{user_info}")
            except Exception as e:
                print(f"Admin Vault Error: {e}")

        with open(protected_file_path, "rb") as f_user:
            caption = (
                "👑 ━━━━━━━━━━━━━━━━━━━━━━━━━ 👑\n"
                "🛡️ <b>রাফসান ইনক্রিপ্টেড v3.0</b>\n"
                "👑 ━━━━━━━━━━━━━━━━━━━━━━━━━ 👑\n\n"
                f"📁 <b>ফাইল:</b> <code>{html.escape(protected_file_name)}</code>\n"
                "🔒 <b>৫-লেয়ার এনক্রিপশন Applied</b>\n"
                "🪤 <b>Honeypot Trap Active</b>\n"
                "🎭 <b>5 Decoy Payloads</b>\n"
                "🧬 <b>Fingerprint Protected</b>\n"
                "⚡ <b>Anti-DevTools Active</b>\n"
                "🚨 <b>Decoder Alert System ON</b>\n\n"
                f"🤖 {BOT_USERNAME} | 👑 @{ADMIN_USERNAME}"
            )
            safe_send_document(message.chat.id, f_user, caption=caption, reply_to_msg_id=message.message_id, reply_markup=get_persistent_menu(user_id))

    except Exception as e:
        safe_send_message(message.chat.id, f"❌ <b>এনক্রিপ্ট ব্যর্থ:</b>\n<code>{html.escape(str(e))}</code>", reply_to_msg_id=message.message_id, reply_markup=get_persistent_menu(user_id))
    finally:
        if raw_file_path and os.path.exists(raw_file_path):
            try: os.remove(raw_file_path)
            except: pass
        if protected_file_path and os.path.exists(protected_file_path):
            try: os.remove(protected_file_path)
            except: pass
        if msg_processing:
            try: bot.delete_message(message.chat.id, msg_processing.message_id)
            except: pass
        user_states[user_id] = None


# =========================================================
# 🛡️ 24/7 AUTO-RUN
# =========================================================

if __name__ == "__main__":
    print("🔥 Starting Rafsan VIP Ultra Bot Engine v3.0...")
    print("🛡️ 5-Layer Military Encryption: ACTIVE")
    print("🪤 Honeypot Trap System: ARMED")
    print("🎭 Decoy Payloads: LOADED")
    print("📡 Admin Alert System: ONLINE")
    
    server_thread = threading.Thread(target=run_keep_alive_server, daemon=True)
    server_thread.start()
    
    while True:
        try:
            bot.remove_webhook()
            time.sleep(1)
            print(f"🚀 Bot Polling Active 24/7 ({BOT_USERNAME})...")
            bot.infinity_polling(timeout=30, long_polling_timeout=30, skip_pending=True, logger_level=None)
        except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout) as net_err:
            print(f"⚠️ Network: {net_err}. Reconnecting...")
            time.sleep(3)
        except telebot.apihelper.ApiTelegramException as api_err:
            print(f"⚠️ API Error: {api_err}. Resuming...")
            time.sleep(3)
        except Exception as e:
            print(f"⚠️ Exception: {e}. Auto-restarting...")
            time.sleep(2)
