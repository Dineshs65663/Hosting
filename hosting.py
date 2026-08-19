import subprocess, sys, os, importlib

# ─── Auto-install dependencies ───────────────────────────────────────────────
_required = ["psutil", "flask", "requests", "pyTelegramBotAPI", "python-dotenv"]
for _pkg in _required:
    try:
        importlib.import_module(_pkg.replace("-", "_").split("==")[0])
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", _pkg, "-q"])

import telebot
import subprocess
import os
import zipfile
import tempfile
import shutil
import time
import math
import re
import json
import logging
import signal
import threading
import struct
import hashlib
import mimetypes
import sqlite3
import atexit
import requests
from datetime import datetime, timedelta
from telebot import types
from threading import Thread
from logging.handlers import RotatingFileHandler
from flask import Flask
from collections import defaultdict

# ════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION  — edit only this block
# ════════════════════════════════════════════════════════════════════════════
TOKEN        = '8976866439:AAF4escWUv5tppxjB4zGfMV7woksxi6IFHI'   # ← your token
OWNER_ID     = 8767249265
ADMIN_ID     = 8767249265
YOUR_USERNAME   = '@DKS65663'
UPDATE_CHANNEL  = 'https://t.me/dkschattinggroup'
FORCE_JOIN_CHANNELS = ["@dksinfo2266"]

# Limits
FREE_USER_LIMIT       = 10
SUBSCRIBED_USER_LIMIT = 20
ADMIN_LIMIT           = 999

# Cooldowns (seconds)
UPLOAD_COOLDOWN   = 15
CMD_COOLDOWN      = 5
APPROVE_TIMEOUT   = 3600   # 1 hour to approve/reject pending files

# Script execution
MAX_FILE_SIZE_MB  = 25
MAX_LOG_TAIL_KB   = 150
WATCHDOG_INTERVAL = 12     # seconds between watchdog checks
STREAM_INTERVAL   = 5      # seconds between log stream messages
STREAM_MAX_MSGS   = 12     # stop streaming after N messages

# ════════════════════════════════════════════════════════════════════════════
#  PATHS
# ════════════════════════════════════════════════════════════════════════════
BASE_DIR        = os.path.abspath(os.path.dirname(__file__))
UPLOAD_BOTS_DIR = os.path.join(BASE_DIR, 'upload_bots')
DATA_DIR        = os.path.join(BASE_DIR, 'data')
DATABASE_PATH   = os.path.join(DATA_DIR, 'bot_data.db')
LOG_FILE_PATH   = os.path.join(DATA_DIR, 'bot_main.log')

for _d in (UPLOAD_BOTS_DIR, DATA_DIR):
    os.makedirs(_d, exist_ok=True)

# ════════════════════════════════════════════════════════════════════════════
#  LOGGING
# ════════════════════════════════════════════════════════════════════════════
_log_fmt = '%(asctime)s [%(levelname)s] %(name)s — %(message)s'
logging.basicConfig(level=logging.INFO, format=_log_fmt)
logger = logging.getLogger('HostBot')
_fh = RotatingFileHandler(LOG_FILE_PATH, maxBytes=5*1024*1024, backupCount=3, encoding='utf-8')
_fh.setFormatter(logging.Formatter(_log_fmt))
logger.addHandler(_fh)

# ════════════════════════════════════════════════════════════════════════════
#  BOT INIT
# ════════════════════════════════════════════════════════════════════════════
bot = telebot.TeleBot(TOKEN, threaded=True, num_threads=8)

# ════════════════════════════════════════════════════════════════════════════
#  IN-MEMORY STATE
# ════════════════════════════════════════════════════════════════════════════
bot_scripts       = {}          # script_key → process info
user_subscriptions= {}          # user_id → {'expiry': datetime}
user_files        = {}          # user_id → [(file_name, file_type)]
active_users      = set()
admin_ids         = {ADMIN_ID, OWNER_ID}
pending_approvals = {}          # approval_id → approval_info
bot_locked        = False
_bot_lock_event   = threading.Lock()

# Rate limiting
_last_upload      = {}          # user_id → timestamp
_last_cmd         = {}          # user_id → timestamp

# Watchdog registry
_watchdog_threads = {}          # script_key → Thread
_watchdog_restarting = set()    # scripts being restarted

# Stream sessions
_stream_sessions  = {}          # user_id → {script_key, msg_count, active}

# ════════════════════════════════════════════════════════════════════════════
#  FLASK KEEP-ALIVE
# ════════════════════════════════════════════════════════════════════════════
_flask_app = Flask('')

@_flask_app.route('/')
def _home():
    return f"HostBot running — {len(bot_scripts)} scripts active."

def _run_flask():
    _flask_app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

def keep_alive():
    t = Thread(target=_run_flask, daemon=True)
    t.start()
    logger.info("Flask keep-alive started.")

# ════════════════════════════════════════════════════════════════════════════
#  DATABASE
# ════════════════════════════════════════════════════════════════════════════
_DB_LOCK = threading.Lock()

def _db():
    return sqlite3.connect(DATABASE_PATH, check_same_thread=False, timeout=10)

def init_db():
    with _DB_LOCK:
        conn = _db()
        c = conn.cursor()
        c.executescript("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id INTEGER PRIMARY KEY, expiry TEXT);
            CREATE TABLE IF NOT EXISTS user_files (
                user_id INTEGER, file_name TEXT, file_type TEXT,
                upload_time TEXT DEFAULT CURRENT_TIMESTAMP,
                run_count INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, file_name));
            CREATE TABLE IF NOT EXISTS active_users (
                user_id INTEGER PRIMARY KEY,
                first_seen TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS pending_approvals (
                approval_id TEXT PRIMARY KEY,
                user_id INTEGER, file_name TEXT, file_type TEXT,
                file_path TEXT, submitted_at TEXT, status TEXT DEFAULT 'pending');
            CREATE TABLE IF NOT EXISTS user_stats (
                user_id INTEGER PRIMARY KEY,
                total_uploads INTEGER DEFAULT 0,
                total_runs INTEGER DEFAULT 0,
                last_active TEXT);
        """)
        c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (OWNER_ID,))
        if ADMIN_ID != OWNER_ID:
            c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (ADMIN_ID,))
        conn.commit()
        conn.close()
        logger.info("DB initialised.")

def load_data():
    with _DB_LOCK:
        conn = _db()
        c = conn.cursor()
        for uid, expiry in c.execute('SELECT user_id, expiry FROM subscriptions'):
            try:
                user_subscriptions[uid] = {'expiry': datetime.fromisoformat(expiry)}
            except ValueError:
                pass
        for uid, fn, ft in c.execute('SELECT user_id, file_name, file_type FROM user_files'):
            user_files.setdefault(uid, [])
            if not any(f[0] == fn for f in user_files[uid]):
                user_files[uid].append((fn, ft))
        for (uid,) in c.execute('SELECT user_id FROM active_users'):
            active_users.add(uid)
        for (uid,) in c.execute('SELECT user_id FROM admins'):
            admin_ids.add(uid)
        for row in c.execute("SELECT approval_id, user_id, file_name, file_type, file_path, submitted_at FROM pending_approvals WHERE status='pending'"):
            aid, uid, fn, ft, fp, sat = row
            pending_approvals[aid] = {
                'user_id': uid, 'file_name': fn, 'file_type': ft,
                'file_path': fp, 'submitted_at': sat, 'status': 'pending'
            }
        conn.close()
    logger.info(f"Loaded: {len(active_users)} users, {len(user_subscriptions)} subs, {len(admin_ids)} admins, {len(pending_approvals)} pending.")

def _expire_stale_approvals():
    """Expire pending approvals older than APPROVE_TIMEOUT"""
    now = datetime.now().isoformat()
    with _DB_LOCK:
        conn = _db()
        c = conn.cursor()
        c.execute(
            "SELECT approval_id, user_id, file_name FROM pending_approvals "
            "WHERE status='pending' AND "
            "julianday('now') - julianday(submitted_at) > ?",
            (APPROVE_TIMEOUT / 86400,)
        )
        expired = c.fetchall()
        for aid, uid, fn in expired:
            c.execute("UPDATE pending_approvals SET status='expired' WHERE approval_id=?", (aid,))
            for admin_uid in admin_ids:
                try:
                    bot.send_message(admin_uid,
                                     f"⏰ Approval <code>{aid}</code> for <code>{uid}</code> ({fn}) EXPIRED.",
                                     parse_mode='HTML')
                except: pass
            try:
                bot.send_message(uid,
                                 f"⏰ Your file <b>{fn}</b> approval expired (1hr timeout). Re-upload if needed.",
                                 parse_mode='HTML')
            except: pass
            info = pending_approvals.get(aid)
            if info and os.path.exists(info.get('file_path', '')):
                try: os.remove(info['file_path'])
                except: pass
            pending_approvals.pop(aid, None)
        conn.commit()
        conn.close()

# ─── DB helpers ──────────────────────────────────────────────────────────────

def db_save_file(uid, fn, ft):
    with _DB_LOCK:
        conn = _db()
        conn.execute('INSERT OR REPLACE INTO user_files (user_id, file_name, file_type) VALUES (?,?,?)', (uid, fn, ft))
        conn.execute('INSERT OR IGNORE INTO user_stats (user_id) VALUES (?)', (uid,))
        conn.execute('UPDATE user_stats SET total_uploads=total_uploads+1, last_active=? WHERE user_id=?',
                     (datetime.now().isoformat(), uid))
        conn.commit(); conn.close()
    user_files.setdefault(uid, [])
    user_files[uid] = [(n, t) for n, t in user_files[uid] if n != fn]
    user_files[uid].append((fn, ft))

def db_remove_file(uid, fn):
    with _DB_LOCK:
        conn = _db()
        conn.execute('DELETE FROM user_files WHERE user_id=? AND file_name=?', (uid, fn))
        conn.commit(); conn.close()
    if uid in user_files:
        user_files[uid] = [(n, t) for n, t in user_files[uid] if n != fn]

def db_add_user(uid):
    active_users.add(uid)
    with _DB_LOCK:
        conn = _db()
        conn.execute('INSERT OR IGNORE INTO active_users (user_id) VALUES (?)', (uid,))
        conn.execute('INSERT OR IGNORE INTO user_stats (user_id) VALUES (?)', (uid,))
        conn.execute('UPDATE user_stats SET last_active=? WHERE user_id=?',
                     (datetime.now().isoformat(), uid))
        conn.commit(); conn.close()

def db_save_sub(uid, expiry):
    with _DB_LOCK:
        conn = _db()
        conn.execute('INSERT OR REPLACE INTO subscriptions (user_id, expiry) VALUES (?,?)',
                     (uid, expiry.isoformat()))
        conn.commit(); conn.close()
    user_subscriptions[uid] = {'expiry': expiry}

def db_remove_sub(uid):
    with _DB_LOCK:
        conn = _db()
        conn.execute('DELETE FROM subscriptions WHERE user_id=?', (uid,))
        conn.commit(); conn.close()
    user_subscriptions.pop(uid, None)

def db_add_admin(uid):
    with _DB_LOCK:
        conn = _db()
        conn.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (uid,))
        conn.commit(); conn.close()
    admin_ids.add(uid)

def db_remove_admin(uid):
    if uid == OWNER_ID:
        return False
    with _DB_LOCK:
        conn = _db()
        conn.execute('DELETE FROM admins WHERE user_id=?', (uid,))
        conn.commit(); conn.close()
    admin_ids.discard(uid)
    return True

def db_save_approval(aid, uid, fn, ft, fp):
    with _DB_LOCK:
        conn = _db()
        conn.execute(
            'INSERT OR REPLACE INTO pending_approvals (approval_id,user_id,file_name,file_type,file_path,submitted_at) VALUES (?,?,?,?,?,?)',
            (aid, uid, fn, ft, fp, datetime.now().isoformat()))
        conn.commit(); conn.close()

def db_update_approval(aid, status):
    with _DB_LOCK:
        conn = _db()
        conn.execute('UPDATE pending_approvals SET status=? WHERE approval_id=?', (status, aid))
        conn.commit(); conn.close()

def db_get_stats(uid):
    with _DB_LOCK:
        conn = _db()
        row = conn.execute('SELECT total_uploads, total_runs, last_active FROM user_stats WHERE user_id=?', (uid,)).fetchone()
        conn.close()
    return row or (0, 0, 'Never')

def db_increment_runs(uid, fn):
    with _DB_LOCK:
        conn = _db()
        conn.execute('UPDATE user_files SET run_count=run_count+1 WHERE user_id=? AND file_name=?', (uid, fn))
        conn.execute('UPDATE user_stats SET total_runs=total_runs+1, last_active=? WHERE user_id=?',
                     (datetime.now().isoformat(), uid))
        conn.commit(); conn.close()

# ════════════════════════════════════════════════════════════════════════════
#  SECURITY — entropy + signature scanner
# ════════════════════════════════════════════════════════════════════════════
_DANGEROUS_EXTENSIONS = {
    '.exe', '.dll', '.bat', '.cmd', '.scr', '.com', '.pif',
    '.msi', '.msp', '.hta', '.cpl', '.msc', '.bin',
    '.apk', '.dmg', '.iso', '.img',
}

_BINARY_SIGNATURES = [
    (b'MZ',           'Windows PE executable'),
    (b'\x7fELF',      'Linux ELF executable'),
    (b'\xfe\xed\xfa', 'Mach-O binary'),
    (b'\xce\xfa\xed\xfe', 'Mach-O binary (LE)'),
]

def _entropy(data: bytes) -> float:
    if not data:
        return 0.0
    freq = defaultdict(int)
    for b in data:
        freq[b] += 1
    total = len(data)
    return -sum((f / total) * math.log2(f / total) for f in freq.values())

def scan_file(content: bytes, filename: str, uid: int) -> tuple[bool, str]:
    """Returns (is_safe, reason). Owner always passes."""
    if uid == OWNER_ID:
        return True, "Owner bypass"
    ext = os.path.splitext(filename)[1].lower()
    if ext in _DANGEROUS_EXTENSIONS:
        return False, f"Blocked extension: {ext}"
    for sig, label in _BINARY_SIGNATURES:
        if content.startswith(sig):
            return False, f"Binary detected: {label}"
    sample = content[:8192]
    ent = _entropy(sample)
    if ent > 7.5 and ext not in ('.zip',):
        return False, f"Suspicious entropy ({ent:.2f}) — possible packed/encrypted payload"
    return True, "OK"

# ════════════════════════════════════════════════════════════════════════════
#  RATE LIMITING
# ════════════════════════════════════════════════════════════════════════════
def check_rate(store: dict, uid: int, cooldown: int) -> bool:
    now = time.time()
    if now - store.get(uid, 0) < cooldown:
        return False
    store[uid] = now
    return True

# ════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ════════════════════════════════════════════════════════════════════════════
def user_folder(uid) -> str:
    p = os.path.join(UPLOAD_BOTS_DIR, str(uid))
    os.makedirs(p, exist_ok=True)
    return p

def file_limit(uid) -> float:
    if uid == OWNER_ID:       return float('inf')
    if uid in admin_ids:      return ADMIN_LIMIT
    sub = user_subscriptions.get(uid)
    if sub and sub['expiry'] > datetime.now():
        return SUBSCRIBED_USER_LIMIT
    return FREE_USER_LIMIT

def file_count(uid) -> int:
    return len(user_files.get(uid, []))

def user_status_str(uid) -> str:
    if uid == OWNER_ID:     return "👑 Owner"
    if uid in admin_ids:    return "🛡️ Admin"
    sub = user_subscriptions.get(uid)
    if sub:
        if sub['expiry'] > datetime.now():
            days = (sub['expiry'] - datetime.now()).days
            return f"⭐ Premium ({days}d left)"
        return "🆓 Free (sub expired)"
    return "🆓 Free"

def is_running(uid, fn) -> bool:
    key = f"{uid}_{fn}"
    info = bot_scripts.get(key)
    if not info:
        return False
    try:
        import psutil
        proc = psutil.Process(info['process'].pid)
        alive = proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
        if not alive:
            _cleanup_key(key)
        return alive
    except Exception:
        _cleanup_key(key)
        return False

def _cleanup_key(key):
    info = bot_scripts.pop(key, None)
    if info:
        lf = info.get('log_file')
        if lf and not lf.closed:
            try: lf.close()
            except: pass

def kill_tree(info: dict):
    import psutil
    lf = info.get('log_file')
    if lf and not lf.closed:
        try: lf.close()
        except: pass
    proc = info.get('process')
    if not proc:
        return
    try:
        parent = psutil.Process(proc.pid)
        children = parent.children(recursive=True)
        for ch in children:
            try: ch.terminate()
            except: pass
        psutil.wait_procs(children, timeout=2)
        for ch in children:
            try: ch.kill()
            except: pass
        try: parent.terminate(); parent.wait(timeout=2)
        except psutil.TimeoutExpired: parent.kill()
        except psutil.NoSuchProcess: pass
    except psutil.NoSuchProcess:
        pass

# ════════════════════════════════════════════════════════════════════════════
#  PACKAGE MAPPING
# ════════════════════════════════════════════════════════════════════════════
TELEGRAM_MODULES = {
    'telebot': 'pyTelegramBotAPI', 'telegram': 'python-telegram-bot',
    'aiogram': 'aiogram', 'pyrogram': 'pyrogram', 'telethon': 'telethon',
    'bs4': 'beautifulsoup4', 'requests': 'requests', 'pillow': 'Pillow',
    'cv2': 'opencv-python', 'yaml': 'PyYAML', 'dotenv': 'python-dotenv',
    'pandas': 'pandas', 'numpy': 'numpy', 'flask': 'Flask',
    'django': 'Django', 'sqlalchemy': 'SQLAlchemy', 'psutil': 'psutil',
    'aiohttp': 'aiohttp', 'httpx': 'httpx', 'pydantic': 'pydantic',
    **{m: None for m in ['asyncio','json','datetime','os','sys','re','time',
                          'math','random','logging','threading','subprocess',
                          'zipfile','tempfile','shutil','sqlite3','atexit',
                          'struct','hashlib','mimetypes','collections']}
}

def pip_install(module, message_obj):
    pkg = TELEGRAM_MODULES.get(module.lower(), module)
    if pkg is None:
        return False
    try:
        bot.reply_to(message_obj, f"📦 Installing `{pkg}`…", parse_mode='HTML')
        r = subprocess.run([sys.executable, '-m', 'pip', 'install', pkg, '-q'],
                           capture_output=True, text=True)
        if r.returncode == 0:
            bot.reply_to(message_obj, f"✅ <code>{pkg}</code> installed.", parse_mode='HTML')
            return True
        bot.reply_to(message_obj, f"❌ Install failed:\n<pre>{(r.stderr or r.stdout)[:800]}</pre>", parse_mode='HTML')
        return False
    except Exception as e:
        bot.reply_to(message_obj, f"❌ Install error: {e}")
        return False

def npm_install(module, folder, message_obj):
    try:
        bot.reply_to(message_obj, f"📦 npm installing `{module}`…", parse_mode='HTML')
        r = subprocess.run(['npm', 'install', module], capture_output=True, text=True, cwd=folder)
        if r.returncode == 0:
            bot.reply_to(message_obj, f"✅ <code>{module}</code> installed.", parse_mode='HTML')
            return True
        bot.reply_to(message_obj, f"❌ npm failed:\n<pre>{(r.stderr or r.stdout)[:800]}</pre>", parse_mode='HTML')
        return False
    except FileNotFoundError:
        bot.reply_to(message_obj, "❌ `node`/`npm` not found."); return False
    except Exception as e:
        bot.reply_to(message_obj, f"❌ npm error: {e}"); return False

# ════════════════════════════════════════════════════════════════════════════
#  SCRIPT RUNNER  (Python & JS)
# ════════════════════════════════════════════════════════════════════════════
def _launch_process(cmd, script_key, uid, fn, folder, ft, reply_msg, attempt=1):
    MAX = 2
    if attempt > MAX:
        bot.reply_to(reply_msg, f"❌ Failed after {MAX} attempts: <code>{fn}</code>", parse_mode='HTML')
        return

    try:
        cp = subprocess.Popen(cmd, cwd=folder, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')
        try:
            out, err = cp.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            cp.kill(); cp.communicate()
            err = ""
        if cp.returncode and cp.returncode != 0 and err:
            py_miss = re.search(r"ModuleNotFoundError: No module named '(.+?)'", err)
            js_miss = re.search(r"Cannot find module '(.+?)'", err)
            if py_miss:
                mod = py_miss.group(1)
                if pip_install(mod, reply_msg):
                    time.sleep(1)
                    Thread(target=_launch_process,
                           args=(cmd, script_key, uid, fn, folder, ft, reply_msg, attempt+1),
                           daemon=True).start()
                    return
                bot.reply_to(reply_msg, f"❌ Cannot install <code>{mod}</code>. Fix script.", parse_mode='HTML')
                return
            if js_miss:
                mod = js_miss.group(1)
                if not mod.startswith(('.','/')):
                    if npm_install(mod, folder, reply_msg):
                        time.sleep(1)
                        Thread(target=_launch_process,
                               args=(cmd, script_key, uid, fn, folder, ft, reply_msg, attempt+1),
                               daemon=True).start()
                        return
                    bot.reply_to(reply_msg, f"❌ Cannot install node module <code>{mod}</code>.", parse_mode='HTML')
                    return
            if cp.returncode != 0:
                bot.reply_to(reply_msg,
                             f"❌ Script error:\n<pre>{err[:1000]}</pre>", parse_mode='HTML')
                return
    except Exception as e:
        logger.error(f"Pre-check error {script_key}: {e}")

    log_path = os.path.join(folder, os.path.splitext(fn)[0] + '.log')
    try:
        lf = open(log_path, 'w', encoding='utf-8', errors='ignore')
    except Exception as e:
        bot.reply_to(reply_msg, f"❌ Cannot open log file: {e}"); return

    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE

    try:
        proc = subprocess.Popen(cmd, cwd=folder, stdout=lf, stderr=lf,
                                stdin=subprocess.PIPE, startupinfo=startupinfo,
                                encoding='utf-8', errors='ignore')
    except Exception as e:
        lf.close()
        bot.reply_to(reply_msg, f"❌ Launch failed: {e}"); return

    bot_scripts[script_key] = {
        'process': proc, 'log_file': lf, 'file_name': fn,
        'chat_id': reply_msg.chat.id, 'script_owner_id': uid,
        'start_time': datetime.now(), 'user_folder': folder,
        'type': ft, 'script_key': script_key, 'log_path': log_path
    }
    db_increment_runs(uid, fn)
    bot.reply_to(reply_msg,
                 f"✅ <b>{fn}</b> started (PID <code>{proc.pid}</code>)", parse_mode='HTML')

    _start_watchdog(script_key, uid, fn, folder, ft, reply_msg)

def run_py(path, uid, folder, fn, reply_msg, attempt=1):
    _launch_process([sys.executable, path], f"{uid}_{fn}", uid, fn, folder, 'py', reply_msg, attempt)

def run_js(path, uid, folder, fn, reply_msg, attempt=1):
    _launch_process(['node', path], f"{uid}_{fn}", uid, fn, folder, 'js', reply_msg, attempt)

# ════════════════════════════════════════════════════════════════════════════
#  WATCHDOG
# ════════════════════════════════════════════════════════════════════════════
def _start_watchdog(script_key, uid, fn, folder, ft, reply_msg):
    if script_key in _watchdog_threads and _watchdog_threads[script_key].is_alive():
        return
    t = Thread(target=_watchdog_loop,
               args=(script_key, uid, fn, folder, ft, reply_msg), daemon=True)
    _watchdog_threads[script_key] = t
    t.start()

def _watchdog_loop(script_key, uid, fn, folder, ft, reply_msg):
    time.sleep(WATCHDOG_INTERVAL * 2)
    while script_key in bot_scripts:
        time.sleep(WATCHDOG_INTERVAL)
        if script_key not in bot_scripts:
            break
        if script_key in _watchdog_restarting:
            continue
        if not is_running(uid, fn):
            logger.warning(f"Watchdog: {script_key} died — restarting")
            _watchdog_restarting.add(script_key)
            try:
                bot.send_message(reply_msg.chat.id,
                                 f"🔄 <b>{fn}</b> crashed — auto-restarting…", parse_mode='HTML')
            except: pass
            path = os.path.join(folder, fn)
            if os.path.exists(path):
                if ft == 'py':
                    Thread(target=run_py, args=(path, uid, folder, fn, reply_msg), daemon=True).start()
                else:
                    Thread(target=run_js, args=(path, uid, folder, fn, reply_msg), daemon=True).start()
            _watchdog_restarting.discard(script_key)
            break
    _watchdog_threads.pop(script_key, None)
    _watchdog_restarting.discard(script_key)

# ════════════════════════════════════════════════════════════════════════════
#  APPROVAL SYSTEM
# ════════════════════════════════════════════════════════════════════════════
def submit_for_approval(uid, fn, ft, file_path, user_name, reply_msg):
    aid = hashlib.md5(f"{uid}{fn}{time.time()}".encode()).hexdigest()[:12]
    pending_approvals[aid] = {
        'user_id': uid, 'file_name': fn, 'file_type': ft,
        'file_path': file_path, 'submitted_at': datetime.now().isoformat(),
        'status': 'pending', 'reply_chat': reply_msg.chat.id,
        'reply_msg_id': reply_msg.message_id
    }
    db_save_approval(aid, uid, fn, ft, file_path)

    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("✅ Approve", callback_data=f"appr_ok_{aid}"),
        types.InlineKeyboardButton("❌ Reject",  callback_data=f"appr_no_{aid}")
    )
    markup.add(types.InlineKeyboardButton("👤 View User Info", callback_data=f"appr_info_{aid}"))

    notif = (f"🔔 <b>New File Pending Approval</b>\n\n"
             f"👤 User: <code>{uid}</code> (@{user_name or 'unknown'})\n"
             f"📄 File: <code>{fn}</code> ({ft})\n"
             f"🕐 At: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
             f"🆔 Approval ID: <code>{aid}</code>")

    for admin_uid in admin_ids:
        try:
            bot.send_message(admin_uid, notif, reply_markup=markup, parse_mode='HTML')
        except: pass

    bot.reply_to(reply_msg,
                 f"⏳ <b>{fn}</b> submitted for admin approval.\nYou'll be notified once reviewed.",
                 parse_mode='HTML')
    logger.info(f"Approval {aid} submitted: uid={uid}, fn={fn}")

# ════════════════════════════════════════════════════════════════════════════
#  FORCE JOIN
# ════════════════════════════════════════════════════════════════════════════
def is_joined_all(uid) -> bool:
    for ch in FORCE_JOIN_CHANNELS:
        try:
            m = bot.get_chat_member(ch, uid)
            if m.status not in ('member', 'administrator', 'creator'):
                return False
        except:
            return False
    return True

def send_force_join(chat_id):
    markup = types.InlineKeyboardMarkup()
    for ch in FORCE_JOIN_CHANNELS:
        markup.add(types.InlineKeyboardButton(
            f"📢 Join {ch.replace('@','')}", url=f"https://t.me/{ch.replace('@','')}"))
    markup.add(types.InlineKeyboardButton("✅ I Joined", callback_data="force_join_check"))
    bot.send_message(chat_id, "⛔ Join all channels first:", reply_markup=markup)

# ════════════════════════════════════════════════════════════════════════════
#  MENUS
# ════════════════════════════════════════════════════════════════════════════
def main_inline(uid):
    mk = types.InlineKeyboardMarkup(row_width=2)
    mk.add(types.InlineKeyboardButton('📢 Channel', url=UPDATE_CHANNEL))
    mk.add(types.InlineKeyboardButton('📤 Upload', callback_data='upload'),
           types.InlineKeyboardButton('📂 My Files', callback_data='check_files'))
    mk.add(types.InlineKeyboardButton('⚡ Ping', callback_data='speed'),
           types.InlineKeyboardButton('📊 Stats', callback_data='stats'))
    mk.add(types.InlineKeyboardButton('📜 Stream Logs', callback_data='stream_menu'),
           types.InlineKeyboardButton('📤 Send CMD', callback_data='send_command'))
    mk.add(types.InlineKeyboardButton('📞 Contact Owner',
           url=f"https://t.me/{YOUR_USERNAME.replace('@','')}"))
    if uid in admin_ids:
        mk.add(types.InlineKeyboardButton('💳 Subscriptions', callback_data='subscription'),
               types.InlineKeyboardButton('📢 Broadcast', callback_data='broadcast'))
        mk.add(types.InlineKeyboardButton('🔒 Lock/Unlock', callback_data='toggle_lock'),
               types.InlineKeyboardButton('🟢 Run All', callback_data='run_all_scripts'))
        mk.add(types.InlineKeyboardButton('⏳ Approvals', callback_data='list_approvals'),
               types.InlineKeyboardButton('👑 Admin Panel', callback_data='admin_panel'))
    return mk

def control_buttons(uid, fn, running=True):
    mk = types.InlineKeyboardMarkup(row_width=2)
    if running:
        mk.row(types.InlineKeyboardButton("🔴 Stop",    callback_data=f'stop_{uid}_{fn}'),
               types.InlineKeyboardButton("🔄 Restart", callback_data=f'restart_{uid}_{fn}'))
        mk.row(types.InlineKeyboardButton("📜 Stream",  callback_data=f'stream_{uid}_{fn}'),
               types.InlineKeyboardButton("📋 Logs",    callback_data=f'logs_{uid}_{fn}'))
        mk.add(types.InlineKeyboardButton("🗑️ Delete",  callback_data=f'delete_{uid}_{fn}'))
    else:
        mk.row(types.InlineKeyboardButton("🟢 Start",   callback_data=f'start_{uid}_{fn}'),
               types.InlineKeyboardButton("📋 Logs",    callback_data=f'logs_{uid}_{fn}'))
        mk.add(types.InlineKeyboardButton("🗑️ Delete",  callback_data=f'delete_{uid}_{fn}'))
    mk.add(types.InlineKeyboardButton("🔙 Back", callback_data='check_files'))
    return mk

# ════════════════════════════════════════════════════════════════════════════
#  WELCOME
# ════════════════════════════════════════════════════════════════════════════
def send_welcome(message):
    uid  = message.from_user.id
    name = message.from_user.first_name
    uname= message.from_user.username

    if uid not in admin_ids and not is_joined_all(uid):
        send_force_join(message.chat.id); return
    if bot_locked and uid not in admin_ids:
        bot.send_message(message.chat.id, "🔒 Bot locked by admin."); return

    if uid not in active_users:
        db_add_user(uid)
        _notify_owner_new_user(uid, name, uname, message)

    lim = file_limit(uid)
    cnt = file_count(uid)
    lim_str = str(int(lim)) if lim != float('inf') else "∞"

    text = (f"👋 Welcome, <b>{name}</b>!\n\n"
            f"🆔 <code>{uid}</code>  |  @{uname or 'no username'}\n"
            f"🔰 {user_status_str(uid)}\n"
            f"📁 Files: <b>{cnt}/{lim_str}</b>\n\n"
            f"Upload <code>.py</code>, <code>.js</code>, or <code>.zip</code> to host & run scripts.\n"
            f"All files require admin approval before execution.")
    bot.send_message(message.chat.id, text, reply_markup=main_inline(uid), parse_mode='HTML')

def _notify_owner_new_user(uid, name, uname, message):
    try:
        txt = (f"🆕 New user!\n👤 {name}  @{uname or 'N/A'}\n"
               f"🆔 <code>{uid}</code>")
        bot.send_message(OWNER_ID, txt, parse_mode='HTML')
    except: pass

# ════════════════════════════════════════════════════════════════════════════
#  FILE UPLOAD HANDLER
# ════════════════════════════════════════════════════════════════════════════
@bot.message_handler(content_types=['document'])
def handle_upload(message):
    uid   = message.from_user.id
    uname = message.from_user.username
    doc   = message.document

    if uid not in admin_ids and not is_joined_all(uid):
        send_force_join(message.chat.id); return
    if bot_locked and uid not in admin_ids:
        bot.reply_to(message, "🔒 Bot locked."); return
    if not check_rate(_last_upload, uid, UPLOAD_COOLDOWN):
        bot.reply_to(message, f"⏳ Wait {UPLOAD_COOLDOWN}s between uploads."); return

    fn  = doc.file_name or ''
    ext = os.path.splitext(fn)[1].lower()
    if ext not in ('.py', '.js', '.zip'):
        bot.reply_to(message, "⚠️ Only <code>.py</code>, <code>.js</code>, <code>.zip</code> allowed.",
                     parse_mode='HTML'); return

    if doc.file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
        bot.reply_to(message, f"⚠️ Max file size is {MAX_FILE_SIZE_MB}MB."); return

    lim = file_limit(uid)
    if file_count(uid) >= lim:
        bot.reply_to(message, f"⚠️ File limit reached ({int(lim)}). Delete files first."); return

    try:
        fi    = bot.get_file(doc.file_id)
        data  = bot.download_file(fi.file_path)
    except Exception as e:
        bot.reply_to(message, f"❌ Download failed: {e}"); return

    safe, reason = scan_file(data, fn, uid)
    if not safe:
        bot.reply_to(message, f"🚨 <b>Security blocked:</b> {reason}", parse_mode='HTML')
        try:
            bot.send_message(OWNER_ID,
                             f"🚨 Blocked upload from <code>{uid}</code> (@{uname})\n"
                             f"File: <code>{fn}</code>\nReason: {reason}", parse_mode='HTML')
        except: pass
        return

    try:
        bot.send_message(OWNER_ID,
                         f"📤 Upload from <code>{uid}</code> (@{uname})\n"
                         f"File: <code>{fn}</code>  ({doc.file_size//1024}KB)", parse_mode='HTML')
    except: pass

    uf = user_folder(uid)

    if ext == '.zip':
        _handle_zip(data, fn, uid, uname, uf, message)
    else:
        fp = os.path.join(uf, fn)
        with open(fp, 'wb') as f:
            f.write(data)
        ft = ext.lstrip('.')
        if uid in admin_ids:
            db_save_file(uid, fn, ft)
            bot.reply_to(message, f"✅ <b>{fn}</b> saved. Starting…", parse_mode='HTML')
            _run_file(fp, uid, uf, fn, ft, message)
        else:
            submit_for_approval(uid, fn, ft, fp, uname, message)

def _handle_zip(data, zip_name, uid, uname, uf, message):
    tmp = tempfile.mkdtemp(prefix=f"zip_{uid}_")
    try:
        zp = os.path.join(tmp, zip_name)
        with open(zp, 'wb') as f:
            f.write(data)
        with zipfile.ZipFile(zp, 'r') as zf:
            for m in zf.infolist():
                mp = os.path.abspath(os.path.join(tmp, m.filename))
                if not mp.startswith(os.path.abspath(tmp)):
                    bot.reply_to(message, "🚨 Zip path traversal blocked."); return
            zf.extractall(tmp)

        target = tmp
        for root, dirs, files in os.walk(tmp):
            dirs[:] = [d for d in dirs if not d.startswith(('.','__'))]
            if any(f.endswith(('.py','.js')) for f in files):
                target = root; break

        if target != tmp:
            for item in os.listdir(target):
                s = os.path.join(target, item)
                d = os.path.join(tmp, item)
                if os.path.exists(d):
                    shutil.rmtree(d) if os.path.isdir(d) else os.remove(d)
                shutil.move(s, d)

        items = os.listdir(tmp)
        py_f  = [x for x in items if x.endswith('.py')]
        js_f  = [x for x in items if x.endswith('.js')]

        main = None; ft = None
        for pref in ['main.py','bot.py','app.py']:
            if pref in py_f: main = pref; ft = 'py'; break
        if not main:
            for pref in ['index.js','main.js','bot.js','app.js']:
                if pref in js_f: main = pref; ft = 'js'; break
        if not main:
            if py_f: main = py_f[0]; ft = 'py'
            elif js_f: main = js_f[0]; ft = 'js'
        if not main:
            bot.reply_to(message, "❌ No .py or .js found in zip."); return

        if 'requirements.txt' in items:
            r = subprocess.run([sys.executable,'-m','pip','install','-r',
                                os.path.join(tmp,'requirements.txt'),'-q'],
                               capture_output=True)
            if r.returncode != 0:
                bot.reply_to(message, f"❌ pip install failed:\n<pre>{r.stderr[:600]}</pre>",
                             parse_mode='HTML'); return

        if 'package.json' in items:
            r = subprocess.run(['npm','install'], capture_output=True, cwd=tmp)
            if r.returncode != 0:
                bot.reply_to(message, f"❌ npm install failed:\n<pre>{r.stderr[:600]}</pre>",
                             parse_mode='HTML'); return

        for item in items:
            if item == zip_name: continue
            s = os.path.join(tmp, item); d = os.path.join(uf, item)
            if os.path.exists(d):
                shutil.rmtree(d) if os.path.isdir(d) else os.remove(d)
            shutil.move(s, d)

        fp = os.path.join(uf, main)
        if uid in admin_ids:
            db_save_file(uid, main, ft)
            bot.reply_to(message, f"✅ Zip extracted. Starting <b>{main}</b>…", parse_mode='HTML')
            _run_file(fp, uid, uf, main, ft, message)
        else:
            submit_for_approval(uid, main, ft, fp, uname, message)
    except zipfile.BadZipFile:
        bot.reply_to(message, "❌ Invalid zip file.")
    except Exception as e:
        logger.error(f"Zip error uid={uid}: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Zip error: {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def _run_file(fp, uid, folder, fn, ft, reply_msg):
    if ft == 'py':
        Thread(target=run_py, args=(fp, uid, folder, fn, reply_msg), daemon=True).start()
    elif ft == 'js':
        Thread(target=run_js, args=(fp, uid, folder, fn, reply_msg), daemon=True).start()

# ════════════════════════════════════════════════════════════════════════════
#  LOG STREAMING
# ════════════════════════════════════════════════════════════════════════════
def start_log_stream(uid, fn, owner_uid, chat_id):
    key = f"{owner_uid}_{fn}"
    if key not in bot_scripts:
        bot.send_message(chat_id, "❌ Script not running."); return

    if uid in _stream_sessions and _stream_sessions[uid].get('active'):
        bot.send_message(chat_id, "⚠️ Already streaming. Use /stopstream first.")
        return

    _stream_sessions[uid] = {'script_key': key, 'msg_count': 0, 'active': True}
    bot.send_message(chat_id, f"📡 Streaming logs for <b>{fn}</b>…\n/stopstream to stop.", parse_mode='HTML')
    Thread(target=_stream_loop, args=(uid, key, fn, chat_id), daemon=True).start()

def _stream_loop(uid, key, fn, chat_id):
    info = bot_scripts.get(key)
    if not info: return
    log_path = info.get('log_path', '')
    if not os.path.exists(log_path):
        bot.send_message(chat_id, "❌ Log file not found."); return

    pos = os.path.getsize(log_path)
    count = 0
    while _stream_sessions.get(uid, {}).get('active') and count < STREAM_MAX_MSGS:
        time.sleep(STREAM_INTERVAL)
        if key not in bot_scripts:
            bot.send_message(chat_id, f"⚠️ <b>{fn}</b> stopped.", parse_mode='HTML')
            break
        try:
            with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                f.seek(pos)
                chunk = f.read(3000)
                pos = f.tell()
            if chunk.strip():
                bot.send_message(chat_id, f"<pre>{chunk[-3000:]}</pre>", parse_mode='HTML')
                count += 1
                if uid in _stream_sessions:
                    _stream_sessions[uid]['msg_count'] = count
        except Exception as e:
            logger.error(f"Stream error: {e}"); break

    if uid in _stream_sessions:
        _stream_sessions[uid]['active'] = False
        Thread(target=lambda: (time.sleep(5), _stream_sessions.pop(uid, None)), daemon=True).start()
    if count >= STREAM_MAX_MSGS:
        bot.send_message(chat_id, f"🔚 Stream ended (limit {STREAM_MAX_MSGS} msgs).")

# ════════════════════════════════════════════════════════════════════════════
#  COMMANDS
# ════════════════════════════════════════════════════════════════════════════
@bot.message_handler(commands=['start','help'])
def cmd_start(m): send_welcome(m)

@bot.message_handler(commands=['myid'])
def cmd_myid(m):
    bot.reply_to(m, f"🆔 Your ID: <code>{m.from_user.id}</code>", parse_mode='HTML')

@bot.message_handler(commands=['stopstream'])
def cmd_stopstream(m):
    uid = m.from_user.id
    if uid in _stream_sessions:
        _stream_sessions[uid]['active'] = False
        bot.reply_to(m, "🔴 Stream stopped.")
    else:
        bot.reply_to(m, "No active stream.")

@bot.message_handler(commands=['users'])
def cmd_users(m):
    if m.from_user.id not in admin_ids:
        bot.reply_to(m, "Admins only."); return
    total = len(active_users)
    active_scripts = sum(1 for k in list(bot_scripts.keys())
                         if is_running(int(k.split('_')[0]), k.split('_',1)[1]))
    bot.reply_to(m,
                 f"👥 <b>User stats</b>\n"
                 f"Total users: {total}\n"
                 f"Running scripts: {active_scripts}\n"
                 f"Pending approvals: {len(pending_approvals)}",
                 parse_mode='HTML')

@bot.message_handler(commands=['ping'])
def cmd_ping(m):
    t = time.time()
    msg = bot.reply_to(m, "Pong…")
    bot.edit_message_text(f"🏓 Pong! <code>{round((time.time()-t)*1000,1)}ms</code>",
                          m.chat.id, msg.message_id, parse_mode='HTML')

@bot.message_handler(commands=['stats'])
def cmd_stats(m):
    _send_stats(m.from_user.id, m.chat.id)

@bot.message_handler(commands=['files'])
def cmd_files(m):
    _send_files(m.from_user.id, m.chat.id)

@bot.message_handler(commands=['cancel'])
def cmd_cancel(m):
    bot.reply_to(m, "Action cancelled.")
    bot.clear_step_handler_by_chat_id(m.chat.id)

# ════════════════════════════════════════════════════════════════════════════
#  CALLBACK ROUTER
# ════════════════════════════════════════════════════════════════════════════
@bot.callback_query_handler(func=lambda c: c.data == 'force_join_check')
def cb_force_join(c):
    if is_joined_all(c.from_user.id):
        bot.answer_callback_query(c.id, "✅ Verified!")
        send_welcome(c.message)
    else:
        bot.answer_callback_query(c.id, "❌ Not joined yet.", show_alert=True)

@bot.callback_query_handler(func=lambda c: True)
def cb_router(c):
    uid  = c.from_user.id
    data = c.data

    if bot_locked and uid not in admin_ids and data not in ('speed','stats','back_main'):
        bot.answer_callback_query(c.id, "🔒 Bot locked.", show_alert=True); return

    if data.startswith('appr_ok_'):
        _cb_approve(c, data[8:], approved=True); return
    if data.startswith('appr_no_'):
        _cb_approve(c, data[8:], approved=False); return
    if data.startswith('appr_info_'):
        _cb_appr_info(c, data[10:]); return
    if data == 'list_approvals':
        _cb_list_approvals(c); return

    if data.startswith('file_'):   _cb_file_control(c);  return
    if data.startswith('start_'):  _cb_start(c);         return
    if data.startswith('stop_'):   _cb_stop(c);          return
    if data.startswith('restart_'):_cb_restart(c);       return
    if data.startswith('delete_'): _cb_delete(c);        return
    if data.startswith('logs_'):   _cb_logs(c);          return
    if data.startswith('stream_'): _cb_stream(c);        return

    if data == 'upload':       _cb_upload(c);        return
    if data == 'check_files':  _send_files(uid, c.message.chat.id, c); return
    if data == 'speed':        _cb_speed(c);         return
    if data == 'stats':        _cb_stats(c);         return
    if data == 'back_main':    _cb_back_main(c);     return
    if data == 'stream_menu':  _cb_stream_menu(c);   return
    if data == 'send_command': _cb_send_command(c);  return

    if uid not in admin_ids:
        bot.answer_callback_query(c.id, "⚠️ Admins only.", show_alert=True); return

    if data == 'subscription':      _cb_sub_menu(c);         return
    if data == 'broadcast':         _cb_broadcast_init(c);   return
    if data == 'toggle_lock':       _cb_toggle_lock(c);      return
    if data == 'run_all_scripts':   _cb_run_all(c);          return
    if data == 'admin_panel':       _cb_admin_panel(c);      return
    if data == 'add_admin':         _cb_add_admin_init(c);   return
    if data == 'remove_admin':      _cb_rem_admin_init(c);   return
    if data == 'list_admins':       _cb_list_admins(c);      return
    if data == 'add_subscription':  _cb_add_sub_init(c);     return
    if data == 'remove_subscription':_cb_rem_sub_init(c);    return
    if data == 'check_subscription':_cb_check_sub_init(c);   return

    bot.answer_callback_query(c.id, "Unknown action.")

# ════════════════════════════════════════════════════════════════════════════
#  APPROVAL CALLBACKS
# ════════════════════════════════════════════════════════════════════════════
def _cb_approve(call, aid, approved):
    if call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "Admins only.", show_alert=True); return
    info = pending_approvals.get(aid)
    if not info:
        bot.answer_callback_query(call.id, "Approval not found / expired.", show_alert=True); return
    if info['status'] != 'pending':
        bot.answer_callback_query(call.id, f"Already {info['status']}.", show_alert=True); return

    info['status'] = 'approved' if approved else 'rejected'
    db_update_approval(aid, info['status'])

    target_uid = info['user_id']
    fn  = info['file_name']
    ft  = info['file_type']
    fp  = info['file_path']
    rc  = info['reply_chat']

    if approved:
        bot.answer_callback_query(call.id, f"✅ Approved {fn}")
        db_save_file(target_uid, fn, ft)
        try:
            bot.send_message(rc, f"✅ Your file <b>{fn}</b> was <b>approved</b>! Starting…",
                             parse_mode='HTML')
        except: pass
        uf = user_folder(target_uid)
        class _FakeMsg:
            chat = type('c', (), {'id': rc})()
            message_id = info.get('reply_msg_id', 0)
        _run_file(fp, target_uid, uf, fn, ft, _FakeMsg())
        try:
            bot.edit_message_text(
                f"✅ <b>Approved</b> by {call.from_user.first_name}\n"
                f"File: <code>{fn}</code>  User: <code>{target_uid}</code>",
                call.message.chat.id, call.message.message_id, parse_mode='HTML')
        except: pass
    else:
        bot.answer_callback_query(call.id, f"❌ Rejected {fn}")
        try:
            bot.send_message(rc, f"❌ Your file <b>{fn}</b> was <b>rejected</b> by admin.",
                             parse_mode='HTML')
        except: pass
        if os.path.exists(fp):
            try: os.remove(fp)
            except: pass
        try:
            bot.edit_message_text(
                f"❌ <b>Rejected</b> by {call.from_user.first_name}\n"
                f"File: <code>{fn}</code>  User: <code>{target_uid}</code>",
                call.message.chat.id, call.message.message_id, parse_mode='HTML')
        except: pass

def _cb_appr_info(call, aid):
    info = pending_approvals.get(aid)
    if not info:
        bot.answer_callback_query(call.id, "Not found."); return
    uid = info['user_id']
    ups, runs, last = db_get_stats(uid)
    sub = user_subscriptions.get(uid)
    sub_str = sub['expiry'].strftime('%Y-%m-%d') if sub else 'None'
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id,
                     f"👤 <b>User Info</b>\n"
                     f"ID: <code>{uid}</code>\n"
                     f"Status: {user_status_str(uid)}\n"
                     f"Uploads: {ups}  Runs: {runs}\n"
                     f"Last active: {last}\n"
                     f"Sub expiry: {sub_str}",
                     parse_mode='HTML')

def _cb_list_approvals(call):
    bot.answer_callback_query(call.id)
    pending = [(aid, info) for aid, info in pending_approvals.items()
               if info['status'] == 'pending']
    if not pending:
        bot.send_message(call.message.chat.id, "✅ No pending approvals."); return
    mk = types.InlineKeyboardMarkup()
    for aid, info in pending[:10]:
        mk.row(
            types.InlineKeyboardButton(
                f"✅ {info['file_name'][:20]} ({info['user_id']})",
                callback_data=f"appr_ok_{aid}"),
            types.InlineKeyboardButton("❌", callback_data=f"appr_no_{aid}")
        )
    bot.send_message(call.message.chat.id,
                     f"⏳ <b>{len(pending)} Pending Approvals</b>",
                     reply_markup=mk, parse_mode='HTML')

# ════════════════════════════════════════════════════════════════════════════
#  FILE CONTROL CALLBACKS
# ════════════════════════════════════════════════════════════════════════════
def _parse_file_cb(data):
    parts = data.split('_', 2)
    return int(parts[1]), parts[2]

def _cb_file_control(c):
    uid = c.from_user.id
    try:
        owner_uid, fn = _parse_file_cb(c.data)
    except:
        bot.answer_callback_query(c.id, "Parse error."); return
    if uid != owner_uid and uid not in admin_ids:
        bot.answer_callback_query(c.id, "Permission denied.", show_alert=True); return
    running = is_running(owner_uid, fn)
    ft = next((t for n,t in user_files.get(owner_uid,[]) if n==fn), '?')
    try:
        bot.answer_callback_query(c.id)
        bot.edit_message_text(
            f"⚙️ <b>{fn}</b> ({ft})\n"
            f"Owner: <code>{owner_uid}</code>\n"
            f"Status: {'🟢 Running' if running else '🔴 Stopped'}",
            c.message.chat.id, c.message.message_id,
            reply_markup=control_buttons(owner_uid, fn, running),
            parse_mode='HTML')
    except: pass

def _cb_start(c):
    uid = c.from_user.id
    try: owner_uid, fn = _parse_file_cb(c.data)
    except: bot.answer_callback_query(c.id,"Parse error."); return
    if uid != owner_uid and uid not in admin_ids:
        bot.answer_callback_query(c.id,"Permission denied.",show_alert=True); return
    if is_running(owner_uid, fn):
        bot.answer_callback_query(c.id,"Already running.",show_alert=True); return
    ft = next((t for n,t in user_files.get(owner_uid,[]) if n==fn), None)
    if not ft: bot.answer_callback_query(c.id,"File not found.",show_alert=True); return
    uf = user_folder(owner_uid)
    fp = os.path.join(uf, fn)
    if not os.path.exists(fp):
        bot.answer_callback_query(c.id,"File missing on disk.",show_alert=True); return
    bot.answer_callback_query(c.id, f"▶️ Starting {fn}…")
    _run_file(fp, owner_uid, uf, fn, ft, c.message)

def _cb_stop(c):
    uid = c.from_user.id
    try: owner_uid, fn = _parse_file_cb(c.data)
    except: bot.answer_callback_query(c.id,"Parse error."); return
    if uid != owner_uid and uid not in admin_ids:
        bot.answer_callback_query(c.id,"Permission denied.",show_alert=True); return
    key = f"{owner_uid}_{fn}"
    info = bot_scripts.pop(key, None)
    if info: kill_tree(info)
    _watchdog_threads.pop(key, None)
    bot.answer_callback_query(c.id, f"🔴 Stopped {fn}")
    try:
        ft = next((t for n,t in user_files.get(owner_uid,[]) if n==fn), '?')
        bot.edit_message_text(
            f"⚙️ <b>{fn}</b> ({ft}) — 🔴 Stopped",
            c.message.chat.id, c.message.message_id,
            reply_markup=control_buttons(owner_uid, fn, False), parse_mode='HTML')
    except: pass

def _cb_restart(c):
    uid = c.from_user.id
    try: owner_uid, fn = _parse_file_cb(c.data)
    except: bot.answer_callback_query(c.id,"Parse error."); return
    if uid != owner_uid and uid not in admin_ids:
        bot.answer_callback_query(c.id,"Permission denied.",show_alert=True); return
    key = f"{owner_uid}_{fn}"
    info = bot_scripts.pop(key, None)
    if info: kill_tree(info)
    _watchdog_threads.pop(key, None)
    time.sleep(1)
    ft = next((t for n,t in user_files.get(owner_uid,[]) if n==fn), None)
    if not ft: bot.answer_callback_query(c.id,"File not found.",show_alert=True); return
    uf = user_folder(owner_uid)
    fp = os.path.join(uf, fn)
    bot.answer_callback_query(c.id, f"🔄 Restarting {fn}…")
    _run_file(fp, owner_uid, uf, fn, ft, c.message)

def _cb_delete(c):
    uid = c.from_user.id
    try: owner_uid, fn = _parse_file_cb(c.data)
    except: bot.answer_callback_query(c.id,"Parse error."); return
    if uid != owner_uid and uid not in admin_ids:
        bot.answer_callback_query(c.id,"Permission denied.",show_alert=True); return
    key = f"{owner_uid}_{fn}"
    info = bot_scripts.pop(key, None)
    if info: kill_tree(info)
    _watchdog_threads.pop(key, None)
    uf = user_folder(owner_uid)
    for f in [fn, os.path.splitext(fn)[0]+'.log']:
        p = os.path.join(uf, f)
        if os.path.exists(p):
            try: os.remove(p)
            except: pass
    db_remove_file(owner_uid, fn)
    bot.answer_callback_query(c.id, f"🗑️ {fn} deleted")
    try:
        bot.edit_message_text(f"🗑️ <code>{fn}</code> deleted.",
                              c.message.chat.id, c.message.message_id, parse_mode='HTML')
    except: pass

def _cb_logs(c):
    uid = c.from_user.id
    try: owner_uid, fn = _parse_file_cb(c.data)
    except: bot.answer_callback_query(c.id,"Parse error."); return
    if uid != owner_uid and uid not in admin_ids:
        bot.answer_callback_query(c.id,"Permission denied.",show_alert=True); return
    uf = user_folder(owner_uid)
    log_path = os.path.join(uf, os.path.splitext(fn)[0]+'.log')
    if not os.path.exists(log_path):
        bot.answer_callback_query(c.id,"No logs yet.",show_alert=True); return
    bot.answer_callback_query(c.id)
    size = os.path.getsize(log_path)
    tail_b = MAX_LOG_TAIL_KB * 1024
    try:
        with open(log_path, 'rb') as f:
            if size > tail_b:
                f.seek(-tail_b, os.SEEK_END)
                raw = f.read()
                text = "(…)\n" + raw.decode('utf-8', errors='ignore')
            else:
                text = f.read().decode('utf-8', errors='ignore')
        if not text.strip():
            text = "(empty)"
        if len(text) > 4000:
            text = "…\n" + text[-4000:]
        bot.send_message(c.message.chat.id,
                         f"📋 <b>{fn}</b> logs:\n<pre>{text}</pre>",
                         parse_mode='HTML')
    except Exception as e:
        bot.send_message(c.message.chat.id, f"❌ Log read error: {e}")

def _cb_stream(c):
    uid = c.from_user.id
    try: owner_uid, fn = _parse_file_cb(c.data)
    except: bot.answer_callback_query(c.id,"Parse error."); return
    if uid != owner_uid and uid not in admin_ids:
        bot.answer_callback_query(c.id,"Permission denied.",show_alert=True); return
    bot.answer_callback_query(c.id)
    start_log_stream(uid, fn, owner_uid, c.message.chat.id)

# ════════════════════════════════════════════════════════════════════════════
#  NAV CALLBACKS
# ════════════════════════════════════════════════════════════════════════════
def _cb_upload(c):
    uid = c.from_user.id
    lim = file_limit(uid)
    cnt = file_count(uid)
    if cnt >= lim:
        bot.answer_callback_query(c.id,f"File limit reached ({int(lim)}).",show_alert=True); return
    bot.answer_callback_query(c.id)
    bot.send_message(c.message.chat.id,
                     "📤 Send your <code>.py</code>, <code>.js</code>, or <code>.zip</code> file.",
                     parse_mode='HTML')

def _send_files(uid, chat_id, call=None):
    files = user_files.get(uid, [])
    if not files:
        txt = "📂 No files uploaded yet."
        if call:
            try: bot.answer_callback_query(call.id); bot.send_message(chat_id, txt)
            except: pass
        else:
            bot.send_message(chat_id, txt)
        return
    mk = types.InlineKeyboardMarkup(row_width=1)
    for fn, ft in sorted(files):
        run = is_running(uid, fn)
        mk.add(types.InlineKeyboardButton(
            f"{'🟢' if run else '🔴'} {fn} ({ft})",
            callback_data=f'file_{uid}_{fn}'))
    mk.add(types.InlineKeyboardButton("🔙 Back", callback_data='back_main'))
    txt = f"📂 Your files ({len(files)}):"
    if call:
        try:
            bot.answer_callback_query(call.id)
            bot.edit_message_text(txt, chat_id, call.message.message_id, reply_markup=mk)
        except:
            bot.send_message(chat_id, txt, reply_markup=mk)
    else:
        bot.send_message(chat_id, txt, reply_markup=mk)

def _cb_speed(c):
    t = time.time()
    bot.answer_callback_query(c.id)
    lat = round((time.time()-t)*1000, 1)
    scripts_running = sum(1 for k in list(bot_scripts.keys())
                         if is_running(int(k.split('_')[0]), k.split('_',1)[1]))
    try:
        bot.edit_message_text(
            f"⚡ <b>Ping:</b> <code>{lat}ms</code>\n"
            f"🟢 Scripts running: {scripts_running}\n"
            f"👥 Active users: {len(active_users)}\n"
            f"🔒 Bot: {'Locked' if bot_locked else 'Unlocked'}",
            c.message.chat.id, c.message.message_id,
            reply_markup=main_inline(c.from_user.id), parse_mode='HTML')
    except: pass

def _send_stats(uid, chat_id):
    ups, runs, last = db_get_stats(uid)
    lim = file_limit(uid)
    cnt = file_count(uid)
    lim_str = str(int(lim)) if lim != float('inf') else "∞"
    extra = ""
    if uid in admin_ids:
        total_users = len(active_users)
        total_files = sum(len(f) for f in user_files.values())
        total_running = sum(1 for k in list(bot_scripts.keys())
                            if is_running(int(k.split('_')[0]), k.split('_',1)[1]))
        extra = f"\n📊 <b>Global:</b> {total_users} users, {total_files} files, {total_running} running"
    bot.send_message(chat_id,
                     f"📊 <b>Your Stats</b>\n"
                     f"🆔 <code>{uid}</code>  {user_status_str(uid)}\n"
                     f"📁 Files: {cnt}/{lim_str}\n"
                     f"⬆️ Total uploads: {ups}\n"
                     f"▶️ Total runs: {runs}\n"
                     f"🕐 Last active: {last}{extra}",
                     parse_mode='HTML')

def _cb_stats(c):
    bot.answer_callback_query(c.id)
    _send_stats(c.from_user.id, c.message.chat.id)

def _cb_back_main(c):
    uid = c.from_user.id
    bot.answer_callback_query(c.id)
    lim = file_limit(uid)
    lim_str = str(int(lim)) if lim != float('inf') else "∞"
    try:
        bot.edit_message_text(
            f"🏠 <b>Main Menu</b>\n{user_status_str(uid)}  |  "
            f"Files: {file_count(uid)}/{lim_str}",
            c.message.chat.id, c.message.message_id,
            reply_markup=main_inline(uid), parse_mode='HTML')
    except:
        bot.send_message(c.message.chat.id, "🏠 Main Menu",
                         reply_markup=main_inline(uid))

def _cb_stream_menu(c):
    uid = c.from_user.id
    bot.answer_callback_query(c.id)
    files = user_files.get(uid, [])
    running = [(fn,ft) for fn,ft in files if is_running(uid, fn)]
    if not running:
        bot.send_message(c.message.chat.id, "❌ No running scripts to stream."); return
    mk = types.InlineKeyboardMarkup()
    for fn, ft in running:
        mk.add(types.InlineKeyboardButton(f"📡 {fn}", callback_data=f"stream_{uid}_{fn}"))
    mk.add(types.InlineKeyboardButton("🔙 Back", callback_data='back_main'))
    bot.send_message(c.message.chat.id, "Select script to stream:", reply_markup=mk)

def _cb_send_command(c):
    uid = c.from_user.id
    bot.answer_callback_query(c.id)
    files = user_files.get(uid, [])
    running = [(fn,ft) for fn,ft in files if is_running(uid, fn)]
    if not running:
        bot.send_message(c.message.chat.id, "❌ No running scripts."); return
    mk = types.InlineKeyboardMarkup()
    for fn, _ in running:
        key = f"{uid}_{fn}"
        mk.add(types.InlineKeyboardButton(fn, callback_data=f"sendcmd_{key}"))
    bot.send_message(c.message.chat.id, "Select script to send command to:", reply_markup=mk)

@bot.callback_query_handler(func=lambda c: c.data.startswith('sendcmd_'))
def cb_sendcmd_select(c):
    script_key = c.data[8:]
    bot.answer_callback_query(c.id)
    msg = bot.send_message(c.message.chat.id, f"📝 Type command to send to <code>{script_key}</code>:",
                           parse_mode='HTML')
    bot.register_next_step_handler(msg, lambda m: _process_cmd_send(m, script_key))

def _process_cmd_send(message, script_key):
    if message.text and message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled."); return
    cmd = message.text
    dangerous = ['&&', '||', ';', '`', '$(']
    for d in dangerous:
        if d in cmd:
            bot.reply_to(message, f"❌ Blocked dangerous character: {d}")
            return
    info = bot_scripts.get(script_key)
    if not info:
        bot.reply_to(message, "❌ Script no longer running."); return
    try:
        info['process'].stdin.write(cmd + '\n')
        info['process'].stdin.flush()
        bot.reply_to(message, f"✅ Sent: <code>{cmd[:200]}</code>", parse_mode='HTML')
    except Exception as e:
        bot.reply_to(message, f"❌ Send failed: {e}")

# ════════════════════════════════════════════════════════════════════════════
#  ADMIN CALLBACKS
# ════════════════════════════════════════════════════════════════════════════
def _cb_toggle_lock(c):
    global bot_locked
    with _bot_lock_event:
        bot_locked = not bot_locked
    state = "🔒 Locked" if bot_locked else "🔓 Unlocked"
    bot.answer_callback_query(c.id, state)
    logger.warning(f"Bot {state} by admin {c.from_user.id}")

def _is_file_approved(uid, fn):
    return any(n == fn for n, t in user_files.get(uid, []))

def _cb_run_all(c):
    if c.from_user.id not in admin_ids:
        bot.answer_callback_query(c.id, "Admins only.", show_alert=True); return
    bot.answer_callback_query(c.id, "Starting all approved scripts…")
    started = 0
    for uid_iter, files in list(user_files.items()):
        uf = user_folder(uid_iter)
        for fn, ft in files:
            if not is_running(uid_iter, fn):
                if uid_iter == OWNER_ID or _is_file_approved(uid_iter, fn):
                    fp = os.path.join(uf, fn)
                    if os.path.exists(fp):
                        _run_file(fp, uid_iter, uf, fn, ft, c.message)
                        started += 1
                        time.sleep(0.5)
    bot.send_message(c.message.chat.id, f"✅ Started {started} approved scripts.")

def _cb_sub_menu(c):
    bot.answer_callback_query(c.id)
    mk = types.InlineKeyboardMarkup(row_width=2)
    mk.add(types.InlineKeyboardButton('➕ Add Sub', callback_data='add_subscription'),
           types.InlineKeyboardButton('➖ Remove Sub', callback_data='remove_subscription'))
    mk.add(types.InlineKeyboardButton('🔍 Check Sub', callback_data='check_subscription'))
    mk.add(types.InlineKeyboardButton('🔙 Back', callback_data='back_main'))
    try:
        bot.edit_message_text("💳 Subscription Management", c.message.chat.id,
                              c.message.message_id, reply_markup=mk)
    except: pass

def _cb_admin_panel(c):
    bot.answer_callback_query(c.id)
    mk = types.InlineKeyboardMarkup(row_width=2)
    mk.add(types.InlineKeyboardButton('➕ Add Admin', callback_data='add_admin'),
           types.InlineKeyboardButton('➖ Remove Admin', callback_data='remove_admin'))
    mk.add(types.InlineKeyboardButton('📋 List Admins', callback_data='list_admins'))
    mk.add(types.InlineKeyboardButton('🔙 Back', callback_data='back_main'))
    try:
        bot.edit_message_text("👑 Admin Panel", c.message.chat.id,
                              c.message.message_id, reply_markup=mk)
    except: pass

def _cb_list_admins(c):
    bot.answer_callback_query(c.id)
    lines = [f"• <code>{a}</code>{'  👑' if a==OWNER_ID else ''}" for a in sorted(admin_ids)]
    bot.send_message(c.message.chat.id, "👑 <b>Admins:</b>\n" + "\n".join(lines), parse_mode='HTML')

def _cb_broadcast_init(c):
    bot.answer_callback_query(c.id)
    msg = bot.send_message(c.message.chat.id, "📢 Send broadcast message (/cancel to abort):")
    bot.register_next_step_handler(msg, _process_broadcast)

def _process_broadcast(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "Admins only."); return
    if message.text and message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled."); return
    mk = types.InlineKeyboardMarkup()
    mk.row(types.InlineKeyboardButton("✅ Send", callback_data=f"bc_confirm_{message.message_id}"),
           types.InlineKeyboardButton("❌ Cancel", callback_data="bc_cancel"))
    bot.reply_to(message, f"Send to {len(active_users)} users?", reply_markup=mk)

@bot.callback_query_handler(func=lambda c: c.data.startswith('bc_'))
def cb_broadcast(c):
    if c.from_user.id not in admin_ids:
        bot.answer_callback_query(c.id,"Admins only.",show_alert=True); return
    if c.data == 'bc_cancel':
        bot.answer_callback_query(c.id,"Cancelled.")
        try: bot.delete_message(c.message.chat.id, c.message.message_id)
        except: pass
        return
    orig = c.message.reply_to_message
    if not orig:
        bot.answer_callback_query(c.id,"Original message lost.",show_alert=True); return
    bot.answer_callback_query(c.id,"📢 Broadcasting…")
    bot.edit_message_text(f"📢 Broadcasting to {len(active_users)} users…",
                          c.message.chat.id, c.message.message_id)
    Thread(target=_execute_broadcast, args=(orig, c.message.chat.id), daemon=True).start()

def _execute_broadcast(orig, admin_chat):
    sent = failed = blocked = 0
    for idx, uid in enumerate(list(active_users)):
        try:
            bot.copy_message(uid, orig.chat.id, orig.message_id)
            sent += 1
        except telebot.apihelper.ApiTelegramException as e:
            err = str(e).lower()
            if any(x in err for x in ['blocked','deactivated','not found','kicked']):
                blocked += 1
            elif 'flood' in err or 'too many' in err:
                time.sleep(5)
                try:
                    bot.copy_message(uid, orig.chat.id, orig.message_id)
                    sent += 1
                except: failed += 1
            else:
                failed += 1
        except:
            failed += 1
        if idx % 20 == 0 and idx > 0:
            time.sleep(1)
        elif idx % 5 == 0:
            time.sleep(0.1)
    try:
        bot.send_message(admin_chat,
                         f"📢 <b>Broadcast done</b>\n✅ {sent}  ❌ {failed}  🚫 {blocked}",
                         parse_mode='HTML')
    except: pass

# ─── subscription step handlers ──────────────────────────────────────────────
def _cb_add_sub_init(c):
    bot.answer_callback_query(c.id)
    m = bot.send_message(c.message.chat.id, "💳 Enter: <code>USER_ID DAYS</code>  (e.g. 123456 30)\n/cancel to abort",
                         parse_mode='HTML')
    bot.register_next_step_handler(m, _process_add_sub)

def _process_add_sub(message):
    if message.from_user.id not in admin_ids: return
    if message.text and message.text.lower() == '/cancel':
        bot.reply_to(message,"Cancelled."); return
    try:
        uid, days = message.text.split()
        uid = int(uid); days = int(days)
        assert uid > 0 and days > 0
        base = user_subscriptions.get(uid, {}).get('expiry', datetime.now())
        if base < datetime.now(): base = datetime.now()
        expiry = base + timedelta(days=days)
        db_save_sub(uid, expiry)
        bot.reply_to(message, f"✅ Sub added for <code>{uid}</code> (+{days}d → {expiry.date()})",
                     parse_mode='HTML')
        try: bot.send_message(uid, f"🎉 Subscription added: +{days} days (expires {expiry.date()})")
        except: pass
    except:
        bot.reply_to(message, "⚠️ Invalid format. Use: USER_ID DAYS")
        m = bot.send_message(message.chat.id, "Try again or /cancel:")
        bot.register_next_step_handler(m, _process_add_sub)

def _cb_rem_sub_init(c):
    bot.answer_callback_query(c.id)
    m = bot.send_message(c.message.chat.id, "💳 Enter user ID to remove sub:\n/cancel to abort")
    bot.register_next_step_handler(m, _process_rem_sub)

def _process_rem_sub(message):
    if message.from_user.id not in admin_ids: return
    if message.text and message.text.lower() == '/cancel':
        bot.reply_to(message,"Cancelled."); return
    try:
        uid = int(message.text.strip())
        db_remove_sub(uid)
        bot.reply_to(message, f"✅ Sub removed for <code>{uid}</code>", parse_mode='HTML')
        try: bot.send_message(uid, "ℹ️ Your subscription was removed by admin.")
        except: pass
    except:
        bot.reply_to(message, "⚠️ Invalid ID.")

def _cb_check_sub_init(c):
    bot.answer_callback_query(c.id)
    m = bot.send_message(c.message.chat.id, "💳 Enter user ID to check:\n/cancel to abort")
    bot.register_next_step_handler(m, _process_check_sub)

def _process_check_sub(message):
    if message.from_user.id not in admin_ids: return
    if message.text and message.text.lower() == '/cancel':
        bot.reply_to(message,"Cancelled."); return
    try:
        uid = int(message.text.strip())
        sub = user_subscriptions.get(uid)
        if sub:
            exp = sub['expiry']
            active = exp > datetime.now()
            days = (exp - datetime.now()).days if active else 0
            bot.reply_to(message,
                         f"{'✅ Active' if active else '❌ Expired'}\n"
                         f"User: <code>{uid}</code>\n"
                         f"Expires: {exp.date()}\n"
                         f"Days left: {days}",
                         parse_mode='HTML')
        else:
            bot.reply_to(message, f"ℹ️ No subscription for <code>{uid}</code>", parse_mode='HTML')
    except:
        bot.reply_to(message, "⚠️ Invalid ID.")

def _cb_add_admin_init(c):
    if c.from_user.id != OWNER_ID:
        bot.answer_callback_query(c.id,"Owner only.",show_alert=True); return
    bot.answer_callback_query(c.id)
    m = bot.send_message(c.message.chat.id, "👑 Enter user ID to promote:\n/cancel to abort")
    bot.register_next_step_handler(m, _process_add_admin)

def _process_add_admin(message):
    if message.from_user.id != OWNER_ID: return
    if message.text and message.text.lower() == '/cancel':
        bot.reply_to(message,"Cancelled."); return
    try:
        uid = int(message.text.strip())
        if uid in admin_ids:
            bot.reply_to(message, f"Already admin: <code>{uid}</code>", parse_mode='HTML'); return
        db_add_admin(uid)
        bot.reply_to(message, f"✅ <code>{uid}</code> is now admin.", parse_mode='HTML')
        try: bot.send_message(uid, "🎉 You are now an admin!")
        except: pass
    except:
        bot.reply_to(message, "⚠️ Invalid ID.")

def _cb_rem_admin_init(c):
    if c.from_user.id != OWNER_ID:
        bot.answer_callback_query(c.id,"Owner only.",show_alert=True); return
    bot.answer_callback_query(c.id)
    m = bot.send_message(c.message.chat.id, "👑 Enter admin ID to demote:\n/cancel to abort")
    bot.register_next_step_handler(m, _process_rem_admin)

def _process_rem_admin(message):
    if message.from_user.id != OWNER_ID: return
    if message.text and message.text.lower() == '/cancel':
        bot.reply_to(message,"Cancelled."); return
    try:
        uid = int(message.text.strip())
        if db_remove_admin(uid):
            bot.reply_to(message, f"✅ <code>{uid}</code> removed from admin.", parse_mode='HTML')
            try: bot.send_message(uid, "ℹ️ You are no longer an admin.")
            except: pass
        else:
            bot.reply_to(message, f"⚠️ Cannot remove <code>{uid}</code> (owner or not found).", parse_mode='HTML')
    except:
        bot.reply_to(message, "⚠️ Invalid ID.")

# ════════════════════════════════════════════════════════════════════════════
#  CLEANUP
# ════════════════════════════════════════════════════════════════════════════
def _cleanup():
    logger.warning("Shutdown — killing all scripts…")
    for key, info in list(bot_scripts.items()):
        kill_tree(info)
    logger.warning("Cleanup done.")

atexit.register(_cleanup)

# ════════════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    logger.info("="*50)
    logger.info("🤖 HostBot starting…")
    logger.info(f"Owner: {OWNER_ID}  |  Python: {sys.version.split()[0]}")
    logger.info("="*50)
    init_db()
    load_data()
    _expire_stale_approvals()
    keep_alive()
    logger.info("🚀 Polling…")
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=30,
                                 logger_level=logging.WARNING)
        except requests.exceptions.ReadTimeout:
            logger.warning("ReadTimeout — restarting poll in 5s")
            time.sleep(5)
        except requests.exceptions.ConnectionError as e:
            logger.error(f"ConnectionError: {e} — retry in 15s")
            time.sleep(15)
        except Exception as e:
            logger.critical(f"Polling crash: {e}", exc_info=True)
            time.sleep(30)
