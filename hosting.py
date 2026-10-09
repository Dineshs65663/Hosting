
"""
import subprocess, sys, importlib

# ─── Auto-install the bot's own dependencies (pip name → import name) ───────
_REQUIRED = {"psutil": "psutil", "flask": "flask", "requests": "requests",
             "pyTelegramBotAPI": "telebot"}
for _pkg, _mod in _REQUIRED.items():
    try:
        importlib.import_module(_mod)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", _pkg, "-q"])

import ast
import atexit
import difflib
import functools
import csv
import hashlib
import hmac
import html
import io
import json
import logging
import math
import os
import re
import shlex
import shutil
import signal
import sqlite3
import threading
import time
import unicodedata
import uuid
import zipfile
from collections import Counter, defaultdict, deque
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler

import psutil
import requests
import telebot
from flask import Flask, abort, jsonify, request
from telebot import types
from telebot.apihelper import ApiTelegramException

try:
    import resource            # POSIX only
except ImportError:            # Windows
    resource = None

# ════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION  — edit only this block
# ════════════════════════════════════════════════════════════════════════════
TOKEN        = '8976866439:AAHkL9xOzyLedr5c6S6CG0NI2KEOOvUDEH4'   # ← your token
OWNER_ID     = 8767249265
ADMIN_ID     = 8767249265
YOUR_USERNAME   = '@DKS65663'
UPDATE_CHANNEL  = 'https://t.me/dkschattinggroup'
FORCE_JOIN_CHANNELS = ["@dksinfo2266"]     # the bot must be an admin in these

# Limits (number of hosted files per user)
FREE_USER_LIMIT       = 10
SUBSCRIBED_USER_LIMIT = 20
ADMIN_LIMIT           = 999

# Cooldowns (seconds)
UPLOAD_COOLDOWN   = 15
ACTION_COOLDOWN   = 0.4      # minimum gap between button presses per user
APPROVE_TIMEOUT   = 3600     # pending uploads expire after this long
INPUT_TIMEOUT     = 300      # how long the bot waits for a typed answer

# Uploads
MAX_FILE_SIZE_MB  = 20       # Telegram bots cannot download files above 20 MB
MAX_UNZIP_MB      = 100      # total size a zip may unpack to (zip-bomb guard)
MAX_ZIP_FILES     = 2000

# Sandbox / resource limits
SANDBOX           = 'auto'   # 'auto' | 'docker' | 'bwrap' | 'process'
DOCKER_PY_IMAGE   = 'python:3.12-slim'
DOCKER_JS_IMAGE   = 'node:20-slim'
MAX_RAM_MB        = 256      # per script (0 = no limit)
MAX_CPUS          = 0.5      # per script — enforced in Docker mode only
MAX_PIDS          = 128      # per script — enforced in Docker mode only
MAX_FILE_WRITE_MB = 512      # biggest single file a script may write (Linux, no-Docker mode)
MAX_LOG_MB        = 5        # per script; at this size the log starts over (one backup kept)

# Supervision
MAX_RESTARTS      = 5        # crashes allowed inside RESTART_WINDOW before giving up
RESTART_WINDOW    = 600      # seconds
RESTART_BACKOFF   = 5        # first retry delay; doubles each time (max 60s)
START_CHECK_DELAY = 2.5      # seconds to wait before reporting "started"
AUTO_INSTALL_MAX  = 5        # missing-package installs per start
AUTO_INSTALL_UNKNOWN = False # False = packages not on the known/allowed list need an admin's OK
INSTALL_TIMEOUT   = 600      # seconds for pip / npm
NPM_IGNORE_SCRIPTS = True    # do not run npm install scripts of dependencies

# Interface
FILES_PER_PAGE    = 8
LIVE_LOG_INTERVAL = 4        # seconds between live-log refreshes
LIVE_LOG_DURATION = 120      # a live log view stops by itself after this long
SUB_REMIND_DAYS   = 3        # remind users this many days before a sub expires

# Plans — free users get FREE_USER_LIMIT files and MAX_RAM_MB / MAX_CPUS per script.
# Paid plans, lowest first. 'files' defaults to SUBSCRIBED_USER_LIMIT. 'stars' is
# the price in Telegram Stars for 'days' days.
PLANS = {
    'premium': {'name': '⭐ Premium', 'ram': 512,  'cpus': 1.0, 'stars': 250, 'days': 30},
    'pro':     {'name': '💎 Pro',     'ram': 1024, 'cpus': 2.0, 'stars': 600, 'days': 30, 'files': 40},
}
STARS_ENABLED       = False  # True = users can buy the plans above with Telegram Stars
TRIAL_DAYS          = 3      # one free trial of the first plan per user (0 = off)
REFERRAL_BONUS_DAYS = 3      # days of the first plan for each new user someone invites (0 = off)
REFERRAL_MAX        = 20     # most invites that are rewarded per user

# More limits
MAX_PROJECT_MB      = 500    # disk per hosted file, including its packages (0 = no limit)
DISK_CHECK_INTERVAL = 300    # seconds between disk-usage checks of a running script
CPU_LIMIT_SECONDS   = 120    # without Docker: stop a script that stays above its CPU share this long (0 = off)
SCRIPT_NICE         = 10     # without Docker: scripts run at lower priority than the bot (0 = off)
MAX_NET_OUT_MB_PER_HOUR = 0  # Docker only: stop a script that uploads more than this per hour (0 = off)
DOCKER_NETWORK      = 'hostbot_net'  # Docker only: private network, containers cannot reach each other
TAMPER_CHECK        = True   # refuse to start code that changed on disk after it was approved
MAX_PARALLEL_INSTALLS = 1    # pip / npm runs allowed at the same time
LOG_BACKUPS         = 3      # old log files kept per script
MAX_ENV_VARS        = 30     # variables a user may set per hosted file
PY_VERSIONS         = ['3.12', '3.11', '3.10', '3.13']   # selectable per file (Docker only)
NODE_VERSIONS       = ['20', '18', '22']

# Deploy from a Git link
GIT_ENABLED         = True
GIT_ALLOWED_HOSTS   = ['github.com', 'gitlab.com', 'bitbucket.org', 'codeberg.org']
GIT_TIMEOUT         = 120    # seconds for a clone

# Owner / admin extras
APPROVE_REMIND_BEFORE = 900  # remind reviewers this long before an upload expires (0 = off)
BACKUP_INTERVAL_HOURS = 24   # database backup sent to the owner (0 = off)
BACKUP_KEEP           = 7    # backup copies kept in data/backups/
DAILY_REPORT_HOUR_UTC = 6    # hour (UTC) of the daily summary for the owner (-1 = off)
ALERT_DISK_PERCENT    = 90   # warn the owner when the disk is fuller than this
ALERT_RAM_PERCENT     = 90   # … or the server's RAM
ALERT_CRASH_LOOPS     = 3    # … or this many scripts gave up within an hour
ALERT_COOLDOWN        = 3600 # seconds before the same warning is repeated
WEBHOOK_URL           = ''   # public https address of this bot → webhook instead of polling

# ════════════════════════════════════════════════════════════════════════════
#  PATHS
# ════════════════════════════════════════════════════════════════════════════
BASE_DIR      = os.path.abspath(os.path.dirname(__file__))
PROJECTS_DIR  = os.path.join(BASE_DIR, 'projects')       # approved, runnable code
LEGACY_DIR    = os.path.join(BASE_DIR, 'upload_bots')    # layout of the old version
DATA_DIR      = os.path.join(BASE_DIR, 'data')
PENDING_DIR   = os.path.join(DATA_DIR, 'pending')        # uploads awaiting review
LOGS_DIR      = os.path.join(DATA_DIR, 'script_logs')
DATABASE_PATH = os.path.join(DATA_DIR, 'bot_data.db')
LOG_FILE_PATH = os.path.join(DATA_DIR, 'bot_main.log')
BACKUP_DIR    = os.path.join(DATA_DIR, 'backups')

for _d in (PROJECTS_DIR, DATA_DIR, PENDING_DIR, LOGS_DIR, BACKUP_DIR):
    os.makedirs(_d, exist_ok=True)

# ════════════════════════════════════════════════════════════════════════════
#  LOGGING
# ════════════════════════════════════════════════════════════════════════════
_log_fmt = '%(asctime)s [%(levelname)s] %(name)s — %(message)s'
logging.basicConfig(level=logging.INFO, format=_log_fmt)
logger = logging.getLogger('HostBot')
if not any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
    _fh = RotatingFileHandler(LOG_FILE_PATH, maxBytes=5 * 1024 * 1024,
                              backupCount=3, encoding='utf-8')
    _fh.setFormatter(logging.Formatter(_log_fmt))
    logger.addHandler(_fh)
logging.getLogger('werkzeug').setLevel(logging.WARNING)

# ════════════════════════════════════════════════════════════════════════════
#  BOT INIT  (every message is sent as HTML, so all dynamic text is escaped)
# ════════════════════════════════════════════════════════════════════════════
bot = telebot.TeleBot(TOKEN, parse_mode='HTML', threaded=True, num_threads=8)

# ════════════════════════════════════════════════════════════════════════════
#  IN-MEMORY STATE
# ════════════════════════════════════════════════════════════════════════════
STATE      = {'locked': False, 'boot': time.time()}
SHUTDOWN   = threading.Event()
admin_ids  = {ADMIN_ID, OWNER_ID}      # full admins
reviewer_ids = set()                   # may only review uploads and package requests

RUN_LOCK        = threading.RLock()
RUNS            = {}                  # project id → Run (live process)
STARTING        = set()               # project ids being launched right now
INSTALLING      = set()               # project ids with pip/npm running
INSTALL_QUEUE   = set()               # project ids waiting for a free install slot
PENDING_RESTART = {}                  # project id → time the next retry is due
CRASHES         = defaultdict(deque)  # project id → recent crash timestamps

AWAIT       = {}    # user id → {'kind', 'arg', 'exp'}   (typed answers we wait for)
BROADCASTS  = {}    # admin id → {'src': (chat id, message id), 'target': 'all'|'subs'|'free'}
_last_alert = {}    # alert name → when it was last sent
LIVE        = {}    # (chat id, message id) → token of the live-log thread
_last_upload = {}   # user id → timestamp
_last_action = {}   # user id → timestamp
_join_cache  = {}   # user id → time until which "has joined" is trusted
_pending_ref = {}   # new user id → id of the user whose invite link they opened

# ════════════════════════════════════════════════════════════════════════════
#  SMALL HELPERS
# ════════════════════════════════════════════════════════════════════════════
def esc(value) -> str:
    """Escape text for Telegram HTML."""
    return html.escape(str(value), quote=False)

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

def iso_now() -> str:
    return utcnow().isoformat(timespec='seconds')

def parse_dt(value):
    """ISO text → aware UTC datetime (naive values are taken as local time)."""
    if not value:
        return None
    try:
        d = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return d.astimezone(timezone.utc)

def fmt_dt(value) -> str:
    d = value if isinstance(value, datetime) else parse_dt(value)
    return d.strftime('%Y-%m-%d %H:%M UTC') if d else '—'

def human_size(n) -> str:
    n = float(n or 0)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n:.0f} {unit}' if unit in ('B', 'KB') else f'{n:.1f} {unit}'
        n /= 1024

def human_dur(seconds) -> str:
    s = int(max(0, seconds))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f'{d}d {h}h' if h else f'{d}d'
    if h:
        return f'{h}h {m}m' if m else f'{h}h'
    if m:
        return f'{m}m {s}s' if s else f'{m}m'
    return f'{s}s'

def bar(used, total, width=10) -> str:
    """▰▰▰▱▱▱▱▱▱▱ 3/10"""
    if total == float('inf'):
        return f'{used}/∞'
    total = int(total)
    filled = min(width, int(round(width * used / total))) if total > 0 else width
    if used > 0 and filled == 0:
        filled = 1
    return f"{'▰' * filled}{'▱' * (width - filled)} {used}/{total}"

def _clean_chars(text, extra='._-') -> str:
    """Keep letters/digits of any script (with their combining marks) and the
    characters in `extra`; every other run of characters becomes one '_'."""
    out = []
    for ch in unicodedata.normalize('NFC', text):
        if ch.isalnum() or ch in extra or unicodedata.category(ch)[0] == 'M':
            out.append(ch)
        elif not out or out[-1] != '_':
            out.append('_')
    return ''.join(out)

def safe_name(name, maxlen=48) -> str:
    """Turn any uploaded file name into a harmless single path component."""
    name = str(name or '').replace('\\', '/').split('/')[-1]
    stem, ext = os.path.splitext(name)
    stem = _clean_chars(stem).strip('._') or 'file'
    ext = re.sub(r'[^A-Za-z0-9.]', '', ext).lower()[:8]
    return stem[:max(1, maxlen - len(ext))] + ext

def check_rate(store: dict, uid: int, cooldown: float) -> bool:
    now = time.time()
    if now - store.get(uid, 0) < cooldown:
        return False
    store[uid] = now
    return True

# ════════════════════════════════════════════════════════════════════════════
#  FLASK KEEP-ALIVE
# ════════════════════════════════════════════════════════════════════════════
_flask_app = Flask('hostbot')

@_flask_app.route('/')
def _home():
    return f"HostBot running — {running_count()} scripts active."

@_flask_app.route('/health')
def _health():
    return jsonify(status='ok', running=running_count(),
                   uptime=int(time.time() - STATE['boot']))

def _webhook_secret() -> str:
    return hashlib.sha256(('hostbot-webhook:' + TOKEN).encode()).hexdigest()[:40]

@_flask_app.route('/telegram/<secret>', methods=['POST'])
def _webhook(secret):
    """Telegram delivers updates here when WEBHOOK_URL is set."""
    expected = _webhook_secret()
    if (not WEBHOOK_URL or not hmac.compare_digest(secret, expected)
            or request.headers.get('X-Telegram-Bot-Api-Secret-Token') != expected):
        abort(403)
    try:
        bot.process_new_updates([types.Update.de_json(request.get_data(as_text=True))])
    except Exception:
        logger.exception('Could not process a webhook update')
    return 'ok'

def _run_flask():
    try:
        _flask_app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
    except Exception as e:
        logger.error('Keep-alive web server stopped: %s', e)

def keep_alive():
    threading.Thread(target=_run_flask, daemon=True, name='flask').start()
    logger.info('Flask keep-alive started.')

# ════════════════════════════════════════════════════════════════════════════
#  DATABASE  (one shared connection, WAL mode, every call serialised)
# ════════════════════════════════════════════════════════════════════════════
_DB_LOCK = threading.RLock()
_conn = None

def _db():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False,
                                timeout=15, isolation_level=None)
        _conn.row_factory = sqlite3.Row
        _conn.execute('PRAGMA journal_mode=WAL')
        _conn.execute('PRAGMA synchronous=NORMAL')
    return _conn

def q(sql, args=()):
    """Run a SELECT and return all rows."""
    with _DB_LOCK:
        return _db().execute(sql, args).fetchall()

def q1(sql, args=()):
    rows = q(sql, args)
    return rows[0] if rows else None

def x(sql, args=()):
    """Run a write statement; returns the cursor (rowcount / lastrowid)."""
    with _DB_LOCK:
        return _db().execute(sql, args)

def _table_exists(name) -> bool:
    return q1("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)) is not None

def init_db():
    with _DB_LOCK:
        _db().executescript("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id INTEGER PRIMARY KEY, expiry TEXT);
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY, first_name TEXT, username TEXT,
                first_seen TEXT, last_active TEXT, banned INTEGER DEFAULT 0,
                total_uploads INTEGER DEFAULT 0, total_runs INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL, name TEXT NOT NULL,
                main_file TEXT NOT NULL, file_type TEXT NOT NULL,
                upload_time TEXT, sha256 TEXT,
                run_count INTEGER DEFAULT 0, should_run INTEGER DEFAULT 0,
                last_exit INTEGER, last_reason TEXT,
                os_pid INTEGER, os_pid_ctime REAL,
                UNIQUE (user_id, name));
            CREATE TABLE IF NOT EXISTS approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL, name TEXT, main_file TEXT, file_type TEXT,
                stage_dir TEXT, tg_file_id TEXT, sha256 TEXT, report TEXT,
                submitted_at REAL, status TEXT DEFAULT 'pending',
                decided_by INTEGER, reason TEXT, cards TEXT DEFAULT '[]');
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS project_env (
                project_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT,
                PRIMARY KEY (project_id, key));
            CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, actor INTEGER,
                action TEXT, target TEXT, detail TEXT);
            CREATE TABLE IF NOT EXISTS events (
                ts REAL, kind TEXT, project_id INTEGER);
            CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
            CREATE TABLE IF NOT EXISTS allowed_packages (
                lang TEXT NOT NULL, name TEXT NOT NULL, PRIMARY KEY (lang, name));
            CREATE TABLE IF NOT EXISTS pkg_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER, lang TEXT,
                pkg TEXT, status TEXT DEFAULT 'pending', ts REAL);
            CREATE TABLE IF NOT EXISTS promo (
                code TEXT PRIMARY KEY, plan TEXT, days INTEGER, max_uses INTEGER,
                used INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS promo_uses (
                code TEXT NOT NULL, user_id INTEGER NOT NULL, PRIMARY KEY (code, user_id));
            CREATE TABLE IF NOT EXISTS payments (
                charge_id TEXT PRIMARY KEY, user_id INTEGER, plan TEXT, days INTEGER,
                stars INTEGER, ts REAL);
            CREATE TABLE IF NOT EXISTS scheduled_broadcasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER, from_chat INTEGER,
                message_id INTEGER, target TEXT, due REAL, status TEXT DEFAULT 'pending');
        """)
        for table, col, decl in (
                ('subscriptions', 'reminded', 'INTEGER DEFAULT 0'),
                ('subscriptions', 'plan', "TEXT DEFAULT 'premium'"),
                ('admins', 'role', "TEXT DEFAULT 'admin'"),
                ('users', 'blocked', 'INTEGER DEFAULT 0'),
                ('users', 'trial_used', 'INTEGER DEFAULT 0'),
                ('users', 'referred_by', 'INTEGER'),
                ('users', 'ref_count', 'INTEGER DEFAULT 0'),
                ('projects', 'manifest', 'TEXT'),
                ('projects', 'args', "TEXT DEFAULT ''"),
                ('projects', 'runtime', "TEXT DEFAULT ''"),
                ('projects', 'mute', 'INTEGER DEFAULT 0'),
                ('projects', 'sched_kind', "TEXT DEFAULT ''"),
                ('projects', 'sched_value', "TEXT DEFAULT ''"),
                ('projects', 'sched_next', 'REAL'),
                ('projects', 'disk_bytes', 'INTEGER DEFAULT 0'),
                ('projects', 'disk_checked', 'REAL DEFAULT 0'),
                ('projects', 'source', "TEXT DEFAULT ''"),
                ('approvals', 'kind', "TEXT DEFAULT 'upload'"),
                ('approvals', 'target', 'TEXT'),
                ('approvals', 'project_id', 'INTEGER'),
                ('approvals', 'reminded', 'INTEGER DEFAULT 0'),
                ('approvals', 'source', "TEXT DEFAULT ''")):
            if col not in [r['name'] for r in q(f'PRAGMA table_info({table})')]:
                x(f'ALTER TABLE {table} ADD COLUMN {col} {decl}')
        for a in {OWNER_ID, ADMIN_ID}:
            x('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (a,))
    _migrate_legacy()
    for r in q('SELECT user_id, role FROM admins'):
        (reviewer_ids if r['role'] == 'reviewer' else admin_ids).add(r['user_id'])
    reviewer_ids.difference_update(admin_ids)
    STATE['locked'] = get_setting('locked', '0') == '1'
    logger.info('DB ready: %d users, %d projects, %d admins.',
                q1('SELECT COUNT(*) n FROM users')['n'],
                q1('SELECT COUNT(*) n FROM projects')['n'], len(admin_ids) + len(reviewer_ids))

def get_setting(key, default=None):
    row = q1('SELECT value FROM settings WHERE key=?', (key,))
    return row['value'] if row else default

def set_setting(key, value):
    x('INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)', (key, str(value)))

def _migrate_legacy():
    """One-time import of the old version's data (user_files / upload_bots/)."""
    if get_setting('migrated_v2') or not _table_exists('user_files'):
        set_setting('migrated_v2', '1')
        return
    logger.info('Migrating data from the previous version…')
    now = iso_now()
    if _table_exists('active_users'):
        for r in q('SELECT user_id, first_seen FROM active_users'):
            x('INSERT OR IGNORE INTO users (user_id, first_seen, last_active) VALUES (?,?,?)',
              (r['user_id'], r['first_seen'] or now, now))
    if _table_exists('user_stats'):
        for r in q('SELECT user_id, total_uploads, total_runs FROM user_stats'):
            x('UPDATE users SET total_uploads=?, total_runs=? WHERE user_id=?',
              (r['total_uploads'] or 0, r['total_runs'] or 0, r['user_id']))
    unreviewed = set()
    if _table_exists('pending_approvals'):
        for r in q("SELECT user_id, file_name FROM pending_approvals WHERE status='pending'"):
            unreviewed.add((r['user_id'], r['file_name']))
    moved = 0
    for r in q('SELECT user_id, file_name, file_type, upload_time, run_count FROM user_files'):
        uid, fn = r['user_id'], r['file_name']
        src_dir = os.path.join(LEGACY_DIR, str(uid))
        if not os.path.isfile(os.path.join(src_dir, fn)):
            continue
        name, n = safe_name(fn), 1
        while q1('SELECT 1 FROM projects WHERE user_id=? AND name=?', (uid, name)):
            n += 1
            name = safe_name(f'{n}_{fn}')
        pid = x('INSERT INTO projects (user_id, name, main_file, file_type, upload_time, run_count) '
                'VALUES (?,?,?,?,?,?)',
                (uid, name, fn, r['file_type'], r['upload_time'] or now, r['run_count'] or 0)).lastrowid
        dst = project_dir(pid)
        os.makedirs(dst, exist_ok=True)
        # The old layout kept every file of a user in one folder, so copy the
        # siblings too (a zip project needs its other modules) — except logs
        # and uploads that were never approved.
        for item in os.listdir(src_dir):
            if item.endswith('.log') or ((uid, item) in unreviewed and item != fn):
                continue
            s = os.path.join(src_dir, item)
            try:
                if os.path.isdir(s):
                    shutil.copytree(s, os.path.join(dst, item), symlinks=True)
                else:
                    shutil.copy2(s, os.path.join(dst, item))
            except OSError as e:
                logger.warning('Migration: could not copy %s: %s', s, e)
        x('INSERT OR IGNORE INTO users (user_id, first_seen, last_active) VALUES (?,?,?)', (uid, now, now))
        moved += 1
    set_setting('migrated_v2', '1')
    logger.info('Migration done: %d files imported into projects/. The old upload_bots/ '
                'folder is no longer used and can be deleted once you have checked them.', moved)

# ─── users / admins / subscriptions ──────────────────────────────────────────
def is_admin(uid) -> bool:
    """Full admin (the owner included)."""
    return uid in admin_ids

def is_staff(uid) -> bool:
    """Admin or reviewer."""
    return uid in admin_ids or uid in reviewer_ids

def staff_ids() -> set:
    return set(admin_ids) | set(reviewer_ids)

def audit(actor, action, target='', detail=''):
    """Record who did what (shown under Admin Panel → Audit log)."""
    try:
        x('INSERT INTO audit (ts, actor, action, target, detail) VALUES (?,?,?,?,?)',
          (time.time(), actor, action, str(target)[:120], str(detail)[:300]))
    except sqlite3.Error:
        logger.exception('Could not write the audit log')

def log_event(kind, pid=None):
    """Count things for the daily report and the alerts (start, crash, gave_up, upload…)."""
    try:
        x('INSERT INTO events (ts, kind, project_id) VALUES (?,?,?)', (time.time(), kind, pid))
    except sqlite3.Error:
        logger.exception('Could not record an event')

def count_events(kind, since) -> int:
    return q1('SELECT COUNT(*) n FROM events WHERE kind=? AND ts>=?', (kind, since))['n']

def is_banned(uid) -> bool:
    row = q1('SELECT banned FROM users WHERE user_id=?', (uid,))
    return bool(row and row['banned'])

def touch_user(u) -> bool:
    """Record a Telegram user. Returns True the first time we see them."""
    now = iso_now()
    with _DB_LOCK:
        if q1('SELECT 1 FROM users WHERE user_id=?', (u.id,)):
            x('UPDATE users SET first_name=?, username=?, last_active=?, blocked=0 WHERE user_id=?',
              (u.first_name, u.username, now, u.id))
            return False
        x('INSERT INTO users (user_id, first_name, username, first_seen, last_active) '
          'VALUES (?,?,?,?,?)', (u.id, u.first_name, u.username, now, now))
        return True

def db_add_admin(uid, role='admin'):
    """role: 'admin' (everything) or 'reviewer' (only reviews uploads)."""
    if uid == OWNER_ID:
        role = 'admin'
    x('INSERT OR REPLACE INTO admins (user_id, role) VALUES (?,?)', (uid, role))
    admin_ids.discard(uid)
    reviewer_ids.discard(uid)
    (reviewer_ids if role == 'reviewer' else admin_ids).add(uid)

def db_remove_admin(uid) -> bool:
    if uid == OWNER_ID or not is_staff(uid):
        return False
    x('DELETE FROM admins WHERE user_id=?', (uid,))
    admin_ids.discard(uid)
    reviewer_ids.discard(uid)
    return True

def sub_expiry(uid):
    row = q1('SELECT expiry FROM subscriptions WHERE user_id=?', (uid,))
    return parse_dt(row['expiry']) if row else None

def has_sub(uid) -> bool:
    exp = sub_expiry(uid)
    return bool(exp and exp > utcnow())

def db_save_sub(uid, expiry, plan=None):
    plan = plan if plan in PLANS else first_plan()
    x('INSERT OR REPLACE INTO subscriptions (user_id, expiry, reminded, plan) VALUES (?,?,0,?)',
      (uid, expiry.isoformat(timespec='seconds'), plan))

def db_remove_sub(uid) -> bool:
    return x('DELETE FROM subscriptions WHERE user_id=?', (uid,)).rowcount > 0

# ─── plans ───────────────────────────────────────────────────────────────────
def first_plan() -> str:
    return next(iter(PLANS), 'premium')

def plan_name(key) -> str:
    return PLANS.get(key, {}).get('name') or '⭐ Premium'

def _plan_rank(key) -> int:
    keys = list(PLANS)
    return keys.index(key) if key in keys else -1

def plan_key(uid) -> str:
    """'owner', 'admin', 'free' or the key of the user's active plan."""
    if uid == OWNER_ID:
        return 'owner'
    if is_admin(uid):
        return 'admin'
    row = q1('SELECT expiry, plan FROM subscriptions WHERE user_id=?', (uid,))
    exp = parse_dt(row['expiry']) if row else None
    if exp and exp > utcnow():
        return row['plan'] if row['plan'] in PLANS else first_plan()
    return 'free'

def limits_for(uid) -> dict:
    """What a user's plan allows: number of files, RAM (MB) and CPU cores per script."""
    key = plan_key(uid)
    best_ram = max([MAX_RAM_MB] + [pl.get('ram', 0) for pl in PLANS.values()])
    best_cpu = max([MAX_CPUS] + [pl.get('cpus', 0) for pl in PLANS.values()])
    if key == 'owner':
        files, ram, cpus = float('inf'), best_ram, best_cpu
    elif key == 'admin':
        files, ram, cpus = ADMIN_LIMIT, best_ram, best_cpu
    elif key == 'free':
        files, ram, cpus = FREE_USER_LIMIT, MAX_RAM_MB, MAX_CPUS
    else:
        pl = PLANS.get(key, {})
        files = pl.get('files', SUBSCRIBED_USER_LIMIT)
        ram, cpus = pl.get('ram', MAX_RAM_MB), pl.get('cpus', MAX_CPUS)
    if not MAX_RAM_MB:
        ram = 0                                  # RAM limit switched off for everyone
    return {'files': files, 'ram': ram, 'cpus': cpus}

def grant_sub(uid, plan, days):
    """Add `days` of `plan`. Time left on a current subscription is kept, and a
    higher plan is never replaced by a lower one. Returns (plan, expiry)."""
    plan = plan if plan in PLANS else first_plan()
    now = utcnow()
    base = now
    row = q1('SELECT expiry, plan FROM subscriptions WHERE user_id=?', (uid,))
    exp = parse_dt(row['expiry']) if row else None
    if exp and exp > now:
        base = exp
        if _plan_rank(row['plan']) > _plan_rank(plan):
            plan = row['plan']
    expiry = base + timedelta(days=days)
    db_save_sub(uid, expiry, plan)
    return plan, expiry

def file_limit(uid) -> float:
    return limits_for(uid)['files']

def status_str(uid) -> str:
    if uid == OWNER_ID:
        return '👑 Owner'
    if is_admin(uid):
        return '🛡️ Admin'
    role = ' · 🔎 Reviewer' if uid in reviewer_ids else ''
    exp = sub_expiry(uid)
    if exp:
        if exp > utcnow():
            return f'{plan_name(plan_key(uid))} ({(exp - utcnow()).days}d left){role}'
        return f'🆓 Free (subscription expired){role}'
    return f'🆓 Free{role}'

def set_banned(uid, banned: bool):
    x('INSERT OR IGNORE INTO users (user_id, first_seen, last_active) VALUES (?,?,?)',
      (uid, iso_now(), iso_now()))
    x('UPDATE users SET banned=? WHERE user_id=?', (1 if banned else 0, uid))

# ─── projects ────────────────────────────────────────────────────────────────
def project_dir(pid) -> str:
    return os.path.join(PROJECTS_DIR, str(int(pid)))

def log_path(pid) -> str:
    return os.path.join(LOGS_DIR, f'{int(pid)}.log')

def get_project(pid):
    return q1('SELECT * FROM projects WHERE id=?', (pid,))

def user_projects(uid):
    return q('SELECT * FROM projects WHERE user_id=? ORDER BY name COLLATE NOCASE', (uid,))

def project_count(uid) -> int:
    return q1('SELECT COUNT(*) n FROM projects WHERE user_id=?', (uid,))['n']

def pending_count(uid=None) -> int:
    if uid is None:
        return q1("SELECT COUNT(*) n FROM approvals WHERE status='pending'")['n']
    return q1("SELECT COUNT(*) n FROM approvals WHERE status='pending' AND user_id=? "
              "AND COALESCE(kind, 'upload') != 'patch'", (uid,))['n']

# ════════════════════════════════════════════════════════════════════════════
#  SECURITY — upload scanning
# ════════════════════════════════════════════════════════════════════════════
_DANGEROUS_EXTENSIONS = {
    '.exe', '.dll', '.bat', '.cmd', '.scr', '.com', '.pif',
    '.msi', '.msp', '.hta', '.cpl', '.msc', '.bin',
    '.apk', '.dmg', '.iso', '.img', '.so', '.dylib',
}
_BINARY_SIGNATURES = [
    (b'MZ',               'Windows PE executable'),
    (b'\x7fELF',          'Linux ELF executable'),
    (b'\xfe\xed\xfa',     'Mach-O binary'),
    (b'\xce\xfa\xed\xfe', 'Mach-O binary (LE)'),
    (b'\xcf\xfa\xed\xfe', 'Mach-O binary (64-bit)'),
]

class UploadError(Exception):
    """An upload was refused; the message is shown to the user."""

def _entropy(data: bytes) -> float:
    if not data:
        return 0.0
    total = len(data)
    return -sum((n / total) * math.log2(n / total) for n in Counter(data).values())

def scan_file(content: bytes, filename: str, uid: int):
    """Returns (is_safe, reason). The owner always passes."""
    if uid == OWNER_ID:
        return True, 'Owner bypass'
    ext = os.path.splitext(filename)[1].lower()
    if ext in _DANGEROUS_EXTENSIONS:
        return False, f'Blocked extension: {ext}'
    for sig, label in _BINARY_SIGNATURES:
        if content.startswith(sig):
            return False, f'Binary detected: {label}'
    ent = _entropy(content[:8192])
    if ent > 7.5 and ext != '.zip':
        return False, f'Suspicious entropy ({ent:.2f}) — possible packed/encrypted payload'
    return True, 'OK'

# ─── safe zip extraction ─────────────────────────────────────────────────────
_SKIP_DIRS  = {'__MACOSX', '__pycache__', 'node_modules', '.git', 'venv', '.venv', '.deps'}
_SKIP_FILES = {'.DS_Store', 'Thumbs.db'}

def _safe_part(part: str) -> str:
    part = _clean_chars(part, extra='._- ').strip()[:100]
    if part in ('', '.', '..'):
        raise UploadError('The zip contains an invalid file name.')
    return part

def extract_zip_safe(data: bytes, dest: str, uid: int) -> None:
    """Unpack a zip into dest, refusing traversal, symlinks, bombs and binaries."""
    cap = MAX_UNZIP_MB * 1024 * 1024
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise UploadError('That is not a valid zip file.')
    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_ZIP_FILES:
            raise UploadError(f'The zip has too many files (max {MAX_ZIP_FILES}).')
        if sum(i.file_size for i in infos) > cap:
            raise UploadError(f'The zip unpacks to more than {MAX_UNZIP_MB} MB.')
        root = os.path.abspath(dest)
        total = written = 0
        for info in infos:
            if info.flag_bits & 0x1:
                raise UploadError('Password-protected zips are not supported.')
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise UploadError('The zip contains a symbolic link — blocked.')
            raw = info.filename.replace('\\', '/')
            if raw.startswith('/') or re.match(r'^[A-Za-z]:', raw):
                raise UploadError('The zip contains an absolute path — blocked.')
            parts = [p for p in raw.split('/') if p not in ('', '.')]
            if '..' in parts:
                raise UploadError('Zip path traversal blocked.')
            if not parts or parts[-1] in _SKIP_FILES or any(p in _SKIP_DIRS for p in parts[:-1]):
                continue
            parts = [_safe_part(p) for p in parts]
            target = os.path.abspath(os.path.join(root, *parts))
            if not target.startswith(root + os.sep):
                raise UploadError('Zip path traversal blocked.')
            ext = os.path.splitext(parts[-1])[1].lower()
            if uid != OWNER_ID and ext in _DANGEROUS_EXTENSIONS:
                raise UploadError(f'Blocked file type inside the zip: {ext}')
            os.makedirs(os.path.dirname(target), exist_ok=True)
            try:
                with zf.open(info) as src, open(target, 'wb') as out:
                    first = True
                    while True:
                        chunk = src.read(65536)
                        if not chunk:
                            break
                        if first and uid != OWNER_ID:
                            for sig, label in _BINARY_SIGNATURES:
                                if chunk.startswith(sig):
                                    raise UploadError(f'Binary inside the zip ({label}) — blocked.')
                        first = False
                        total += len(chunk)
                        if total > cap:       # never trust the sizes in the zip header
                            raise UploadError(f'The zip unpacks to more than {MAX_UNZIP_MB} MB.')
                        out.write(chunk)
            except UploadError:
                raise
            except Exception:
                raise UploadError('Could not read the zip — it looks corrupted.')
            written += 1
        if not written:
            raise UploadError('The zip is empty.')

def _flatten_single_dir(dest: str) -> None:
    """If everything sits inside one top-level folder, lift it up a level."""
    while True:
        items = os.listdir(dest)
        if len(items) != 1 or not os.path.isdir(os.path.join(dest, items[0])):
            return
        tmp = dest + '_flat'
        os.rename(os.path.join(dest, items[0]), tmp)
        os.rmdir(dest)
        os.rename(tmp, dest)

def find_main(root: str):
    """Pick the entry point of an unpacked project → (file name, 'py' | 'js')."""
    items = [f for f in os.listdir(root) if os.path.isfile(os.path.join(root, f))]
    for pref in ('main.py', 'bot.py', 'app.py', 'run.py', 'start.py'):
        if pref in items:
            return pref, 'py'
    if 'package.json' in items:
        try:
            with open(os.path.join(root, 'package.json'), encoding='utf-8') as f:
                pkg = json.load(f)
            cand = str(pkg.get('main') or '')
            m = re.match(r'^\s*node\s+([\w.\-/]+\.js)\s*$', str((pkg.get('scripts') or {}).get('start', '')))
            if m:
                cand = m.group(1)
            if (cand and '..' not in cand and not os.path.isabs(cand)
                    and os.path.isfile(os.path.join(root, cand))):
                return cand.replace('\\', '/').lstrip('./'), 'js'
        except (OSError, ValueError, AttributeError):
            pass
    for pref in ('index.js', 'main.js', 'bot.js', 'app.js', 'server.js'):
        if pref in items:
            return pref, 'js'
    py = sorted(f for f in items if f.endswith('.py'))
    js = sorted(f for f in items if f.endswith('.js'))
    if py:
        return py[0], 'py'
    if js:
        return js[0], 'js'
    return None, None

def vet_tree(root: str, uid: int) -> None:
    """Apply the zip rules to a folder that is already on disk (a Git clone):
    drop junk folders and links, refuse binaries and anything too large."""
    cap = MAX_UNZIP_MB * 1024 * 1024
    total = count = 0
    for cur, dirs, files in os.walk(root):
        for d in list(dirs):
            full = os.path.join(cur, d)
            if d in _SKIP_DIRS or os.path.islink(full):
                dirs.remove(d)
                (os.unlink if os.path.islink(full) else shutil.rmtree)(full)
        for fn in files:
            full = os.path.join(cur, fn)
            if os.path.islink(full) or fn in _SKIP_FILES:
                os.unlink(full)
                continue
            count += 1
            total += os.path.getsize(full)
            if count > MAX_ZIP_FILES:
                raise UploadError(f'The repository has too many files (max {MAX_ZIP_FILES}).')
            if total > cap:
                raise UploadError(f'The repository is larger than {MAX_UNZIP_MB} MB.')
            if uid == OWNER_ID:
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext in _DANGEROUS_EXTENSIONS:
                raise UploadError(f'Blocked file type in the repository: {ext}')
            with open(full, 'rb') as f:
                head = f.read(8)
            for sig, label in _BINARY_SIGNATURES:
                if head.startswith(sig):
                    raise UploadError(f'Binary in the repository ({label}) — blocked.')
    if not count:
        raise UploadError('The repository is empty.')

def zip_tree(root: str) -> bytes:
    """Zip a project folder (without packages and caches) into memory."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for cur, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
            for fn in sorted(files):
                full = os.path.join(cur, fn)
                if os.path.isfile(full) and not os.path.islink(full):
                    zf.write(full, os.path.relpath(full, root))
    return buf.getvalue()

_GIT_URL = re.compile(r'^https://([A-Za-z0-9.-]+)/((?:[\w.-]+/){1,4}[\w.-]+?)(?:\.git)?/?$')

def parse_git_url(url: str):
    """Check a repository link → (clean https URL, repository name)."""
    m = _GIT_URL.match((url or '').strip())
    if not m or '..' in m.group(2):
        raise UploadError('That is not a repository link. Use the form '
                          'https://github.com/user/repository')
    host, path = m.group(1).lower(), m.group(2)
    if host not in [h.lower() for h in GIT_ALLOWED_HOSTS]:
        raise UploadError('Only these sites are allowed: ' + ', '.join(GIT_ALLOWED_HOSTS))
    return f'https://{host}/{path}.git', path.split('/')[-1]

def _git_clone(url: str, dest: str) -> None:
    """Shallow clone without this server's git settings or saved logins."""
    home = dest + '_home'
    os.makedirs(home, exist_ok=True)
    env = {k: os.environ[k] for k in _PASS_ENV + ('GIT_SSL_CAINFO',) if k in os.environ}
    env.update(HOME=home, GIT_TERMINAL_PROMPT='0', GIT_LFS_SKIP_SMUDGE='1',
               GIT_CONFIG_NOSYSTEM='1', GIT_ASKPASS='true')
    try:
        r = subprocess.run(
            [shutil.which('git') or 'git', '-c', 'core.symlinks=false', '-c', 'credential.helper=',
             '-c', 'protocol.file.allow=never', 'clone', '--depth', '1', '--single-branch',
             '--no-tags', '--no-recurse-submodules', url, dest],
            capture_output=True, timeout=GIT_TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        raise UploadError(f'Cloning took longer than {GIT_TIMEOUT}s — is the repository very large?')
    finally:
        shutil.rmtree(home, ignore_errors=True)
    if r.returncode != 0:
        err = r.stderr.decode('utf-8', errors='ignore').strip().splitlines()
        raise UploadError('Could not clone the repository (is it public?): '
                          + (err[-1][:200] if err else 'unknown error'))

def stage_git(uid: int, url: str) -> dict:
    """Clone a public repository into data/pending/ and vet it like a zip."""
    clean, repo = parse_git_url(url)
    if not shutil.which('git'):
        raise UploadError('Git is not installed on this server.')
    stage = os.path.join(PENDING_DIR, uuid.uuid4().hex[:16])
    try:
        _git_clone(clean, stage)
        shutil.rmtree(os.path.join(stage, '.git'), ignore_errors=True)
        vet_tree(stage, uid)
        main, ftype = find_main(stage)
        if not main:
            raise UploadError('No .py or .js file was found at the top level of the repository.')
        return {'stage_dir': stage, 'main_file': main, 'file_type': ftype,
                'name': safe_name(repo + '.git'), 'source': clean}
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise

def _text_lines(path):
    """Lines of a text file, or None when it is missing, binary or very large."""
    try:
        if os.path.getsize(path) > 512 * 1024:
            return None
        with open(path, 'rb') as f:
            raw = f.read()
    except OSError:
        return None
    if b'\x00' in raw:
        return None
    return raw.decode('utf-8', errors='replace').splitlines()

def _diffable(root) -> dict:
    out = {}
    for cur, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fn in files:
            if fn.lower().endswith(_CODE_EXT) or fn in ('requirements.txt', 'package.json'):
                full = os.path.join(cur, fn)
                out[os.path.relpath(full, root).replace(os.sep, '/')] = full
    return out

def diff_files(old_path, new_path, rel):
    """(lines added, lines removed, unified diff text) for one file."""
    old, new = _text_lines(old_path) if old_path else [], _text_lines(new_path) if new_path else []
    if old is None or new is None:
        return 0, 0, f'--- {rel}\n(binary or very large file — not shown)\n'
    lines = list(difflib.unified_diff(old, new, f'approved/{rel}', f'new/{rel}', lineterm='', n=3))
    plus = sum(1 for ln in lines if ln.startswith('+') and not ln.startswith('+++'))
    minus = sum(1 for ln in lines if ln.startswith('-') and not ln.startswith('---'))
    return plus, minus, '\n'.join(lines) + ('\n' if lines else '')

def diff_trees(old_root, new_root):
    """Compare the approved project with a new upload → (summary dict, diff text)."""
    old, new = _diffable(old_root), _diffable(new_root)
    summary = {'changed': 0, 'added': 0, 'removed': 0, 'plus': 0, 'minus': 0}
    parts = []
    for rel in sorted(set(old) | set(new)):
        plus, minus, text = diff_files(old.get(rel), new.get(rel), rel)
        if not text:
            continue
        key = 'changed' if rel in old and rel in new else ('added' if rel in new else 'removed')
        summary[key] += 1
        summary['plus'] += plus
        summary['minus'] += minus
        parts.append(text)
    return summary, '\n'.join(parts)[:400 * 1024]

def stage_upload(uid: int, name: str, data: bytes) -> dict:
    """Write an upload into data/pending/ — never into a live project folder."""
    stage = os.path.join(PENDING_DIR, uuid.uuid4().hex[:16])
    os.makedirs(stage)
    try:
        if name.endswith('.zip'):
            extract_zip_safe(data, stage, uid)
            _flatten_single_dir(stage)
            main, ftype = find_main(stage)
            if not main:
                raise UploadError('No .py or .js file was found at the top level of the zip.')
        else:
            main, ftype = name, name.rsplit('.', 1)[-1]
            with open(os.path.join(stage, name), 'wb') as f:
                f.write(data)
        return {'stage_dir': stage, 'main_file': main, 'file_type': ftype}
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise

# ─── static report shown to admins ───────────────────────────────────────────
_RISKY_PY_CALLS   = {'eval', 'exec', 'compile', '__import__'}
_RISKY_PY_ATTRS   = {'os.system', 'os.popen', 'os.kill', 'os.fork', 'os.remove',
                     'shutil.rmtree', 'pickle.loads', 'marshal.loads', 'base64.b64decode'}
_RISKY_PY_MODULES = {'subprocess', 'socket', 'ctypes', 'pickle', 'marshal', 'pty',
                     'importlib', 'multiprocessing'}
_RISKY_TEXT = [
    (re.compile(r'hosting\.py|bot_data\.db|bot_main\.log'),            "reads the host bot's files"),
    (re.compile(r'/etc/(passwd|shadow)|\.ssh/|/proc/|/root/'),         'system paths'),
    (re.compile(r'rm\s+-rf|mkfs|:\(\)\s*\{'),                          'destructive shell command'),
    (re.compile(r'(curl|wget)[^\n]{0,80}\|\s*(ba)?sh'),                'download-and-run'),
    (re.compile(r'[A-Za-z0-9+/=]{400,}'),                              'large encoded blob'),
]
_RISKY_JS = [
    (re.compile(r'''require\(\s*['"](node:)?child_process['"]'''),      'child_process'),
    (re.compile(r'''require\(\s*['"](node:)?(net|dgram|vm|cluster)['"]'''), 'net/vm module'),
    (re.compile(r'\beval\s*\('),                                        'eval()'),
    (re.compile(r'new\s+Function\s*\('),                                'new Function()'),
    (re.compile(r'process\.env'),                                       'process.env'),
]
_JS_IMPORT = re.compile(r'''(?:require\(\s*|from\s+|import\s+)['"]([^'"./][^'"]*)['"]''')

def _analyze_py(src: str, imports: set, flags: Counter, notes: set):
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        notes.add(f'syntax error (line {e.lineno})')
        return
    except Exception:
        notes.add('could not be parsed')
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(a.name.split('.')[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                imports.add(node.module.split('.')[0])
        elif isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id in _RISKY_PY_CALLS:
                flags[f.id + '()'] += 1
            elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                full = f'{f.value.id}.{f.attr}'
                if (full in _RISKY_PY_ATTRS or f.value.id == 'subprocess'
                        or (f.value.id == 'os' and f.attr.startswith(('exec', 'spawn')))):
                    flags[full + '()'] += 1

def analyze_tree(root: str) -> dict:
    """Read-only summary of a staged upload: size, imports, risky constructs."""
    imports, flags, notes = set(), Counter(), set()
    n_files = size = analyzed = 0
    for cur, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fn in files:
            path = os.path.join(cur, fn)
            try:
                fsize = os.path.getsize(path)
            except OSError:
                continue
            n_files += 1
            size += fsize
            ext = os.path.splitext(fn)[1].lower()
            if ext not in ('.py', '.js') or fsize > 512 * 1024 or analyzed >= 60:
                if ext in ('.py', '.js'):
                    notes.add('some code files were too large/many to scan')
                continue
            analyzed += 1
            try:
                with open(path, encoding='utf-8', errors='ignore') as f:
                    src = f.read()
            except OSError:
                continue
            before = set(imports)
            if ext == '.py':
                _analyze_py(src, imports, flags, notes)
                for mod in (imports - before) & _RISKY_PY_MODULES:
                    flags['import ' + mod] += 1
            else:
                imports.update(m.split('/')[0] if not m.startswith('@') else m
                               for m in _JS_IMPORT.findall(src))
                for rx, label in _RISKY_JS:
                    hits = len(rx.findall(src))
                    if hits:
                        flags[label] += hits
            for rx, label in _RISKY_TEXT:
                if rx.search(src):
                    flags[label] += 1
    req = os.path.join(root, 'requirements.txt')
    if os.path.isfile(req):
        try:
            with open(req, encoding='utf-8', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and not re.match(
                            r'^[A-Za-z0-9][\w.\-]*(\[[\w,\- ]+\])?\s*([<>=!~]=?\s*[\w.*]+\s*,?\s*)*$', line):
                        flags['unusual line in requirements.txt'] += 1
        except OSError:
            pass
    requires = []
    try:
        if os.path.isfile(req):
            with open(req, encoding='utf-8', errors='ignore') as f:
                for line in f:
                    m = re.match(r'^\s*([A-Za-z0-9][\w.\-]*)', line)
                    if m and not line.lstrip().startswith('#'):
                        requires.append(m.group(1))
        pkg_json = os.path.join(root, 'package.json')
        if os.path.isfile(pkg_json):
            with open(pkg_json, encoding='utf-8', errors='ignore') as f:
                requires += [str(k) for k in (json.load(f).get('dependencies') or {})]
    except (OSError, ValueError, AttributeError):
        pass
    stdlib = getattr(sys, 'stdlib_module_names', frozenset())
    return {'n_files': n_files, 'size': size, 'requires': requires[:40],
            'imports': sorted(i for i in imports if i not in stdlib),
            'flags': dict(flags.most_common()), 'notes': sorted(notes)}

# ════════════════════════════════════════════════════════════════════════════
#  MESSAGING HELPERS
# ════════════════════════════════════════════════════════════════════════════
def btn(text, data):
    return types.InlineKeyboardButton(text, callback_data=data)

def url_btn(text, url):
    return types.InlineKeyboardButton(text, url=url)

def kb(*rows):
    """Build an inline keyboard from rows of buttons (empty rows are skipped)."""
    markup = types.InlineKeyboardMarkup()
    for row in rows:
        row = [b for b in (row or []) if b]
        if row:
            markup.row(*row)
    return markup

def notify(chat_id, text, markup=None):
    """Send a message; failures (user blocked the bot, …) are logged, not raised."""
    try:
        return bot.send_message(chat_id, text, reply_markup=markup)
    except Exception as e:
        logger.info('Could not message %s: %s', chat_id, e)
        return None

class Ctx:
    """Where a screen is drawn: an existing message is edited in place
    (button presses); otherwise one message is sent and then reused."""

    def __init__(self, chat_id, message_id=None, is_text=True):
        self.chat_id, self.message_id, self.is_text = chat_id, message_id, is_text

    @classmethod
    def of(cls, call):
        m = call.message
        return cls(m.chat.id, m.message_id, getattr(m, 'content_type', 'text') == 'text')

    def draw(self, text, markup=None) -> bool:
        if self.message_id is not None:
            try:
                if self.is_text:
                    bot.edit_message_text(text, self.chat_id, self.message_id, reply_markup=markup)
                else:
                    bot.edit_message_caption(text, self.chat_id, self.message_id, reply_markup=markup)
                return True
            except ApiTelegramException as e:
                desc = str(getattr(e, 'description', e)).lower()
                if 'not modified' in desc:
                    return True
                if getattr(e, 'error_code', 0) == 429:
                    return False
                logger.info('Edit failed (%s) — sending a new message instead.', desc)
            except Exception as e:
                logger.warning('Edit failed: %s', e)
        msg = notify(self.chat_id, text, markup)
        if msg is None:
            return False
        self.message_id, self.is_text = msg.message_id, True
        return True

# ════════════════════════════════════════════════════════════════════════════
#  SANDBOX BACKEND
# ════════════════════════════════════════════════════════════════════════════
_backend = None

def _docker_works() -> bool:
    if not shutil.which('docker'):
        return False
    try:
        return subprocess.run(['docker', 'info'], capture_output=True, timeout=15).returncode == 0
    except Exception:
        return False

def _under(path, parents) -> bool:
    return any(path == par or path.startswith(par + os.sep) for par in parents)

def _bwrap_prefix(pdir) -> list:
    """bubblewrap arguments: the whole system is read-only, the bot's own folder
    and the home directory are hidden, only this project's folder is writable."""
    args = [shutil.which('bwrap') or 'bwrap', '--die-with-parent',
            '--unshare-pid', '--unshare-ipc', '--unshare-uts',
            '--ro-bind', '/', '/', '--dev', '/dev', '--proc', '/proc', '--tmpfs', '/tmp']
    hidden = []
    for path in (BASE_DIR, os.path.expanduser('~')):
        path = os.path.realpath(path)
        if path != os.sep and os.path.isdir(path) and not _under(path, hidden):
            hidden.append(path)
            args += ['--tmpfs', path]
    # what scripts still need from inside a hidden folder: the interpreters and their packages
    needed = {os.path.realpath(sys.prefix), os.path.realpath(sys.base_prefix)}
    try:
        import site
        needed.add(os.path.realpath(site.getusersitepackages()))
    except Exception:
        pass
    node = shutil.which('node')
    if node:
        needed.add(os.path.dirname(os.path.dirname(os.path.realpath(node))))
    for path in sorted(needed):
        if os.path.isdir(path) and _under(path, hidden) and path not in hidden:
            args += ['--ro-bind', path, path]
    return args + ['--bind', pdir, pdir, '--chdir', pdir]

def _bwrap_works() -> bool:
    """Run a tiny script inside bubblewrap and check that it starts, can write its
    own folder and can NOT see this bot's files. Only then is bubblewrap used."""
    if os.name != 'posix' or not shutil.which('bwrap'):
        return False
    probe = os.path.join(PROJECTS_DIR, '.sandbox_test')
    code = ("import os\n"
            "open('ok.txt', 'w').write('x')\n"
            f"leak = os.path.exists({os.path.abspath(__file__)!r}) or os.path.exists({DATABASE_PATH!r})\n"
            "print('LEAK' if leak else 'SAFE')\n")
    try:
        os.makedirs(probe, exist_ok=True)
        r = subprocess.run(_bwrap_prefix(probe) + [sys.executable, '-c', code], capture_output=True,
                           timeout=30, text=True, env=_child_env(probe, backend='bwrap'))
        ok = r.returncode == 0 and r.stdout.strip() == 'SAFE' and os.path.isfile(os.path.join(probe, 'ok.txt'))
        if not ok:
            logger.warning('bubblewrap is installed but its self-test failed (%s) — not using it.',
                           (r.stderr or r.stdout).strip()[-200:])
        return ok
    except Exception as e:
        logger.warning('bubblewrap self-test error: %s', e)
        return False
    finally:
        shutil.rmtree(probe, ignore_errors=True)

def _is_local(backend) -> bool:
    """True when scripts are our own child processes (not Docker containers)."""
    return backend in ('process', 'bwrap')

def sandbox_backend() -> str:
    """'docker', 'bwrap', 'process' (no isolation) or 'unavailable' (the SANDBOX
    setting demands a sandbox this server does not have)."""
    global _backend
    if _backend is None:
        if SANDBOX in ('auto', 'docker') and _docker_works():
            _backend = 'docker'
        elif SANDBOX in ('auto', 'bwrap') and _bwrap_works():
            _backend = 'bwrap'
        elif SANDBOX in ('docker', 'bwrap'):
            _backend = 'unavailable'
        else:
            _backend = 'process'
        if _backend == 'process':
            logger.warning('Sandbox: plain processes. Uploaded scripts are NOT isolated from this '
                           'machine — install Docker or bubblewrap for real isolation.')
        else:
            logger.info('Sandbox backend: %s', _backend)
            if _backend == 'bwrap' and hasattr(os, 'getuid') and os.getuid() == 0:
                logger.warning('The bot runs as root: sandboxed scripts can still READ system files. '
                               'Run the bot as a normal user for stronger isolation.')
    return _backend

def sandbox_label() -> str:
    return {'docker': '🛡 Docker container',
            'bwrap': '🛡 bubblewrap sandbox',
            'process': '⚠️ none (plain process — install Docker or bubblewrap)',
            'unavailable': f'❌ "{SANDBOX}" required but not available'}[sandbox_backend()]

def _container(pid) -> str:
    return f'hostbot_{int(pid)}'

_PASS_ENV = ('PATH', 'LANG', 'LC_ALL', 'TZ', 'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY',
             'http_proxy', 'https_proxy', 'no_proxy', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
             'REQUESTS_CA_BUNDLE', 'NODE_EXTRA_CA_CERTS', 'PIP_CERT', 'PIP_INDEX_URL',
             'SYSTEMROOT', 'SystemRoot', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP', 'WINDIR',
             'APPDATA', 'LOCALAPPDATA', 'USERPROFILE', 'ProgramData', 'PREFIX', 'LD_LIBRARY_PATH')

def _child_env(pdir, pid=None, backend='process') -> dict:
    """A clean environment — the bot's own variables are not passed to scripts.
    With `pid`, the variables the user set for that file are added."""
    env = {k: os.environ[k] for k in _PASS_ENV if k in os.environ}
    if pid is not None:
        env.update(project_env(pid))
    env.update(HOME=pdir, PYTHONUNBUFFERED='1', PYTHONIOENCODING='utf-8',
               PYTHONPATH=os.path.join(pdir, '.deps'),
               npm_config_cache='/tmp/.npm' if backend == 'bwrap' else os.path.join(DATA_DIR, 'npm_cache'))
    return env

_ENV_KEY = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,63}$')
_ENV_RESERVED = {'PATH', 'HOME', 'PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'PYTHONUNBUFFERED',
                 'PYTHONIOENCODING', 'LD_PRELOAD', 'LD_LIBRARY_PATH', 'NODE_OPTIONS', 'NODE_PATH',
                 'SHELL', 'USER', 'TMPDIR'}

def valid_env_key(key) -> bool:
    return bool(_ENV_KEY.match(key)) and key.upper() not in _ENV_RESERVED \
        and not key.lower().startswith('npm_config')

def project_env(pid) -> dict:
    """Variables the owner set for a hosted file (🔑 Variables)."""
    return {r['key']: r['value'] or '' for r in
            q('SELECT key, value FROM project_env WHERE project_id=? ORDER BY key', (pid,))}

def _script_args(proj) -> list:
    try:
        return shlex.split(proj['args'] or '', posix=os.name != 'nt')
    except ValueError:
        return []

def _docker_image(proj) -> str:
    ver = proj['runtime'] or ''
    if proj['file_type'] == 'py':
        return f'python:{ver}-slim' if ver in PY_VERSIONS else DOCKER_PY_IMAGE
    return f'node:{ver}-slim' if ver in NODE_VERSIONS else DOCKER_JS_IMAGE

_net_ready = None

def _docker_network_args() -> list:
    """A private bridge network on which containers cannot talk to each other."""
    global _net_ready
    if not DOCKER_NETWORK:
        return []
    if _net_ready is None:
        try:
            ok = subprocess.run(['docker', 'network', 'inspect', DOCKER_NETWORK],
                                capture_output=True, timeout=20).returncode == 0
            if not ok:
                ok = subprocess.run(['docker', 'network', 'create', '--driver', 'bridge', '-o',
                                     'com.docker.network.bridge.enable_icc=false', DOCKER_NETWORK],
                                    capture_output=True, timeout=30).returncode == 0
            _net_ready = ok
        except Exception:
            _net_ready = False
        if not _net_ready:
            logger.warning('Could not create the Docker network %r — using the default network.', DOCKER_NETWORK)
    return ['--network', DOCKER_NETWORK] if _net_ready else []

def _docker_mounts(pdir) -> list:
    args = ['-v', f'{pdir}:/app', '-w', '/app', '-e', 'HOME=/tmp',
            '-e', 'PYTHONUNBUFFERED=1', '-e', 'PYTHONPATH=/app/.deps',
            '-e', 'npm_config_cache=/tmp/.npm']
    if hasattr(os, 'getuid'):       # files created inside stay owned by the bot's user
        args += ['--user', f'{os.getuid()}:{os.getgid()}']
    return args

def _run_command(proj, backend):
    pid, main = proj['id'], proj['main_file']
    pdir = project_dir(pid)
    lim = limits_for(proj['user_id'])
    extra = _script_args(proj)
    if backend == 'docker':
        inner = (['python', '-u', main] if proj['file_type'] == 'py' else ['node', main]) + extra
        limits = ['--cpus', str(lim['cpus']), '--pids-limit', str(MAX_PIDS),
                  '--security-opt', 'no-new-privileges', '--cap-drop', 'ALL']
        if lim['ram']:
            limits += ['--memory', f"{lim['ram']}m", '--memory-swap', f"{lim['ram']}m"]
        # user variables are passed by name; their values travel in the docker client's environment
        env_flags = [part for key in project_env(pid) for part in ('-e', key)]
        return ['docker', 'run', '--rm', '-i', '--name', _container(pid), *limits,
                *_docker_network_args(), *_docker_mounts(pdir), *env_flags, _docker_image(proj), *inner]
    if proj['file_type'] == 'py':
        inner = [sys.executable, '-u', main] + extra
    else:
        inner = [shutil.which('node') or 'node', main] + extra
    return _bwrap_prefix(pdir) + inner if backend == 'bwrap' else inner

# ════════════════════════════════════════════════════════════════════════════
#  PACKAGE INSTALLATION  (only ever runs for approved projects)
# ════════════════════════════════════════════════════════════════════════════
PIP_NAMES = {
    'telebot': 'pyTelegramBotAPI', 'telegram': 'python-telegram-bot',
    'aiogram': 'aiogram', 'pyrogram': 'pyrogram', 'telethon': 'telethon',
    'bs4': 'beautifulsoup4', 'requests': 'requests', 'pil': 'Pillow',
    'cv2': 'opencv-python-headless', 'yaml': 'PyYAML', 'dotenv': 'python-dotenv',
    'pandas': 'pandas', 'numpy': 'numpy', 'flask': 'Flask', 'django': 'Django',
    'sqlalchemy': 'SQLAlchemy', 'psutil': 'psutil', 'aiohttp': 'aiohttp',
    'httpx': 'httpx', 'pydantic': 'pydantic', 'discord': 'discord.py',
    'pymongo': 'pymongo', 'bson': 'pymongo', 'redis': 'redis', 'pytz': 'pytz',
    'dateutil': 'python-dateutil', 'crypto': 'pycryptodome', 'jwt': 'PyJWT',
    'googleapiclient': 'google-api-python-client', 'openai': 'openai',
    'sklearn': 'scikit-learn', 'aiofiles': 'aiofiles', 'tgcrypto': 'TgCrypto',
}
_PY_MISSING = re.compile(r"ModuleNotFoundError: No module named '([^']+)'")
_JS_MISSING = re.compile(r"Cannot find (?:module|package) '([^']+)'")
_PKG_OK     = re.compile(r'^(@[A-Za-z0-9][\w.\-]*/)?[A-Za-z0-9][\w.\-]*$')

def missing_package(output: str, proj):
    """Name of the package a failed start is asking for, or None."""
    pdir = project_dir(proj['id'])
    if proj['file_type'] == 'py':
        found = _PY_MISSING.findall(output)
        if not found:
            return None
        mod = found[-1].split('.')[0]
        if mod in getattr(sys, 'stdlib_module_names', ()):
            return None
        if os.path.exists(os.path.join(pdir, mod + '.py')) or os.path.isdir(os.path.join(pdir, mod)):
            return None                      # a local module that failed, not a package
        pkg = PIP_NAMES.get(mod.lower()) or mod
    else:
        found = _JS_MISSING.findall(output)
        if not found:
            return None
        mod = found[-1]
        if mod.startswith(('.', '/', 'node:')) or re.match(r'^[A-Za-z]:', mod):
            return None
        parts = mod.split('/')
        pkg = '/'.join(parts[:2]) if mod.startswith('@') else parts[0]
    return pkg if _PKG_OK.match(pkg) else None

NPM_NAMES = {
    'telegraf', 'node-telegram-bot-api', 'grammy', 'axios', 'express', 'dotenv', 'mongoose',
    'mongodb', 'discord.js', 'node-fetch', 'ws', 'moment', 'dayjs', 'cheerio', 'redis', 'ioredis',
    'uuid', 'lodash', 'sqlite3', 'better-sqlite3', 'pg', 'mysql2', 'cors', 'body-parser',
    'socket.io', 'openai', 'form-data', 'chalk', 'winston', 'node-cron',
}

def package_allowed(lang, pkg) -> bool:
    """May this package be installed automatically, without asking an admin?"""
    if AUTO_INSTALL_UNKNOWN:
        return True
    known = {v.lower() for v in PIP_NAMES.values()} if lang == 'py' else NPM_NAMES
    return pkg.lower() in known or q1('SELECT 1 FROM allowed_packages WHERE lang=? AND name=?',
                                      (lang, pkg.lower())) is not None

def request_package(proj, pkg):
    """A script needs a package that is not on the allowed list: ask the reviewers."""
    pid, lang = proj['id'], proj['file_type']
    name = esc(proj['name'])
    x("UPDATE projects SET should_run=0, last_reason='needs package approval' WHERE id=?", (pid,))
    if not q1("SELECT 1 FROM pkg_requests WHERE project_id=? AND pkg=? AND status='pending'", (pid, pkg)):
        rid = x('INSERT INTO pkg_requests (project_id, lang, pkg, ts) VALUES (?,?,?,?)',
                (pid, lang, pkg, time.time())).lastrowid
        for staff in staff_ids():
            notify(staff, f"📦 <b>Package request #{rid}</b>\n\n<b>{name}</b> of user "
                          f"<code>{proj['user_id']}</code> needs <code>{esc(pkg)}</code> "
                          f"({'pip' if lang == 'py' else 'npm'}), which is not on the allowed list.",
                   kb([btn('✅ Allow + install', f'pky:{rid}'), btn('❌ Deny', f'pkn:{rid}')]))
    notify(proj['user_id'], f'📦 <b>{name}</b> needs the package <code>{esc(pkg)}</code>, which is not on '
                            'the allowed list yet. An admin was asked — it starts by itself once allowed.',
           _open_btn(pid))

def _install_and_start(pid, pkg):
    proj = get_project(pid)
    if not proj:
        return
    owner, name = proj['user_id'], esc(proj['name'])
    ok, out = install_packages(proj, [pkg])
    if not ok:
        x("UPDATE projects SET should_run=0, last_reason='install failed' WHERE id=?", (pid,))
        notify(owner, f'❌ Could not install <code>{esc(pkg)}</code> for <b>{name}</b>:\n'
                      f'<pre>{esc(out[-500:])}</pre>', _open_btn(pid))
        return
    ok, msg = start_project(pid)
    notify(owner, f'✅ <code>{esc(pkg)}</code> was allowed and installed — <b>{name}</b> '
                  + ('is starting.' if ok else f'could not start: {esc(msg)}'), _open_btn(pid))

def decide_package(rid, admin, allow):
    """Allow or deny a package request. Returns (ok, text)."""
    status = 'allowed' if allow else 'denied'
    if x("UPDATE pkg_requests SET status=? WHERE id=? AND status='pending'", (status, rid)).rowcount != 1:
        return False, 'Already handled.'
    r = q1('SELECT * FROM pkg_requests WHERE id=?', (rid,))
    lang, pkg = r['lang'], r['pkg']
    audit(admin.id, f'package {status}', pkg, f'request #{rid}')
    if allow:
        x('INSERT OR IGNORE INTO allowed_packages (lang, name) VALUES (?,?)', (lang, pkg.lower()))
        waiting = [r] + q("SELECT * FROM pkg_requests WHERE lang=? AND pkg=? AND status='pending'", (lang, pkg))
        x("UPDATE pkg_requests SET status='allowed' WHERE lang=? AND pkg=? AND status='pending'", (lang, pkg))
        for req in waiting:
            threading.Thread(target=_install_and_start, args=(req['project_id'], pkg), daemon=True).start()
        return True, f'✅ <code>{esc(pkg)}</code> is now allowed and is being installed.'
    proj = get_project(r['project_id'])
    if proj:
        x("UPDATE projects SET last_reason='package denied' WHERE id=?", (proj['id'],))
        notify(proj['user_id'], f"❌ The package <code>{esc(pkg)}</code> needed by <b>{esc(proj['name'])}</b> "
                                'was not allowed by an admin.', _open_btn(proj['id']))
    return True, f'❌ <code>{esc(pkg)}</code> was not allowed.'

_install_slots = None

def _slots():
    global _install_slots
    if _install_slots is None:
        _install_slots = threading.BoundedSemaphore(max(1, int(MAX_PARALLEL_INSTALLS)))
    return _install_slots

def _install_command(proj, packages, backend):
    pdir = project_dir(proj['id'])
    if proj['file_type'] == 'py':
        if packages is None:
            if not os.path.isfile(os.path.join(pdir, 'requirements.txt')):
                return None
            what = ['-r', 'requirements.txt']
        else:
            what = list(packages)
        pip = ['pip'] if backend == 'docker' else [sys.executable, '-m', 'pip']
        inner = pip + ['install', '--disable-pip-version-check', '--no-input', '--no-cache-dir',
                       '-q', '--upgrade', '--target', '.deps'] + what
    else:
        if packages is None and not os.path.isfile(os.path.join(pdir, 'package.json')):
            return None
        npm = 'npm' if backend == 'docker' else (shutil.which('npm') or 'npm')
        inner = [npm, 'install', '--no-audit', '--no-fund', '--omit=dev']
        if NPM_IGNORE_SCRIPTS:
            inner.append('--ignore-scripts')
        inner += list(packages or [])
    if backend == 'docker':
        return ['docker', 'run', '--rm', *_docker_mounts(pdir), _docker_image(proj), *inner]
    return _bwrap_prefix(pdir) + inner if backend == 'bwrap' else inner

def install_packages(proj, packages=None):
    """Install requirements.txt / package.json (packages=None) or the named
    packages into the project's own folder. Returns (ok, output tail).
    Only MAX_PARALLEL_INSTALLS installs run at once; the rest wait their turn."""
    backend = sandbox_backend()
    cmd = _install_command(proj, packages, backend)
    if cmd is None:
        return True, ''
    pid, pdir = proj['id'], project_dir(proj['id'])
    INSTALL_QUEUE.add(pid)
    with _slots():
        INSTALL_QUEUE.discard(pid)
        INSTALLING.add(pid)
        try:
            r = subprocess.run(cmd, cwd=pdir, capture_output=True, timeout=INSTALL_TIMEOUT,
                               env=None if backend == 'docker' else _child_env(pdir, backend=backend))
            out = (r.stdout + b'\n' + r.stderr).decode('utf-8', errors='ignore').strip()
            return r.returncode == 0, out[-700:]
        except subprocess.TimeoutExpired:
            return False, f'Timed out after {INSTALL_TIMEOUT}s.'
        except FileNotFoundError as e:
            return False, f'{e.filename or "installer"} is not installed on this server.'
        except Exception as e:
            logger.exception('Install failed for project %s', pid)
            return False, str(e)
        finally:
            INSTALLING.discard(pid)

# ════════════════════════════════════════════════════════════════════════════
#  SCRIPT LOGS
# ════════════════════════════════════════════════════════════════════════════
_ANSI = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]')
_CTRL = re.compile(r'[\x00-\x08\x0b-\x1f\x7f]')

def _clean_log(raw: bytes) -> str:
    text = raw.decode('utf-8', errors='ignore').replace('\r\n', '\n').replace('\r', '\n')
    return _CTRL.sub('', _ANSI.sub('', text))

def tail_log(pid, chars=2800, since=None) -> str:
    """Last `chars` characters of a script's log (optionally only what was
    written after byte offset `since`, i.e. during the current run)."""
    path = log_path(pid)
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as f:
            start = max(0, size - chars * 4)
            if since is not None and since <= size:
                start = max(start, since)
            f.seek(start)
            text = _clean_log(f.read())
    except OSError:
        return ''
    if len(text) > chars:
        text = text[-chars:]
        cut = text.find('\n')
        if 0 <= cut < 200:
            text = text[cut + 1:]
    return text.strip('\n')

def _rotate_log(path):
    """log → log.1 → log.2 …, keeping LOG_BACKUPS old files."""
    keep = max(0, int(LOG_BACKUPS))
    try:
        if keep == 0:
            open(path, 'wb').close()
            return
        for i in range(keep, 0, -1):
            src = path if i == 1 else f'{path}.{i - 1}'
            if os.path.exists(src):
                os.replace(src, f'{path}.{i}')
    except OSError:                             # file busy (Windows) → just start over
        try:
            open(path, 'wb').close()
        except OSError:
            pass

def log_files(pid) -> list:
    """A script's log files, oldest first."""
    path = log_path(pid)
    old = [f'{path}.{i}' for i in range(max(int(LOG_BACKUPS), 1) + 5, 0, -1)]
    return [f for f in old + [path] if os.path.isfile(f)]

def search_log(pid, needle, limit=30):
    """(number of matching lines, the last `limit` of them) across all log files."""
    needle = needle.lower()
    hits, total = deque(maxlen=limit), 0
    for path in log_files(pid):
        try:
            with open(path, 'rb') as f:
                for raw in f:
                    line = _clean_log(raw).rstrip('\n')
                    if needle in line.lower():
                        total += 1
                        hits.append(line[:300])
        except OSError:
            continue
    return total, list(hits)

def _pump(run, pid):
    """Copy a script's output into its log file, rotating at MAX_LOG_MB."""
    path, cap = log_path(pid), MAX_LOG_MB * 1024 * 1024
    f = None
    try:
        f = open(path, 'ab')
        size = f.tell()
        while True:
            chunk = run.proc.stdout.read(8192)
            if not chunk:
                return
            f.write(chunk)
            f.flush()
            size += len(chunk)
            if size > cap:
                f.close()
                _rotate_log(path)
                f = open(path, 'ab')
                size = 0
                run.log_start = 0
    except (OSError, ValueError) as e:
        logger.warning('Log writer of project %s stopped (%s) — output is discarded from here.', pid, e)
        try:                                    # keep draining so the script never blocks on a full pipe
            while run.proc.stdout.read(65536):
                pass
        except (OSError, ValueError):
            pass
    finally:
        if f is not None:
            f.close()

# ════════════════════════════════════════════════════════════════════════════
#  PROCESS SUPERVISOR
# ════════════════════════════════════════════════════════════════════════════
class Run:
    """A live script process."""
    def __init__(self, proc, backend, container, log_start, installs, tried, limits):
        self.proc, self.backend, self.container = proc, backend, container
        self.ram_mb, self.cpus = limits['ram'], limits['cpus']   # from the owner's plan
        self.cpu_over_since = None      # when it went above its CPU share
        self.net = deque()              # (time, bytes sent) samples — Docker only
        self.started = time.time()
        self.log_start = log_start      # log offset where this run's output begins
        self.installs = installs        # auto-installs done since the manual start
        self.tried = tried              # packages already auto-installed
        self.stopping = False           # set when we kill it on purpose
        self.kill_reason = None         # e.g. memory limit
        self.pump = None
        self.pscache, self.cpu, self.rss = {}, 0.0, 0
        self.stats_at = 0

def is_running(pid) -> bool:
    run = RUNS.get(pid)
    return bool(run and run.proc.poll() is None)

def running_count() -> int:
    return sum(1 for r in list(RUNS.values()) if r.proc.poll() is None)

def _kill_tree(root_pid, group=False):
    """Terminate a process and all its children, then force-kill what is left."""
    procs = []
    try:
        parent = psutil.Process(root_pid)
        procs = parent.children(recursive=True) + [parent]
    except (psutil.Error, OSError):
        pass
    if group and os.name == 'posix':
        try:
            os.killpg(root_pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    for p in procs:
        try:
            p.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(procs, timeout=3)
    for p in alive:
        try:
            p.kill()
        except psutil.Error:
            pass
    if alive and group and os.name == 'posix':
        try:
            os.killpg(root_pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass

def _kill_run(run):
    if run.backend == 'docker':
        try:
            subprocess.run(['docker', 'kill', run.container], capture_output=True, timeout=20)
        except Exception as e:
            logger.warning('docker kill %s failed: %s', run.container, e)
    _kill_tree(run.proc.pid, group=_is_local(run.backend))
    try:
        run.proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        logger.warning('Process %s did not exit after kill.', run.proc.pid)

def _kill_orphan(proj, backend):
    """Kill a copy left over from a previous bot process (e.g. after a hard kill)."""
    if backend == 'docker':
        try:
            subprocess.run(['docker', 'rm', '-f', _container(proj['id'])],
                           capture_output=True, timeout=20)
        except Exception:
            pass
    old = proj['os_pid']
    if old and old != os.getpid():
        try:
            p = psutil.Process(old)
            if abs(p.create_time() - (proj['os_pid_ctime'] or 0)) < 2:
                logger.warning('Killing orphaned process %s of project %s', old, proj['id'])
                _kill_tree(old, group=_is_local(backend))
        except (psutil.Error, OSError):
            pass

_CODE_EXT = ('.py', '.js', '.mjs', '.cjs')

def file_sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()

def build_manifest(root) -> dict:
    """Fingerprint of every code file of a project: {relative path: sha256}."""
    out = {}
    for cur, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fn in files:
            if fn.lower().endswith(_CODE_EXT):
                path = os.path.join(cur, fn)
                try:
                    out[os.path.relpath(path, root).replace(os.sep, '/')] = file_sha256(path)
                except OSError:
                    continue
                if len(out) >= 5000:
                    return out
    return out

def set_manifest(pid):
    """Remember the code as it is now as "the approved version"."""
    x('UPDATE projects SET manifest=? WHERE id=?', (json.dumps(build_manifest(project_dir(pid))), pid))

def manifest_changes(pid, manifest) -> list:
    """What differs between the approved code and what is on disk now."""
    now = build_manifest(project_dir(pid))
    out = [f'changed: {k}' for k in manifest if k in now and now[k] != manifest[k]]
    out += [f'missing: {k}' for k in manifest if k not in now]
    out += [f'new: {k}' for k in now if k not in manifest]
    return out

def dir_size(path) -> int:
    total, stack = 0, [path]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total

def project_disk(pid) -> int:
    """Measure a project's folder (code, data and packages) and remember the result."""
    size = dir_size(project_dir(pid))
    x('UPDATE projects SET disk_bytes=?, disk_checked=? WHERE id=?', (size, time.time(), pid))
    return size

def halt_project(pid, reason, message):
    """Stop a script for breaking a limit. It stays stopped and the owner is told why."""
    proj = get_project(pid)
    run = RUNS.get(pid)
    PENDING_RESTART.pop(pid, None)
    if run:
        run.stopping = True
        _kill_run(run)
        with RUN_LOCK:
            if RUNS.get(pid) is run and run.proc.poll() is not None:
                del RUNS[pid]
    x('UPDATE projects SET should_run=0, last_reason=? WHERE id=?', (reason, pid))
    log_event('halt', pid)
    logger.warning('Project %s halted: %s', pid, reason)
    if proj:
        notify(proj['user_id'], f"⛔ <b>{esc(proj['name'])}</b> was stopped: {message}", _open_btn(pid))

def check_disk_quotas():
    """Stop running scripts whose folder grew past MAX_PROJECT_MB."""
    if not MAX_PROJECT_MB:
        return
    cap = MAX_PROJECT_MB * 1024 * 1024
    for pid in list(RUNS):
        row = q1('SELECT disk_checked FROM projects WHERE id=?', (pid,))
        if not row or time.time() - (row['disk_checked'] or 0) < DISK_CHECK_INTERVAL:
            continue
        size = project_disk(pid)
        if size > cap:
            halt_project(pid, 'disk quota',
                         f'it uses {human_size(size)} of disk and the limit is {MAX_PROJECT_MB} MB. '
                         'Delete what you do not need under 📁 Files, then start it again.')

def start_project(pid, auto=False, installs=0, tried=None):
    """Launch a project. Returns (ok, message). `auto` = restart by the supervisor."""
    proj = get_project(pid)
    if not proj:
        return False, 'This file no longer exists.'
    if is_banned(proj['user_id']):
        return False, 'The owner of this file is banned.'
    pdir = project_dir(pid)
    if not os.path.isfile(os.path.join(pdir, proj['main_file'])):
        return False, 'The main file is missing on disk — please upload it again.'
    backend = sandbox_backend()
    if backend == 'unavailable':
        need = 'Docker' if SANDBOX == 'docker' else 'bubblewrap'
        return False, f'{need} is required (SANDBOX = "{SANDBOX}") but is not available on this server.'
    if TAMPER_CHECK:
        if proj['manifest'] is None:
            set_manifest(pid)                # imported from the old version: trust what is there now
        else:
            changed = manifest_changes(pid, json.loads(proj['manifest']))
            if changed:
                first = proj['last_reason'] != 'modified'
                x("UPDATE projects SET should_run=0, last_reason='modified' WHERE id=?", (pid,))
                shown = ', '.join(changed[:5]) + (' …' if len(changed) > 5 else '')
                if first:
                    logger.warning('Project %s changed on disk after approval: %s', pid, shown)
                    for staff in admin_ids:
                        notify(staff, f"🚫 <b>{esc(proj['name'])}</b> of user <code>{proj['user_id']}</code> "
                                      f'was blocked: its code changed after approval.\n<code>{esc(shown)}</code>',
                               _open_btn(pid))
                    if auto and proj['user_id'] not in admin_ids:
                        notify(proj['user_id'], f"🚫 <b>{esc(proj['name'])}</b> was not restarted because its "
                                                'code changed on disk after it was approved. Upload it again.',
                               _open_btn(pid))
                return False, (f'Its code changed on disk after it was approved ({shown}). '
                               'Upload it again, or ask an admin to re-approve it.')
    if MAX_PROJECT_MB:
        size = project_disk(pid)
        if size > MAX_PROJECT_MB * 1024 * 1024:
            x("UPDATE projects SET should_run=0, last_reason='disk quota' WHERE id=?", (pid,))
            return False, (f'It uses {human_size(size)} of disk; the limit is {MAX_PROJECT_MB} MB. '
                           'Delete what you do not need under 📁 Files.')
    with RUN_LOCK:
        if pid in STARTING or is_running(pid):
            return False, 'Already running.'
        STARTING.add(pid)
    try:
        PENDING_RESTART.pop(pid, None)
        if not auto:
            CRASHES.pop(pid, None)
        _kill_orphan(proj, backend)
        with open(log_path(pid), 'ab') as f:
            f.write(f"\n──── {'auto-restart' if auto else 'start'} · "
                    f"{utcnow():%Y-%m-%d %H:%M:%S} UTC ────\n".encode('utf-8'))
            log_start = f.tell()
        kw = dict(cwd=pdir, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                  stderr=subprocess.STDOUT, bufsize=0)
        if _is_local(backend):
            kw['env'] = _child_env(pdir, pid, backend)
        else:                                # docker: -e NAME takes the value from the client's environment
            user_env = project_env(pid)
            if user_env:
                kw['env'] = dict(os.environ, **user_env)
        if os.name == 'posix':
            kw['start_new_session'] = True       # own process group → the whole tree can be killed
        else:
            kw['creationflags'] = (subprocess.CREATE_NEW_PROCESS_GROUP
                                   | getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            proc = subprocess.Popen(_run_command(proj, backend), **kw)
        except FileNotFoundError:
            what = 'Node.js' if proj['file_type'] == 'js' else 'The interpreter'
            return False, f'{what} is not installed on this server.'
        except OSError as e:
            return False, f'Could not launch: {e}'
        if _is_local(backend) and resource is not None and hasattr(resource, 'prlimit'):
            try:
                resource.prlimit(proc.pid, resource.RLIMIT_CORE, (0, 0))
                if MAX_FILE_WRITE_MB:
                    cap = MAX_FILE_WRITE_MB * 1024 * 1024
                    resource.prlimit(proc.pid, resource.RLIMIT_FSIZE, (cap, cap))
            except (OSError, ValueError):
                pass
        if _is_local(backend) and SCRIPT_NICE:
            try:                             # scripts never get priority over the bot itself
                psutil.Process(proc.pid).nice(SCRIPT_NICE if os.name == 'posix'
                                              else psutil.BELOW_NORMAL_PRIORITY_CLASS)
            except (psutil.Error, OSError, AttributeError):
                pass
        run = Run(proc, backend, _container(pid), log_start, installs, tried or set(),
                  limits_for(proj['user_id']))
        try:
            ctime = psutil.Process(proc.pid).create_time()
        except (psutil.Error, OSError):
            ctime = time.time()
        x('UPDATE projects SET should_run=1, run_count=run_count+1, last_reason=NULL, '
          'os_pid=?, os_pid_ctime=? WHERE id=?', (proc.pid, ctime, pid))
        x('UPDATE users SET total_runs=total_runs+1 WHERE user_id=?', (proj['user_id'],))
        with RUN_LOCK:
            RUNS[pid] = run
        run.pump = threading.Thread(target=_pump, args=(run, pid), daemon=True, name=f'pump-{pid}')
        run.pump.start()
        threading.Thread(target=_watch, args=(run, pid), daemon=True, name=f'watch-{pid}').start()
        log_event('start', pid)
        logger.info('Started project %s (%s) pid=%s backend=%s', pid, proj['name'], proc.pid, backend)
        return True, 'Started.'
    finally:
        with RUN_LOCK:
            STARTING.discard(pid)

def stop_project(pid, keep_flag=False) -> bool:
    """Stop a project. keep_flag=True leaves it marked as "should be running"."""
    PENDING_RESTART.pop(pid, None)
    if not keep_flag:
        x('UPDATE projects SET should_run=0 WHERE id=?', (pid,))
    run = RUNS.get(pid)
    if run or not keep_flag:
        x('UPDATE projects SET last_exit=NULL, last_reason=NULL WHERE id=?', (pid,))
    if not run:
        return False
    run.stopping = True
    _kill_run(run)
    with RUN_LOCK:
        if RUNS.get(pid) is run and run.proc.poll() is not None:
            del RUNS[pid]
    return True

def delete_project(pid):
    stop_project(pid)
    shutil.rmtree(project_dir(pid), ignore_errors=True)
    for p in log_files(pid):
        try:
            os.remove(p)
        except OSError:
            pass
    CRASHES.pop(pid, None)
    x('DELETE FROM projects WHERE id=?', (pid,))
    x('DELETE FROM project_env WHERE project_id=?', (pid,))
    x("UPDATE pkg_requests SET status='gone' WHERE project_id=? AND status='pending'", (pid,))

def _open_btn(pid):
    return kb([btn('⚙️ Open', f'f:{pid}')])

def _watch(run, pid):
    """Wait for a script to exit, then decide: finished, install + retry, restart or give up."""
    code = run.proc.wait()
    if run.pump:
        run.pump.join(timeout=3)
    uptime = time.time() - run.started
    with RUN_LOCK:
        if RUNS.get(pid) is run:
            del RUNS[pid]
    if run.stopping or SHUTDOWN.is_set():
        x('UPDATE projects SET os_pid=NULL, os_pid_ctime=NULL WHERE id=?', (pid,))
        return
    x('UPDATE projects SET last_exit=?, os_pid=NULL, os_pid_ctime=NULL WHERE id=?', (code, pid))
    try:
        _on_exit(run, pid, code, uptime)
    except Exception:
        logger.exception('Supervisor error for project %s', pid)

def _on_exit(run, pid, code, uptime):
    proj = get_project(pid)
    if not proj:
        return
    owner, name, quiet = proj['user_id'], esc(proj['name']), bool(proj['mute'])
    output = tail_log(pid, 4000, since=run.log_start)

    # 1) a missing package right after start → install it into the project and retry
    if code != 0 and not run.kill_reason and uptime < 90 and run.installs < AUTO_INSTALL_MAX:
        pkg = missing_package(output, proj)
        if pkg and pkg not in run.tried:
            if not package_allowed(proj['file_type'], pkg):
                request_package(proj, pkg)       # not on the allowed list → an admin decides
                return
            run.tried.add(pkg)
            notify(owner, f'📦 <b>{name}</b> needs <code>{esc(pkg)}</code> — installing…')
            ok, out = install_packages(proj, [pkg])
            if ok:
                ok2, msg = start_project(pid, auto=True, installs=run.installs + 1, tried=run.tried)
                if ok2:
                    notify(owner, f'✅ <code>{esc(pkg)}</code> installed — <b>{name}</b> restarted.',
                           _open_btn(pid))
                    return
            else:
                x("UPDATE projects SET should_run=0, last_reason='install failed' WHERE id=?", (pid,))
                notify(owner, f'❌ Could not install <code>{esc(pkg)}</code> for <b>{name}</b>:\n'
                              f'<pre>{esc(out[-500:])}</pre>', _open_btn(pid))
                return

    # 2) clean exit → the script simply finished; do not restart it
    if code == 0 and not run.kill_reason:
        x("UPDATE projects SET should_run=0, last_reason='finished' WHERE id=?", (pid,))
        log_event('finish', pid)
        if not quiet:
            notify(owner, f'✅ <b>{name}</b> finished (exit code 0) after {human_dur(uptime)}.',
                   _open_btn(pid))
        return

    # 3) crash → restart with back-off, up to MAX_RESTARTS inside RESTART_WINDOW
    why = run.kill_reason or f'exit code {code}'
    log_event('crash', pid)
    now = time.time()
    dq = CRASHES[pid]
    while dq and now - dq[0] > RESTART_WINDOW:
        dq.popleft()
    dq.append(now)
    tail = esc(output[-500:]) or '(no output)'
    if len(dq) > MAX_RESTARTS:
        x("UPDATE projects SET should_run=0, last_reason='crash loop' WHERE id=?", (pid,))
        log_event('gave_up', pid)
        notify(owner, f'❌ <b>{name}</b> crashed {len(dq)} times in {human_dur(RESTART_WINDOW)} '
                      f'({esc(why)}) — auto-restart stopped.\n<pre>{tail}</pre>', _open_btn(pid))
        return
    delay = min(60, RESTART_BACKOFF * 2 ** (len(dq) - 1))
    x('UPDATE projects SET last_reason=? WHERE id=?', (why, pid))
    PENDING_RESTART[pid] = now + delay
    if not quiet or run.kill_reason:             # limit kills are always reported
        notify(owner, f'🔄 <b>{name}</b> crashed ({esc(why)}) — restart {len(dq)}/{MAX_RESTARTS} '
                      f'in {delay}s.\n<pre>{tail}</pre>', _open_btn(pid))
    if SHUTDOWN.wait(delay):
        return
    proj = get_project(pid)
    if proj and proj['should_run'] and pid in PENDING_RESTART and not is_running(pid):
        start_project(pid, auto=True, installs=run.installs, tried=run.tried)

def proj_state(p) -> str:
    pid = p['id']
    if is_running(pid):
        return '🟢 Running'
    if pid in INSTALLING:
        return '📦 Installing packages…'
    if pid in INSTALL_QUEUE:
        return '⏳ Waiting for its turn to install packages…'
    due = PENDING_RESTART.get(pid)
    if due:
        return f'🟡 Crashed — restarting in {max(0, int(due - time.time()))}s'
    reason = p['last_reason']
    known = {'crash loop': '❌ Crashed (auto-restart gave up)',
             'install failed': '❌ Package install failed',
             'finished': '⚪ Finished',
             'needs package approval': '📦 Waiting for an admin to allow a package',
             'package denied': '❌ A package it needs was not allowed',
             'modified': '🚫 Blocked — its code changed after approval',
             'disk quota': '💽 Stopped — disk limit reached',
             'network quota': '🌐 Stopped — upload limit reached'}
    if reason in known:
        return known[reason]
    if p['last_exit'] not in (None, 0) and p['should_run']:
        return f"🔴 Stopped ({esc(reason or 'exit ' + str(p['last_exit']))})"
    return '🔴 Stopped'

def run_stats(pid):
    """(cpu %, rss bytes) of a running project as last measured, or (None, None)."""
    run = RUNS.get(pid)
    return (run.cpu, run.rss) if run else (None, None)

def _parse_size(text) -> int:
    """'12.5MiB' / '1.2kB' / '3GB' → bytes."""
    m = re.match(r'\s*([\d.]+)\s*([kKMGT]?)(i?)B', text or '')
    if not m:
        return 0
    base = 1024 if m.group(3) or m.group(2) in ('', 'K') else 1000
    return int(float(m.group(1)) * base ** ' KMGT'.index(m.group(2).upper() or ' '))

def _sample_docker():
    """Docker mode: read CPU / RAM / network of all containers in one call and
    enforce the optional upload cap."""
    live = {r.container: (pid, r) for pid, r in list(RUNS.items())
            if r.backend == 'docker' and r.proc.poll() is None}
    if not live:
        return
    try:
        out = subprocess.run(['docker', 'stats', '--no-stream', '--format',
                              '{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.NetIO}}'],
                             capture_output=True, timeout=30, text=True).stdout
    except Exception as e:
        logger.debug('docker stats failed: %s', e)
        return
    now = time.time()
    for line in out.splitlines():
        parts = line.strip().split('|')
        if len(parts) != 4 or parts[0] not in live:
            continue
        pid, run = live[parts[0]]
        try:
            run.cpu = float(parts[1].strip().rstrip('%'))
        except ValueError:
            pass
        run.rss = _parse_size(parts[2].split('/')[0])
        sent = _parse_size(parts[3].split('/')[-1])
        run.net.append((now, sent))
        while run.net and now - run.net[0][0] > 3600:
            run.net.popleft()
        used = sent - run.net[0][1]
        if MAX_NET_OUT_MB_PER_HOUR and used > MAX_NET_OUT_MB_PER_HOUR * 1024 * 1024:
            threading.Thread(target=halt_project, daemon=True, args=(
                pid, 'network quota', f'it uploaded {human_size(used)} within an hour; the limit is '
                                      f'{MAX_NET_OUT_MB_PER_HOUR} MB.')).start()

def _sample_runs():
    """Without Docker: measure CPU/RAM of every script and enforce its plan's
    RAM cap and (if it stays above it for CPU_LIMIT_SECONDS) its CPU share."""
    for pid, run in list(RUNS.items()):
        if not _is_local(run.backend) or run.proc.poll() is not None:
            continue
        try:
            root = psutil.Process(run.proc.pid)
            procs = [root] + root.children(recursive=True)
        except (psutil.Error, OSError):
            continue
        cache, cpu, rss = {}, 0.0, 0
        for p in procs:
            old = run.pscache.get(p.pid)
            if old is not None and old == p:
                p = old                   # keep the object: cpu_percent needs two samples
            try:
                cpu += p.cpu_percent(None)
                rss += p.memory_info().rss
            except (psutil.Error, OSError):
                continue
            cache[p.pid] = p
        run.pscache, run.cpu, run.rss = cache, cpu, rss
        if run.kill_reason:
            continue
        if run.ram_mb and rss > run.ram_mb * 1024 * 1024:
            run.kill_reason = f'memory limit: used {human_size(rss)} of {run.ram_mb} MB'
        elif CPU_LIMIT_SECONDS and run.cpus and cpu > run.cpus * 100:
            run.cpu_over_since = run.cpu_over_since or time.time()
            if time.time() - run.cpu_over_since >= CPU_LIMIT_SECONDS:
                run.kill_reason = (f'CPU limit: above {run.cpus * 100:.0f}% of a core for '
                                   f'{human_dur(CPU_LIMIT_SECONDS)}')
        else:
            run.cpu_over_since = None
        if run.kill_reason:
            logger.warning('Project %s stopped by a limit: %s', pid, run.kill_reason)
            threading.Thread(target=_kill_run, args=(run,), daemon=True).start()

def resume_projects():
    """After a bot restart: clean up leftovers and start what was running before."""
    backend = sandbox_backend()
    for p in q('SELECT * FROM projects WHERE os_pid IS NOT NULL AND should_run=0'):
        _kill_orphan(p, backend)
        x('UPDATE projects SET os_pid=NULL, os_pid_ctime=NULL WHERE id=?', (p['id'],))
    rows = q('SELECT id, name FROM projects WHERE should_run=1')
    started = 0
    for p in rows:
        if SHUTDOWN.is_set():
            return
        ok, msg = start_project(p['id'], auto=True)
        if ok:
            started += 1
        elif msg != 'Already running.':
            logger.warning('Could not resume project %s (%s): %s', p['id'], p['name'], msg)
        time.sleep(0.3)
    if rows:
        logger.info('Resumed %d of %d scripts that were running before the restart.', started, len(rows))

# ════════════════════════════════════════════════════════════════════════════
#  PROJECT INSTALL / DEPLOY
# ════════════════════════════════════════════════════════════════════════════
def install_project(uid, name, main_file, ftype, stage_dir, sha) -> int:
    """Move a reviewed upload from data/pending/ into projects/<id>/."""
    row = q1('SELECT id FROM projects WHERE user_id=? AND name=?', (uid, name))
    if row:
        pid = row['id']
        stop_project(pid)
        shutil.rmtree(project_dir(pid), ignore_errors=True)
        x('UPDATE projects SET main_file=?, file_type=?, upload_time=?, sha256=?, '
          'last_exit=NULL, last_reason=NULL WHERE id=?', (main_file, ftype, iso_now(), sha, pid))
    else:
        pid = x('INSERT INTO projects (user_id, name, main_file, file_type, upload_time, sha256) '
                'VALUES (?,?,?,?,?,?)', (uid, name, main_file, ftype, iso_now(), sha)).lastrowid
    shutil.move(stage_dir, project_dir(pid))
    set_manifest(pid)
    log_event('upload', pid)
    x('UPDATE users SET total_uploads=total_uploads+1 WHERE user_id=?', (uid,))
    return pid

def deploy(pid, ctx, install=True):
    """Install a project's packages, start it and report the result on ctx."""
    proj = get_project(pid)
    if not proj:
        return
    name = esc(proj['name'])
    pdir = project_dir(pid)
    has_deps = install and os.path.isfile(os.path.join(
        pdir, 'requirements.txt' if proj['file_type'] == 'py' else 'package.json'))
    if has_deps:
        ctx.draw(f'📦 <b>{name}</b> — installing packages…')
        ok, out = install_packages(proj)
        if not ok:
            x("UPDATE projects SET last_reason='install failed' WHERE id=?", (pid,))
            ctx.draw(f'❌ <b>{name}</b> — package install failed:\n<pre>{esc(out[-600:])}</pre>',
                     _open_btn(pid))
            return
    ctx.draw(f'⏳ <b>{name}</b> — starting…')
    ok, msg = start_project(pid)
    if not ok:
        ctx.draw(f'❌ <b>{name}</b> — {esc(msg)}', _open_btn(pid))
        return
    time.sleep(START_CHECK_DELAY)
    proj = get_project(pid)
    if not proj:
        return
    if is_running(pid):
        ctx.draw(f'🟢 <b>{name}</b> is running.', _open_btn(pid))
    else:
        ctx.draw(f'⚠️ <b>{name}</b> — {proj_state(proj)}\n<pre>{esc(tail_log(pid, 600)) or "(no output)"}</pre>',
                 _open_btn(pid))

# ════════════════════════════════════════════════════════════════════════════
#  APPROVAL SYSTEM
# ════════════════════════════════════════════════════════════════════════════
def get_approval(aid):
    return q1('SELECT * FROM approvals WHERE id=?', (aid,))

def visible_len(text) -> int:
    """Length of a message as Telegram counts it (tags removed, entities decoded)."""
    return len(html.unescape(re.sub(r'<[^<>]+>', '', text)))

def approval_text(a) -> str:
    """Review card for reviewers (always short enough for a document caption)."""
    uid = a['user_id']
    kind = a['kind'] or 'upload'
    u = q1('SELECT first_name, username FROM users WHERE user_id=?', (uid,))
    who = esc((u['first_name'] if u else None) or 'user')[:40]
    if u and u['username']:
        who += f" (@{esc(u['username'])})"
    try:
        rep = json.loads(a['report'] or '{}')
    except ValueError:
        rep = {}
    lang = '🐍 Python' if a['file_type'] == 'py' else '🟨 Node.js'
    head = {'git': 'Git deploy', 'patch': 'File change'}.get(kind, 'Upload')
    # (rank, line): rank 0 is always shown; higher ranks are dropped first if the card gets too long
    rows = [(0, f"🔔 <b>{head} #{a['id']} — needs review</b>"),
            (0, f'👤 {who} · <code>{uid}</code> · {status_str(uid)}')]
    if kind == 'patch':
        rows.append((0, f"🩹 <code>{esc((a['target'] or '')[:60])}</code> in "
                        f"<code>{esc(a['name'])}</code> · {lang}"))
    else:
        rows.append((0, f"📄 <code>{esc(a['name'])}</code> · {lang}"))
        if a['main_file'] != a['name']:
            rows.append((0, f"▶️ Entry: <code>{esc(a['main_file'][:60])}</code>"))
    if a['source']:
        rows.append((0, f"🔗 {esc(a['source'][:90])}"))
    rows.append((0, f"📦 {rep.get('n_files', '?')} file(s) · {human_size(rep.get('size', 0))} · "
                    f"sha256 <code>{esc((a['sha256'] or '')[:12])}</code>"))
    d = rep.get('diff')
    if d:
        rows.append((0, f"🔀 <b>Compared with the approved version:</b> {d['changed']} changed, "
                        f"{d['added']} new, {d['removed']} removed (+{d['plus']} / −{d['minus']} lines)"))
    elif kind == 'patch':
        rows.append((0, '🆕 This file is new in the project'))
    elif q1('SELECT 1 FROM projects WHERE user_id=? AND name=?', (uid, a['name'])):
        rows.append((0, '♻️ <b>Replaces</b> a file this user already hosts (no code differences found)'))
    requires = rep.get('requires') or []
    if requires:
        lang_key = a['file_type']
        shown = ', '.join(('' if package_allowed(lang_key, r) else '⚠️') + esc(r[:22]) for r in requires[:10])
        rows.append((1, f"🧩 Installs: {shown}{' …' if len(requires) > 10 else ''}"))
    imports = rep.get('imports') or []
    if imports:
        shown = ', '.join(esc(i[:24]) for i in imports[:12])
        rows.append((2, f"📥 Imports: {shown}{' …' if len(imports) > 12 else ''}"))
    flags = rep.get('flags') or {}
    if flags:
        shown = ', '.join(f'{esc(k[:34])}×{v}' for k, v in list(flags.items())[:8])
        rows.append((0, f"⚠️ <b>Check:</b> {shown}{' …' if len(flags) > 8 else ''}"))
    else:
        rows.append((0, '✅ Nothing risky found by the automatic scan'))
    for note in (rep.get('notes') or [])[:2]:
        rows.append((3, f'ℹ️ {esc(note)}'))
    left = a['submitted_at'] + APPROVE_TIMEOUT - time.time()
    rows.append((0, f'⏰ Expires in {human_dur(left)}'))
    while visible_len('\n'.join(t for _, t in rows)) > 1000 and any(r for r, _ in rows):
        worst = max(r for r, _ in rows)
        rows = [row for row in rows if row[0] != worst]
    return '\n'.join(t for _, t in rows)

def approval_buttons(aid, in_panel=False, full=True, has_diff=False):
    """full=False for reviewers, who cannot ban."""
    rows = [[btn('✅ Approve', f'ay:{aid}'), btn('❌ Reject', f'an:{aid}')],
            [btn('📝 Reject + reason', f'anr:{aid}'), btn('🚫 Reject + ban', f'ab:{aid}') if full else None],
            [btn('👤 User info', f'ai:{aid}')]]
    if in_panel:
        rows.append([btn('📎 Send me the file', f'afile:{aid}'),
                     btn('🔀 Changes', f'adiff:{aid}') if has_diff else None])
        rows.append([btn('🔙 Back', 'ap:0')])
    return kb(*rows)

def _diff_path(a) -> str:
    return (a['stage_dir'] or '') + '.diff'

def _drop_stage(a):
    """Remove everything that was staged for a request."""
    shutil.rmtree(a['stage_dir'] or '', ignore_errors=True)
    try:
        os.remove(_diff_path(a))
    except OSError:
        pass

def _send_diff(chat_id, a) -> bool:
    path = _diff_path(a)
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        return False
    with open(path, 'rb') as f:
        bot.send_document(chat_id, f, caption=f"🔀 What changed in request #{a['id']} "
                                              f"(<code>{esc(a['name'])}</code>)",
                          visible_file_name=f"changes_{a['id']}.diff.txt")
    return True

def submit_for_approval(uid, name, staged, sha, report, tg_file_id, kind='upload', target=None,
                        project_id=None, source='', document=None) -> int:
    """Queue something for review and send it to every reviewer.
    kind: 'upload' (file from the chat), 'git' (a cloned repository — pass its zip as
    `document`) or 'patch' (one changed file of an existing project)."""
    if kind == 'patch':
        olds = q("SELECT id FROM approvals WHERE project_id=? AND target=? AND kind='patch' "
                 "AND status='pending'", (project_id, target))
    else:
        olds = q("SELECT id FROM approvals WHERE user_id=? AND name=? AND status='pending' "
                 "AND COALESCE(kind, 'upload') != 'patch'", (uid, name))
    for old in olds:
        _close_approval(old['id'], 'superseded', '♻️ Replaced by a newer upload of the same file.')

    # what differs from the version that is approved right now?
    diff_text = ''
    try:
        if kind == 'patch':
            live = os.path.join(project_dir(project_id), *target.split('/'))
            new = os.path.join(staged['stage_dir'], os.path.basename(target))
            if os.path.isfile(live):
                plus, minus, diff_text = diff_files(live, new, target)
                report['diff'] = {'changed': 1 if diff_text else 0, 'added': 0, 'removed': 0,
                                  'plus': plus, 'minus': minus}
        else:
            existing = q1('SELECT id FROM projects WHERE user_id=? AND name=?', (uid, name))
            if existing:
                summary, diff_text = diff_trees(project_dir(existing['id']), staged['stage_dir'])
                if diff_text:
                    report['diff'] = summary
    except Exception:
        logger.exception('Could not compute the diff for a review request')

    aid = x('INSERT INTO approvals (user_id, name, main_file, file_type, stage_dir, tg_file_id, sha256, '
            'report, submitted_at, kind, target, project_id, source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (uid, name, staged['main_file'], staged['file_type'], staged['stage_dir'], tg_file_id, sha,
             json.dumps(report), time.time(), kind, target, project_id, source or '')).lastrowid
    a = get_approval(aid)
    if diff_text:
        with open(_diff_path(a), 'w', encoding='utf-8') as f:
            f.write(diff_text)
    text, cards = approval_text(a), []
    for staff in sorted(staff_ids()):
        try:
            doc = tg_file_id
            if doc is None:                       # no Telegram file yet (Git deploy): upload it once
                doc = io.BytesIO(document)
                doc.name = os.path.splitext(name)[0] + '.zip'
            m = bot.send_document(staff, doc, caption=text,
                                  reply_markup=approval_buttons(aid, full=is_admin(staff)))
            cards.append([m.chat.id, m.message_id])
            if tg_file_id is None and getattr(m, 'document', None):
                tg_file_id = m.document.file_id
                x('UPDATE approvals SET tg_file_id=? WHERE id=?', (tg_file_id, aid))
            if diff_text:
                _send_diff(staff, a)
        except Exception as e:
            logger.warning('Could not send request #%s to %s: %s', aid, staff, e)
    x('UPDATE approvals SET cards=? WHERE id=?', (json.dumps(cards), aid))
    logger.info('Review request #%s (%s) submitted: uid=%s file=%s', aid, kind, uid, name)
    return aid

def _update_cards(a, text, skip=None):
    """Replace the review card every admin received with the outcome."""
    try:
        cards = json.loads(a['cards'] or '[]')
    except ValueError:
        cards = []
    for chat_id, msg_id in cards:
        if skip and (chat_id, msg_id) == skip:
            continue
        try:
            bot.edit_message_caption(text, chat_id, msg_id, reply_markup=None)
        except Exception as e:
            logger.debug('Card update failed: %s', e)

def _close_approval(aid, status, note) -> bool:
    """Close a pending approval without installing it (expired / superseded)."""
    if x("UPDATE approvals SET status=? WHERE id=? AND status='pending'", (status, aid)).rowcount != 1:
        return False
    a = get_approval(aid)
    _drop_stage(a)
    _update_cards(a, f"{note}\n📄 <code>{esc(a['name'])}</code> · user <code>{a['user_id']}</code>")
    return True

def ban_user(uid) -> bool:
    if uid == OWNER_ID or is_admin(uid):
        return False
    set_banned(uid, True)
    for p in user_projects(uid):
        stop_project(p['id'])
    for a in q("SELECT id FROM approvals WHERE user_id=? AND status='pending'", (uid,)):
        _close_approval(a['id'], 'rejected', '🚫 User banned — upload discarded.')
    logger.warning('User %s banned.', uid)
    return True

def write_project_file(pid, rel, src_path) -> None:
    """Put one file into a project (rel is a path inside it) and re-fingerprint the project."""
    pdir = os.path.abspath(project_dir(pid))
    dst = os.path.abspath(os.path.join(pdir, *rel.split('/')))
    if not dst.startswith(pdir + os.sep):
        raise UploadError('Invalid file path.')
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src_path, dst)
    set_manifest(pid)

def apply_file_change(pid, rel, src_path, ctx):
    """Swap one file of a project; a running script is restarted with the new code."""
    proj = get_project(pid)
    was_active = is_running(pid) or pid in PENDING_RESTART
    if was_active:
        stop_project(pid, keep_flag=True)
    write_project_file(pid, rel, src_path)
    name = esc(proj['name'])
    if rel in ('requirements.txt', 'package.json'):
        ctx.draw(f'📦 <b>{name}</b> — installing packages…')
        ok, out = install_packages(get_project(pid))
        if not ok:
            x("UPDATE projects SET should_run=0, last_reason='install failed' WHERE id=?", (pid,))
            ctx.draw(f'❌ <b>{name}</b> — package install failed:\n<pre>{esc(out[-600:])}</pre>', _open_btn(pid))
            return
    if was_active:
        deploy(pid, ctx, install=False)
    else:
        ctx.draw(f'✅ <code>{esc(rel)}</code> in <b>{name}</b> was updated. Start it whenever you like.',
                 _open_btn(pid))

def decide(aid, admin, approved, reason=None, ban=False, skip_card=None):
    """Approve or reject. Returns (ok, text for the admin). Safe against double clicks."""
    status = 'approved' if approved else 'rejected'
    if x("UPDATE approvals SET status=?, decided_by=?, reason=? WHERE id=? AND status='pending'",
         (status, admin.id, reason, aid)).rowcount != 1:
        a = get_approval(aid)
        return False, f"Already {a['status']}." if a else 'Not found.'
    a = get_approval(aid)
    uid, name = a['user_id'], esc(a['name'])
    by = esc(admin.first_name or admin.id)
    kind = a['kind'] or 'upload'
    what = f"<code>{name}</code>" if kind != 'patch' else f"<code>{esc(a['target'])}</code> in <code>{name}</code>"
    audit(admin.id, status, f"request #{aid}", f"{a['name']} of {uid}" + (f' — {reason}' if reason else ''))
    if approved:
        if not os.path.isdir(a['stage_dir'] or '') or (kind == 'patch' and not get_project(a['project_id'])):
            x("UPDATE approvals SET status='error' WHERE id=?", (aid,))
            _drop_stage(a)
            return False, 'The staged files (or the file they belong to) are gone — ask the user to upload again.'
        result = f'✅ <b>Approved</b> by {by}\n📄 {what} · user <code>{uid}</code>'
        status_msg = notify(uid, f'✅ Your change to {what} was <b>approved</b>!' if kind == 'patch'
                            else f'✅ Your file <b>{name}</b> was <b>approved</b>!')
        ctx = Ctx(uid, status_msg.message_id) if status_msg else Ctx(uid)
        if kind == 'patch':
            def work():
                try:
                    apply_file_change(a['project_id'], a['target'],
                                      os.path.join(a['stage_dir'], os.path.basename(a['target'])), ctx)
                except Exception as e:
                    logger.exception('Could not apply the approved change #%s', aid)
                    ctx.draw(f'❌ The approved change could not be applied: {esc(e)}')
                finally:
                    _drop_stage(a)
            threading.Thread(target=work, daemon=True).start()
        else:
            pid = install_project(uid, a['name'], a['main_file'], a['file_type'], a['stage_dir'], a['sha256'])
            if a['source']:
                x('UPDATE projects SET source=? WHERE id=?', (a['source'], pid))
            _drop_stage(a)
            threading.Thread(target=deploy, args=(pid, ctx), daemon=True).start()
    else:
        _drop_stage(a)
        result = f'❌ <b>Rejected</b> by {by}\n📄 {what} · user <code>{uid}</code>'
        if reason:
            result += f'\n📝 {esc(reason[:300])}'
        if ban and is_admin(admin.id) and ban_user(uid):
            audit(admin.id, 'banned', f'user {uid}', 'together with a rejection')
            result += '\n🚫 User banned.'
        notify(uid, (f'❌ Your change to {what} was <b>rejected</b> by an admin.' if kind == 'patch'
                     else f'❌ Your file <b>{name}</b> was <b>rejected</b> by an admin.')
               + (f'\n📝 Reason: {esc(reason[:300])}' if reason else ''))
    _update_cards(a, result, skip=skip_card)
    logger.info('Approval #%s %s by %s', aid, status, admin.id)
    return True, result

def expire_approvals():
    cutoff = time.time() - APPROVE_TIMEOUT
    for a in q("SELECT id, user_id, name FROM approvals WHERE status='pending' AND submitted_at < ?",
               (cutoff,)):
        if _close_approval(a['id'], 'expired', '⏰ Expired — nobody reviewed it in time.'):
            notify(a['user_id'], f"⏰ Your file <b>{esc(a['name'])}</b> was not reviewed within "
                                 f'{human_dur(APPROVE_TIMEOUT)} and expired. Please upload it again.')
            logger.info('Approval #%s expired.', a['id'])

def remind_approvals():
    """Nudge the reviewers shortly before a request expires unreviewed."""
    if not APPROVE_REMIND_BEFORE or APPROVE_REMIND_BEFORE >= APPROVE_TIMEOUT:
        return
    soon = time.time() - APPROVE_TIMEOUT + APPROVE_REMIND_BEFORE
    for a in q("SELECT * FROM approvals WHERE status='pending' AND COALESCE(reminded, 0)=0 "
               "AND submitted_at < ?", (soon,)):
        if x('UPDATE approvals SET reminded=1 WHERE id=? AND COALESCE(reminded, 0)=0', (a['id'],)).rowcount != 1:
            continue
        left = a['submitted_at'] + APPROVE_TIMEOUT - time.time()
        for staff in staff_ids():
            notify(staff, f"⏰ Request #{a['id']} (<code>{esc(a['name'])}</code>, user "
                          f"<code>{a['user_id']}</code>) expires in {human_dur(left)} and nobody has "
                          'reviewed it yet.', kb([btn('🔍 Review it', f"a:{a['id']}")]))

def cleanup_pending_dir():
    """Remove staged folders that no pending approval points at (e.g. after a crash)."""
    wanted = set()
    for r in q("SELECT stage_dir FROM approvals WHERE status='pending'"):
        if r['stage_dir']:
            wanted.add(os.path.abspath(r['stage_dir']))
            wanted.add(os.path.abspath(r['stage_dir']) + '.diff')
    for item in os.listdir(PENDING_DIR):
        path = os.path.abspath(os.path.join(PENDING_DIR, item))
        if path not in wanted and time.time() - os.path.getmtime(path) > 600:
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                try:
                    os.remove(path)
                except OSError:
                    pass

# ════════════════════════════════════════════════════════════════════════════
#  BACKGROUND MAINTENANCE
# ════════════════════════════════════════════════════════════════════════════
def subscription_reminders():
    now = utcnow()
    for r in q('SELECT user_id, expiry, reminded FROM subscriptions'):
        exp = parse_dt(r['expiry'])
        if not exp:
            continue
        if exp <= now and (r['reminded'] or 0) < 2:
            x('UPDATE subscriptions SET reminded=2 WHERE user_id=?', (r['user_id'],))
            notify(r['user_id'], '⌛ Your subscription has expired. Your files keep running, '
                                 f'but your limit is back to {FREE_USER_LIMIT} files.')
        elif now < exp <= now + timedelta(days=SUB_REMIND_DAYS) and not r['reminded']:
            x('UPDATE subscriptions SET reminded=1 WHERE user_id=?', (r['user_id'],))
            notify(r['user_id'], f'⏳ Your subscription expires in {human_dur((exp - now).total_seconds())} '
                                 f'({fmt_dt(exp)}). Contact {esc(YOUR_USERNAME)} to renew.')

def _prune_state():
    now = time.time()
    for uid in [u for u, st in list(AWAIT.items()) if st['exp'] < now]:
        AWAIT.pop(uid, None)
    for store in (_last_upload, _last_action, _join_cache):
        for uid in [u for u, t in list(store.items()) if now - t > 3600]:
            store.pop(uid, None)

def _maintenance_loop():
    last_slow, tick = 0, 0
    while not SHUTDOWN.wait(5):
        tick += 1
        try:
            _sample_runs()
            if tick % 3 == 0:
                _sample_docker()
        except Exception:
            logger.exception('Resource sampling failed')
        if tick % 6 == 0:
            for job in (run_schedules, run_scheduled_broadcasts):
                try:
                    job()
                except Exception:
                    logger.exception('%s failed', job.__name__)
        if time.time() - last_slow >= 60:
            last_slow = time.time()
            for job in (remind_approvals, expire_approvals, subscription_reminders,
                        cleanup_pending_dir, _prune_state, check_disk_quotas, check_alerts,
                        daily_report, scheduled_backup):
                try:
                    job()
                except Exception:
                    logger.exception('Maintenance job %s failed', job.__name__)

# ════════════════════════════════════════════════════════════════════════════
#  ACCESS GATES
# ════════════════════════════════════════════════════════════════════════════
def is_joined_all(uid) -> bool:
    if not FORCE_JOIN_CHANNELS or is_staff(uid):
        return True
    if _join_cache.get(uid, 0) > time.time():
        return True
    for ch in FORCE_JOIN_CHANNELS:
        try:
            m = bot.get_chat_member(ch, uid)
        except Exception as e:
            logger.warning('Membership check failed for %s in %s (%s) — is the bot an admin there?',
                           uid, ch, e)
            return False
        if m.status not in ('member', 'administrator', 'creator') and not (
                m.status == 'restricted' and getattr(m, 'is_member', False)):
            return False
    _join_cache[uid] = time.time() + 120
    return True

def register_user(u):
    if not touch_user(u):
        return
    ref = _pending_ref.pop(u.id, None)
    invited = ''
    if ref and _apply_referral(u, ref):
        invited = f'\n👥 Invited by <code>{ref}</code>'
    if u.id != OWNER_ID:
        uname = f'@{esc(u.username)}' if u.username else 'no username'
        notify(OWNER_ID, f'🆕 <b>New user</b>\n👤 {esc(u.first_name or "")} · {uname}\n'
                         f'🆔 <code>{u.id}</code>{invited}')

def _apply_referral(u, ref) -> bool:
    """A new user arrived through someone's invite link: reward the inviter."""
    if ref == u.id or not q1('SELECT 1 FROM users WHERE user_id=? AND banned=0', (ref,)):
        return False
    x('UPDATE users SET referred_by=? WHERE user_id=?', (ref, u.id))
    if REFERRAL_BONUS_DAYS and x('UPDATE users SET ref_count=ref_count+1 WHERE user_id=? AND ref_count < ?',
                                 (ref, REFERRAL_MAX)).rowcount == 1:
        plan, expiry = grant_sub(ref, first_plan(), REFERRAL_BONUS_DAYS)
        notify(ref, f'🎉 {esc(u.first_name or "A friend")} joined with your link — you got '
                    f'<b>{REFERRAL_BONUS_DAYS} days</b> of {plan_name(plan)} (until {fmt_dt(expiry)}).')
    return True

_bot_name = None

def bot_username() -> str:
    global _bot_name
    if _bot_name is None:
        try:
            _bot_name = bot.get_me().username or ''
        except Exception:
            return ''
    return _bot_name

def gate(m) -> bool:
    """Checks every incoming message must pass: ban, forced join, lock."""
    u = m.from_user
    if is_banned(u.id):
        notify(m.chat.id, '🚫 You are banned from using this bot.')
        return False
    if not is_joined_all(u.id):
        scr_join(Ctx(m.chat.id))
        return False
    register_user(u)
    if STATE['locked'] and not is_staff(u.id):
        notify(m.chat.id, '🔒 The bot is locked by the admin. Please try again later.')
        return False
    return True

def guard(fn):
    """Log exceptions from a message handler instead of losing them."""
    @functools.wraps(fn)
    def wrapper(m, *args, **kwargs):
        try:
            return fn(m, *args, **kwargs)
        except Exception:
            logger.exception('Handler %s failed', fn.__name__)
            notify(m.chat.id, '⚠️ Something went wrong. Please try again.')
    return wrapper

def ans(call, text=None, alert=False):
    """Answer a button press (exactly once)."""
    if getattr(call, '_hb_answered', False):
        return
    call._hb_answered = True
    try:
        bot.answer_callback_query(call.id, text[:200] if text else None, show_alert=alert)
    except Exception as e:
        logger.debug('answer_callback_query failed: %s', e)

def ask(ctx, uid, kind, prompt, arg=None, back='home', want='message'):
    """Show a prompt and wait for the user's next message (or file, with want='file')."""
    AWAIT[uid] = {'kind': kind, 'arg': arg, 'exp': time.time() + INPUT_TIMEOUT}
    hint = ('Send the file as a document' if want == 'file' else 'Send your answer as a message')
    ctx.draw(f'{prompt}\n\n<i>{hint}, or press Cancel.</i>', kb([btn('✖️ Cancel', back)]))

def to_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None

def nav_row(prefix, page, pages):
    if pages <= 1:
        return []
    return [btn('◀️', f'{prefix}{page - 1}') if page > 0 else btn('·', 'noop'),
            btn(f'{page + 1}/{pages}', 'noop'),
            btn('▶️', f'{prefix}{page + 1}') if page < pages - 1 else btn('·', 'noop')]

def paginate(items, page, per_page=None):
    per_page = per_page or FILES_PER_PAGE
    pages = max(1, math.ceil(len(items) / per_page))
    page = min(max(0, page or 0), pages - 1)
    return items[page * per_page:(page + 1) * per_page], page, pages

LANG = {'py': '🐍 Python', 'js': '🟨 Node.js'}

# ════════════════════════════════════════════════════════════════════════════
#  SCREENS — USER
# ════════════════════════════════════════════════════════════════════════════
def scr_join(ctx):
    rows = [[url_btn(f"📢 Join {ch.lstrip('@')}", f"https://t.me/{ch.lstrip('@')}")]
            for ch in FORCE_JOIN_CHANNELS]
    rows.append([btn('✅ I joined', 'join')])
    ctx.draw('⛔ <b>Join our channel first</b>\n\nJoin using the button below, then press '
             '<b>I joined</b>.', kb(*rows))

def scr_home(ctx, user):
    uid = user.id
    projs = user_projects(uid)
    running = sum(1 for p in projs if is_running(p['id']))
    pend = pending_count(uid)
    uname = f'@{esc(user.username)}' if user.username else 'no username'
    text = (f"👋 Welcome, <b>{esc(user.first_name or 'there')}</b>!\n\n"
            f"🆔 <code>{uid}</code> · {uname}\n"
            f"🔰 {status_str(uid)}\n"
            f"📁 Files  {bar(len(projs), file_limit(uid))}\n"
            f"🟢 Running: <b>{running}</b>"
            + (f" · ⏳ In review: <b>{pend}</b>" if pend else '') + "\n\n"
            "Send a <code>.py</code>, <code>.js</code> or <code>.zip</code> file to host it.\n"
            "Every upload is reviewed by an admin before it runs.")
    if STATE['locked']:
        text += '\n\n🔒 <i>The bot is currently locked by the admin.</i>'
    rows = [[btn('📤 Upload', 'up'), btn('📂 My Files', 'files:0')],
            [btn('📊 Stats', 'stats'), btn('⚡ Ping', 'ping')],
            [btn('⭐ Plans', 'prem'), btn('📖 Help', 'help')],
            [url_btn('📢 Channel', UPDATE_CHANNEL),
             url_btn('📞 Contact Owner', f"https://t.me/{YOUR_USERNAME.lstrip('@')}")]]
    if is_staff(uid):
        n = pending_count()
        rows.append([btn(('🛠 Admin Panel' if is_admin(uid) else '🔎 Review Panel')
                         + (f' · {n} to review' if n else ''), 'adm')])
    ctx.draw(text, kb(*rows))

def scr_help(ctx):
    ctx.draw(
        '📖 <b>How it works</b>\n\n'
        '1️⃣ Send a <code>.py</code>, <code>.js</code> or <code>.zip</code> file in this chat'
        + (' — or use <b>Upload → From Git</b> with a public repository link.\n' if GIT_ENABLED else '.\n') +
        '2️⃣ An admin reviews it. Nothing runs before approval.\n'
        '3️⃣ Once approved, packages are installed and the script starts by itself.\n\n'
        '<b>Zip / Git projects</b>\n'
        '• Entry point: <code>main.py</code>, <code>bot.py</code>, <code>app.py</code>, '
        '<code>index.js</code> … (or <code>"main"</code> in package.json)\n'
        '• <code>requirements.txt</code> / <code>package.json</code> are installed automatically\n'
        '• Do not include <code>node_modules</code> or <code>venv</code>\n\n'
        '<b>On each file\'s card</b>\n'
        '• <b>📁 Files</b> — download, replace or add single files (code changes are reviewed)\n'
        '• <b>⚙️ Settings → Variables</b> — keep tokens and keys out of your code\n'
        '• <b>⚙️ Settings → Schedule</b> — restart every few hours or start daily at a set time\n'
        '• <b>📋 Logs</b> — live view, search and full download\n\n'
        '<b>Good to know</b>\n'
        f'• Max upload {MAX_FILE_SIZE_MB} MB'
        + (f' · {MAX_PROJECT_MB} MB disk per file\n' if MAX_PROJECT_MB else '\n') +
        f'• A crashed script is restarted up to {MAX_RESTARTS} times; a script that ends '
        'normally is not restarted\n'
        '• Uploading a file with the same name replaces the old one after review\n'
        '• Your limits depend on your plan — see <b>⭐ Plans</b>\n\n'
        '<b>Commands</b>\n'
        '/start — menu · /files — your files · /stats — your stats\n'
        '/git LINK — deploy a repository · /redeem CODE — use a promo code\n'
        '/ping — latency · /myid — your ID · /cancel — cancel input',
        kb([btn('⭐ Plans', 'prem'), btn('🔙 Back', 'home')]))

def scr_upload(ctx, uid):
    cnt, lim = project_count(uid), file_limit(uid)
    text = ('📤 <b>Upload</b>\n\n'
            'Send the file right here in this chat:\n'
            '• <code>.py</code> — a Python script\n'
            '• <code>.js</code> — a Node.js script\n'
            '• <code>.zip</code> — a whole project\n'
            + ('• or deploy a public repository with <b>From Git</b>\n' if GIT_ENABLED else '') +
            f'\nMax size: {MAX_FILE_SIZE_MB} MB\n'
            f'Your files: {bar(cnt, lim)}')
    if cnt + pending_count(uid) >= lim:
        text += '\n\n⚠️ <b>You have reached your limit</b> — delete a file first.'
    ctx.draw(text, kb([btn('🔗 From Git', 'upg') if GIT_ENABLED else None, btn('📂 My Files', 'files:0')],
                      [btn('🔙 Back', 'home')]))

def scr_files(ctx, viewer, target, page=0):
    own = viewer == target
    projs = user_projects(target)
    pend = q("SELECT name, kind, target FROM approvals WHERE user_id=? AND status='pending' ORDER BY id",
             (target,))
    title = '📂 <b>Your files</b>' if own else f'📂 <b>Files of</b> <code>{target}</code>'
    back = 'home' if own else f'u:{target}'
    if not projs and not pend:
        ctx.draw(f'{title}\n\nNothing here yet.' + (' Send a file to get started.' if own else ''),
                 kb([btn('📤 Upload', 'up') if own else None, btn('🔙 Back', back)]))
        return
    shown, page, pages = paginate(projs, page)
    running = sum(1 for p in projs if is_running(p['id']))
    text = f'{title}  {bar(len(projs), file_limit(target))}\n🟢 Running: <b>{running}</b>'
    if pend:
        text += '\n\n⏳ <b>Waiting for review</b>\n' + '\n'.join(
            f"• <code>{esc(a['name'])}</code>"
            + (f" — change to <code>{esc(a['target'])}</code>" if a['kind'] == 'patch' else '')
            for a in pend[:10])
    if projs:
        text += '\n\nTap a file to manage it:'
    rows = []
    for p in shown:
        icon = '🟢' if is_running(p['id']) else ('🟡' if p['id'] in PENDING_RESTART else '🔴')
        rows.append([btn(f"{icon} {p['name'][:40]}", f"f:{p['id']}")])
    rows.append(nav_row('files:' if own else f'uf:{target}:', page, pages))
    rows.append([btn('📤 Upload', 'up') if own else None, btn('🔙 Back', back)])
    ctx.draw(text, kb(*rows))

def scr_file(ctx, viewer, p, note=None, show_tail=False):
    pid = p['id']
    run = RUNS.get(pid)
    running = is_running(pid)
    head = LANG.get(p['file_type'], p['file_type'])
    if p['main_file'] != p['name']:
        head += f" · entry <code>{esc(p['main_file'][:60])}</code>"
    lines = [f"⚙️ <b>{esc(p['name'])}</b>", head]
    if p['user_id'] != viewer:
        lines.append(f"👤 Owner: <code>{p['user_id']}</code>")
    lines += ['', f'<b>Status:</b> {proj_state(p)}']
    if running and run:
        if not run.rss:
            _sample_docker() if run.backend == 'docker' else _sample_runs()
        cpu, rss = run_stats(pid)
        cap = f' / {run.ram_mb} MB' if run.ram_mb else ''
        lines.append(f'⏱ Uptime: {human_dur(time.time() - run.started)} · PID <code>{run.proc.pid}</code>')
        lines.append(f'🧠 RAM: {human_size(rss) if rss else "—"}{cap} · ⚡ CPU: {cpu or 0:.1f}%')
    now = time.time()
    crashes = sum(1 for t in list(CRASHES.get(pid, ())) if now - t <= RESTART_WINDOW)
    lines.append(f"🔁 Crashes: {crashes}/{MAX_RESTARTS} (last {human_dur(RESTART_WINDOW)}) · "
                 f"▶️ Runs: {p['run_count']}")
    if not running and p['last_exit'] is not None:
        lines.append(f"🚪 Last exit code: {p['last_exit']}")
    lines.append(f"📅 Uploaded: {fmt_dt(p['upload_time'])}")
    if p['disk_bytes']:
        lines.append(f"💽 Disk: {human_size(p['disk_bytes'])}"
                     + (f' / {MAX_PROJECT_MB} MB' if MAX_PROJECT_MB else ''))
    if p['sched_kind']:
        lines.append(f'⏰ {schedule_label(p)}')
    if p['mute']:
        lines.append('🔕 Crash / finish messages are muted')
    if p['source']:
        lines.append(f"🔗 {esc(p['source'][:80])}")
    if is_admin(viewer):
        lines.append(f'Sandbox: {sandbox_label()}')
    if note:
        lines += ['', note]
    if show_tail:
        lines.append(f'<pre>{esc(tail_log(pid, 700)) or "(no output)"}</pre>')
    if running or pid in PENDING_RESTART:
        rows = [[btn('🔴 Stop', f'fx:{pid}'), btn('🔄 Restart', f'fr:{pid}')],
                [btn('📋 Logs', f'fl:{pid}'), btn('⌨️ Send input', f'fc:{pid}') if running else None],
                [btn('🔃 Refresh', f'f:{pid}'), btn('📥 Download', f'fg:{pid}')]]
    else:
        rows = [[btn('🟢 Start', f'fs:{pid}'), btn('📋 Logs', f'fl:{pid}')],
                [btn('🔃 Refresh', f'f:{pid}'), btn('📥 Download', f'fg:{pid}')]]
    rows.append([btn('📁 Files', f'fm:{pid}:0'), btn('⚙️ Settings', f'fset:{pid}')])
    if p['last_reason'] == 'modified' and is_admin(viewer):
        rows.append([btn('✅ Re-approve the code as it is now', f'fok:{pid}')])
    rows.append([btn('🗑 Delete', f'fd:{pid}'),
                 btn('🔙 Back', 'files:0' if p['user_id'] == viewer else f"uf:{p['user_id']}:0")])
    ctx.draw('\n'.join(lines), kb(*rows))

def logs_view(p, live=False):
    pid = p['id']
    body = esc(tail_log(pid)) or '(no output yet)'
    text = (f"📋 <b>{esc(p['name'])}</b> — {proj_state(p)}\n<pre>{body}</pre>\n"
            f"{'🔴 LIVE · ' if live else ''}updated {utcnow():%H:%M:%S} UTC")
    markup = kb([btn('🔄 Refresh', f'fl:{pid}'),
                 btn('⏹ Stop live', f'fl:{pid}') if live else btn('▶️ Live', f'fll:{pid}')],
                [btn('🔎 Search', f'fsr:{pid}'), btn('📥 Full log', f'fdl:{pid}')],
                [btn('🔙 Back', f'f:{pid}')])
    return text, markup

def _live_loop(ctx, pid, token):
    """Keep one message updated with fresh log output until stopped or timed out."""
    key = (ctx.chat_id, ctx.message_id)
    end, last = time.time() + LIVE_LOG_DURATION, None
    while time.time() < end and not SHUTDOWN.wait(LIVE_LOG_INTERVAL):
        if LIVE.get(key) is not token:
            return
        p = get_project(pid)
        if not p:
            break
        sig = (tail_log(pid), proj_state(p))
        if sig == last:
            continue
        last = sig
        text, markup = logs_view(p, live=True)
        try:
            bot.edit_message_text(text, ctx.chat_id, ctx.message_id, reply_markup=markup)
        except ApiTelegramException as e:
            if getattr(e, 'error_code', 0) == 429:
                wait = ((getattr(e, 'result_json', None) or {}).get('parameters') or {}).get('retry_after', 5)
                SHUTDOWN.wait(wait + 1)
            elif 'not modified' not in str(getattr(e, 'description', e)).lower():
                break
        except Exception:
            break
    if LIVE.get(key) is token:
        LIVE.pop(key, None)
        p = get_project(pid)
        if p:
            ctx.draw(*logs_view(p, live=False))

def schedule_label(p) -> str:
    kind, value = p['sched_kind'], p['sched_value']
    if kind == 'restart':
        return f"Restarts every {value}h (next: {fmt_dt(datetime.fromtimestamp(p['sched_next'] or 0, timezone.utc))})"
    if kind == 'run':
        return f'Starts every day at {value} UTC'
    return 'No schedule'

def _next_daily(hhmm) -> float:
    h, m = (int(v) for v in hhmm.split(':'))
    now = utcnow()
    due = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if due <= now:
        due += timedelta(days=1)
    return due.timestamp()

def set_schedule(pid, kind, value=''):
    """kind: '' (off), 'restart' (value = hours) or 'run' (value = 'HH:MM', UTC)."""
    nxt = None
    if kind == 'restart':
        nxt = time.time() + int(value) * 3600
    elif kind == 'run':
        nxt = _next_daily(value)
    x('UPDATE projects SET sched_kind=?, sched_value=?, sched_next=? WHERE id=?', (kind, str(value), nxt, pid))

def run_schedules():
    """Do what is due: restart running scripts, start the daily ones."""
    now = time.time()
    for p in q("SELECT * FROM projects WHERE COALESCE(sched_kind, '') != '' AND sched_next IS NOT NULL "
               "AND sched_next <= ?", (now,)):
        pid = p['id']
        try:
            if p['sched_kind'] == 'restart':
                x('UPDATE projects SET sched_next=? WHERE id=?', (now + int(p['sched_value']) * 3600, pid))
                if is_running(pid):
                    logger.info('Scheduled restart of project %s', pid)
                    stop_project(pid, keep_flag=True)
                    start_project(pid)
            else:
                x('UPDATE projects SET sched_next=? WHERE id=?', (_next_daily(p['sched_value']), pid))
                if not is_running(pid):
                    ok, msg = start_project(pid)
                    logger.info('Scheduled start of project %s: %s', pid, msg)
                    if not ok:
                        notify(p['user_id'], f"⏰ <b>{esc(p['name'])}</b> could not be started on schedule: "
                                             f'{esc(msg)}', _open_btn(pid))
        except Exception:
            logger.exception('Schedule failed for project %s', pid)
            x("UPDATE projects SET sched_kind='', sched_next=NULL WHERE id=?", (pid,))

def scr_settings(ctx, viewer, p, note=None):
    pid = p['id']
    n_env = q1('SELECT COUNT(*) n FROM project_env WHERE project_id=?', (pid,))['n']
    docker = sandbox_backend() == 'docker'
    lines = [f"⚙️ <b>Settings — {esc(p['name'])}</b>", '',
             f'🔑 Variables: <b>{n_env}</b>',
             f"▶️ Arguments: <code>{esc(p['args'])}</code>" if p['args'] else '▶️ Arguments: none',
             f'⏰ {schedule_label(p)}',
             f"🔔 Crash / finish messages: <b>{'muted' if p['mute'] else 'on'}</b>"]
    if docker:
        lines.append(f"🧬 Version: <b>{esc(p['runtime'] or 'default')}</b>")
    lines += ['', '<i>Variables, arguments and version apply the next time the script starts.</i>']
    if note:
        lines += ['', note]
    ctx.draw('\n'.join(lines), kb(
        [btn('🔑 Variables', f'fe:{pid}'), btn('▶️ Arguments', f'fa:{pid}')],
        [btn('⏰ Schedule', f'fsch:{pid}'),
         btn('🔔 Unmute messages' if p['mute'] else '🔕 Mute messages', f'fmute:{pid}')],
        [btn('🧬 Version', f'fver:{pid}') if docker else None],
        [btn('🔙 Back', f'f:{pid}')]))

def scr_env(ctx, p, note=None):
    pid = p['id']
    env = project_env(pid)
    lines = [f"🔑 <b>Variables — {esc(p['name'])}</b>", '',
             'Your script reads these like any environment variable '
             '(<code>os.environ["NAME"]</code> / <code>process.env.NAME</code>), so tokens and keys '
             'do not have to be written into the code.', '']
    for key, value in env.items():
        shown = (value[:2] + '•••••') if len(value) > 6 else '•••••'
        lines.append(f'• <code>{esc(key)}</code> = {esc(shown)}')
    if not env:
        lines.append('No variables yet.')
    lines += ['', f'{len(env)}/{MAX_ENV_VARS} used · restart the script to apply changes.']
    if note:
        lines += ['', note]
    ctx.draw('\n'.join(lines), kb([btn('➕ Add / change', f'fea:{pid}'),
                                   btn('➖ Remove', f'fer:{pid}') if env else None],
                                  [btn('🔙 Back', f'fset:{pid}')]))

def scr_schedule(ctx, p, note=None):
    pid = p['id']
    text = (f"⏰ <b>Schedule — {esc(p['name'])}</b>\n\nNow: <b>{schedule_label(p)}</b>\n\n"
            '• <b>Restart every …</b> restarts the script while it is running (fresh memory, new session).\n'
            '• <b>Start daily at …</b> is for scripts that do a job and exit; times are UTC '
            f'(now {utcnow():%H:%M} UTC).')
    if note:
        text += f'\n\n{note}'
    ctx.draw(text, kb([btn('🔄 Every 6h', f'fss:{pid}:r6'), btn('🔄 Every 12h', f'fss:{pid}:r12'),
                       btn('🔄 Every 24h', f'fss:{pid}:r24')],
                      [btn('▶️ Start daily at …', f'fsd:{pid}'), btn('🚫 No schedule', f'fss:{pid}:off')],
                      [btn('🔙 Back', f'fset:{pid}')]))

def project_files(pid) -> list:
    """Relative paths of a project's own files (packages and caches left out)."""
    root, out = project_dir(pid), []
    for cur, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
        for fn in sorted(files):
            full = os.path.join(cur, fn)
            if os.path.isfile(full) and not os.path.islink(full):
                out.append(os.path.relpath(full, root).replace(os.sep, '/'))
                if len(out) >= 400:
                    return out
    return out

def _fkey(rel) -> str:
    """Short stable id of a file for button data (paths are too long for buttons)."""
    return hashlib.md5(rel.encode('utf-8')).hexdigest()[:10]

def _file_by_key(pid, key):
    return next((rel for rel in project_files(pid) if _fkey(rel) == key), None)

def scr_file_manager(ctx, p, page=0, note=None):
    pid = p['id']
    files = project_files(pid)
    shown, page, pages = paginate(files, page)
    size = project_disk(pid)
    lines = [f"📁 <b>Files — {esc(p['name'])}</b>",
             f'💽 {human_size(size)}' + (f' of {MAX_PROJECT_MB} MB' if MAX_PROJECT_MB else '')
             + ' used (packages included)', '',
             'Tap a file to download or replace it. Changes to code are reviewed before they go live.']
    if note:
        lines += ['', note]
    rows = []
    for rel in shown:
        try:
            fsize = human_size(os.path.getsize(os.path.join(project_dir(pid), *rel.split('/'))))
        except OSError:
            fsize = '?'
        icon = '📜' if rel.lower().endswith(_CODE_EXT) else '📄'
        rows.append([btn(f"{icon} {('…' + rel[-34:]) if len(rel) > 35 else rel} · {fsize}",
                         f'fmf:{pid}:{_fkey(rel)}')])
    rows.append(nav_row(f'fm:{pid}:', page, pages))
    rows.append([btn('➕ Add a file', f'fmn:{pid}'), btn('🔙 Back', f'f:{pid}')])
    ctx.draw('\n'.join(lines), kb(*rows))

def scr_one_file(ctx, p, rel):
    pid = p['id']
    full = os.path.join(project_dir(pid), *rel.split('/'))
    try:
        st = os.stat(full)
        facts = f'{human_size(st.st_size)} · changed {fmt_dt(datetime.fromtimestamp(st.st_mtime, timezone.utc))}'
    except OSError:
        facts = 'missing'
    code = rel.lower().endswith(_CODE_EXT)
    key = _fkey(rel)
    ctx.draw(f"{'📜' if code else '📄'} <code>{esc(rel)}</code>\n{facts}\n\n"
             + ('This is code: a replacement is reviewed by an admin before it goes live.' if code
                else 'This is a data file.'),
             kb([btn('📥 Download', f'fmg:{pid}:{key}'), btn('♻️ Replace', f'fmr:{pid}:{key}')],
                [btn('🗑 Delete', f'fmd:{pid}:{key}') if not code else None],
                [btn('🔙 Back', f'fm:{pid}:0')]))

def scr_plans(ctx, user, note=None):
    uid = user.id
    lim = limits_for(uid)

    def facts(files, ram, cpus):
        return (f"{'∞' if files == float('inf') else int(files)} files"
                + (f' · {ram} MB RAM' if ram else '') + f' · {cpus:g} CPU')
    exp = sub_expiry(uid)
    lines = ['⭐ <b>Plans</b>', '',
             f"Your plan: <b>{status_str(uid)}</b>\n{facts(lim['files'], lim['ram'], lim['cpus'])} per script"]
    if exp and exp > utcnow():
        lines.append(f'Valid until {fmt_dt(exp)}')
    lines += ['', f'🆓 <b>Free</b> — {facts(FREE_USER_LIMIT, MAX_RAM_MB, MAX_CPUS)}']
    rows = []
    for key, pl in PLANS.items():
        price = f" — {pl['stars']} ⭐ / {pl.get('days', 30)} days" if STARS_ENABLED and pl.get('stars') else ''
        lines.append(f"{plan_name(key)} — "
                     f"{facts(pl.get('files', SUBSCRIBED_USER_LIMIT), pl.get('ram', MAX_RAM_MB) if MAX_RAM_MB else 0, pl.get('cpus', MAX_CPUS))}"
                     f'{price}')
        if price:
            rows.append([btn(f"Buy {plan_name(key)} · {pl['stars']} ⭐", f'buy:{key}')])
    if not STARS_ENABLED:
        lines += ['', f'To subscribe, contact {esc(YOUR_USERNAME)}.']
    u = q1('SELECT trial_used, ref_count FROM users WHERE user_id=?', (uid,))
    extra = []
    if TRIAL_DAYS and u and not u['trial_used'] and plan_key(uid) == 'free':
        extra.append(btn(f'🎁 Free {TRIAL_DAYS}-day trial', 'trial'))
    extra.append(btn('🎟 Redeem a code', 'redeem'))
    rows.append(extra)
    if REFERRAL_BONUS_DAYS:
        rows.append([btn('👥 Invite friends', 'ref')])
    rows.append([btn('🔙 Back', 'home')])
    if note:
        lines += ['', note]
    ctx.draw('\n'.join(lines), kb(*rows))

def redeem_code(uid, code):
    """Use a promo code once per user. Returns (ok, text)."""
    code = (code or '').strip().upper()
    promo = q1('SELECT * FROM promo WHERE code=?', (code,))
    if not promo:
        return False, '❌ That code does not exist.'
    with _DB_LOCK:
        if q1('SELECT 1 FROM promo_uses WHERE code=? AND user_id=?', (code, uid)):
            return False, 'ℹ️ You have already used this code.'
        if x('UPDATE promo SET used=used+1 WHERE code=? AND (max_uses=0 OR used < max_uses)',
             (code,)).rowcount != 1:
            return False, '❌ This code has been used up.'
        x('INSERT INTO promo_uses (code, user_id) VALUES (?,?)', (code, uid))
    plan, expiry = grant_sub(uid, promo['plan'], promo['days'])
    audit(uid, 'promo used', code, f"{promo['days']} days of {plan}")
    return True, (f"🎉 Code accepted: <b>{promo['days']} days</b> of {plan_name(plan)} "
                  f'(until {fmt_dt(expiry)}).')

def scr_stats(ctx, uid):
    u = q1('SELECT * FROM users WHERE user_id=?', (uid,))
    projs = user_projects(uid)
    running = sum(1 for p in projs if is_running(p['id']))
    text = (f"📊 <b>Your stats</b>\n\n"
            f"🆔 <code>{uid}</code> · {status_str(uid)}\n"
            f"📁 Files  {bar(len(projs), file_limit(uid))}\n"
            f"🟢 Running: {running}\n"
            f"⬆️ Uploads: {u['total_uploads'] if u else 0} · ▶️ Runs: {u['total_runs'] if u else 0}\n"
            f"📅 Member since: {fmt_dt(u['first_seen']) if u else '—'}")
    exp = sub_expiry(uid)
    if exp:
        text += f'\n💳 Subscription until: {fmt_dt(exp)}'
    if is_admin(uid):
        text += (f"\n\n🌐 <b>Global</b>\n"
                 f"👥 Users: {q1('SELECT COUNT(*) n FROM users')['n']} · "
                 f"📁 Files: {q1('SELECT COUNT(*) n FROM projects')['n']} · "
                 f"🟢 Running: {running_count()}")
    ctx.draw(text, kb([btn('🔙 Back', 'home')]))

# ════════════════════════════════════════════════════════════════════════════
#  SCREENS — ADMIN
# ════════════════════════════════════════════════════════════════════════════
def scr_review_panel(ctx):
    """What a reviewer (not a full admin) sees instead of the admin panel."""
    n_pend = pending_count()
    n_pkg = q1("SELECT COUNT(*) n FROM pkg_requests WHERE status='pending'")['n']
    ctx.draw(f'🔎 <b>Review Panel</b>\n\n⏳ Uploads waiting for review: <b>{n_pend}</b>\n'
             f'📦 Package requests: <b>{n_pkg}</b>\n\n'
             '<i>You can approve or reject uploads and packages. Everything else is for admins.</i>',
             kb([btn(f'⏳ Approvals ({n_pend})', 'ap:0'), btn(f'📦 Packages ({n_pkg})', 'pk')],
                [btn('🔙 Back', 'home')]))

def scr_admin(ctx, note=None, viewer=None):
    if viewer is not None and not is_admin(viewer):
        scr_review_panel(ctx)
        return
    try:
        vm = psutil.virtual_memory()
        host = (f'CPU {psutil.cpu_percent(interval=None):.0f}% · '
                f'RAM {human_size(vm.used)} / {human_size(vm.total)} ({vm.percent:.0f}%)')
    except Exception:                       # not readable on some hosts (e.g. Android)
        host = 'CPU / RAM: not available on this host'
    try:
        du = shutil.disk_usage(BASE_DIR)
        disk = f'{human_size(du.used)} / {human_size(du.total)}'
    except OSError:
        disk = '—'
    users = q1('SELECT COUNT(*) n, COALESCE(SUM(banned), 0) b FROM users')
    subs = sum(1 for r in q('SELECT expiry FROM subscriptions')
               if (parse_dt(r['expiry']) or utcnow()) > utcnow())
    n_pend, n_run = pending_count(), running_count()
    n_pkg = q1("SELECT COUNT(*) n FROM pkg_requests WHERE status='pending'")['n']
    text = (f"🛠 <b>Admin Panel</b>\n\n"
            f"🖥 {host}\n"
            f"💽 Disk {disk}\n"
            f"⏱ Bot uptime: {human_dur(time.time() - STATE['boot'])}\n"
            f"Sandbox: {sandbox_label()}\n\n"
            f"👥 Users: <b>{users['n']}</b> ({users['b']} banned) · ⭐ Subs: <b>{subs}</b>\n"
            f"📁 Files: <b>{q1('SELECT COUNT(*) n FROM projects')['n']}</b> · "
            f"🟢 Running: <b>{n_run}</b>\n"
            f"⏳ Waiting for review: <b>{n_pend}</b>\n"
            f"{'🔒 Bot is <b>LOCKED</b> for users' if STATE['locked'] else '🔓 Bot is open'}")
    if note:
        text += f'\n\n{note}'
    ctx.draw(text, kb(
        [btn(f'⏳ Approvals ({n_pend})', 'ap:0'), btn(f'📦 Packages ({n_pkg})', 'pk')],
        [btn(f'🖥 Running ({n_run})', 'run:0'), btn('👥 Users', 'users:0')],
        [btn('👤 Find user', 'ul'), btn('💳 Subscriptions', 'subs')],
        [btn('📢 Broadcast', 'bc'), btn('🔓 Unlock bot' if STATE['locked'] else '🔒 Lock bot', 'lock')],
        [btn('🟢 Run all', 'runall'), btn('🔴 Stop all', 'stopall')],
        [btn('📜 Audit log', 'audit:0'), btn('💾 Backup now', 'bkp')],
        [btn('👑 Admins', 'adms'), btn('🔃 Refresh', 'adm')],
        [btn('🔙 Back', 'home')]))

def scr_running(ctx, page=0):
    live = [(pid, run) for pid, run in sorted(RUNS.items()) if run.proc.poll() is None]
    shown, page, pages = paginate(live, page)
    lines = [f'🖥 <b>Running scripts</b> ({len(live)})', '']
    rows = []
    for pid, run in shown:
        p = get_project(pid)
        if not p:
            continue
        lines.append(f"• <b>{esc(p['name'][:30])}</b> · <code>{p['user_id']}</code>\n"
                     f"   🧠 {human_size(run.rss) if run.rss else '—'} · ⚡ {run.cpu:.0f}% · "
                     f"⏱ {human_dur(time.time() - run.started)}")
        rows.append([btn(f"⚙️ {p['name'][:36]}", f'f:{pid}')])
    if not live:
        lines.append('Nothing is running.')
    else:
        lines += ['', f'Total RAM: {human_size(sum(r.rss for _, r in live))}']
    rows.append(nav_row('run:', page, pages))
    rows.append([btn('🔃 Refresh', f'run:{page}'), btn('🔙 Back', 'adm')])
    ctx.draw('\n'.join(lines), kb(*rows))

def scr_approvals(ctx, page=0):
    pend = q("SELECT id, user_id, name FROM approvals WHERE status='pending' ORDER BY id")
    if not pend:
        ctx.draw('⏳ <b>Approvals</b>\n\n✅ Nothing is waiting for review.', kb([btn('🔙 Back', 'adm')]))
        return
    shown, page, pages = paginate(pend, page)
    rows = [[btn(f"📄 #{a['id']} {a['name'][:28]} · {a['user_id']}", f"a:{a['id']}")] for a in shown]
    rows.append(nav_row('ap:', page, pages))
    rows.append([btn('🔃 Refresh', f'ap:{page}'), btn('🔙 Back', 'adm')])
    ctx.draw(f'⏳ <b>Approvals</b> — {len(pend)} waiting\n\nTap one to review it:', kb(*rows))

def scr_user(ctx, target):
    u = q1('SELECT * FROM users WHERE user_id=?', (target,))
    if not u:
        ctx.draw(f'ℹ️ User <code>{target}</code> has never used this bot.', kb([btn('🔙 Back', 'adm')]))
        return
    projs = user_projects(target)
    running = sum(1 for p in projs if is_running(p['id']))
    exp = sub_expiry(target)
    text = (f"👤 <b>{esc(u['first_name'] or 'User')}</b>"
            + (f" · @{esc(u['username'])}" if u['username'] else '') + "\n"
            f"🆔 <code>{target}</code>\n"
            f"🔰 {status_str(target)}" + (' · 🚫 <b>BANNED</b>' if u['banned'] else '') + "\n"
            f"📁 Files  {bar(len(projs), file_limit(target))} · 🟢 {running} running\n"
            f"⬆️ Uploads: {u['total_uploads']} · ▶️ Runs: {u['total_runs']}\n"
            f"📅 First seen: {fmt_dt(u['first_seen'])}\n"
            f"🕐 Last active: {fmt_dt(u['last_active'])}\n"
            f"💳 Subscription: {fmt_dt(exp) if exp else 'none'}")
    can_ban = target != OWNER_ID and not is_admin(target)
    ban_btn = None
    if u['banned']:
        ban_btn = btn('✅ Unban', f'uunban:{target}')
    elif can_ban:
        ban_btn = btn('🚫 Ban', f'uban:{target}')
    ctx.draw(text, kb([btn(f'📂 Files ({len(projs)})', f'uf:{target}:0'), ban_btn],
                      [btn('🔙 Back', 'adm')]))

def scr_subs(ctx):
    now = utcnow()
    active = sorted(((parse_dt(r['expiry']), r['user_id'], r['plan']) for r in q('SELECT * FROM subscriptions')
                     if (parse_dt(r['expiry']) or now) > now), key=lambda t: t[0])
    lines = [f'💳 <b>Subscriptions</b> — {len(active)} active', '']
    for exp, uid, plan in active[:10]:
        lines.append(f'• <code>{uid}</code> — {plan_name(plan)} · {(exp - now).days}d left ({exp:%Y-%m-%d})')
    if len(active) > 10:
        lines.append(f'… and {len(active) - 10} more')
    lines += ['', f"Stars payments: <b>{'on' if STARS_ENABLED else 'off'}</b> (STARS_ENABLED)"]
    ctx.draw('\n'.join(lines), kb([btn('➕ Add / extend', 'sadd'), btn('➖ Remove', 'srem')],
                                  [btn('🔍 Check a user', 'schk'), btn('🎟 Promo codes', 'promo')],
                                  [btn('🔙 Back', 'adm')]))

def scr_promo(ctx, note=None):
    codes = q('SELECT * FROM promo ORDER BY code')
    lines = ['🎟 <b>Promo codes</b>', '']
    for r in codes[:25]:
        limit = '∞' if not r['max_uses'] else r['max_uses']
        lines.append(f"• <code>{esc(r['code'])}</code> — {r['days']} days of {plan_name(r['plan'])} · "
                     f"used {r['used']}/{limit}")
    if not codes:
        lines.append('No codes yet.')
    lines += ['', 'Users redeem a code under ⭐ Plans or with <code>/redeem CODE</code>; each user once.']
    if note:
        lines += ['', note]
    ctx.draw('\n'.join(lines), kb([btn('➕ New code', 'promoadd'), btn('➖ Delete a code', 'promodel') if codes else None],
                                  [btn('🔙 Back', 'subs')]))

def scr_packages(ctx, viewer):
    reqs = q("SELECT * FROM pkg_requests WHERE status='pending' ORDER BY id")
    allowed = q('SELECT lang, name FROM allowed_packages ORDER BY lang, name')
    lines = ['📦 <b>Packages</b>', '']
    if AUTO_INSTALL_UNKNOWN:
        lines.append('Every package may be installed automatically (AUTO_INSTALL_UNKNOWN is on).')
    else:
        lines.append('Well-known packages install by themselves. Anything else needs an OK here.')
    lines += ['', f'<b>Waiting ({len(reqs)})</b>']
    rows = []
    for r in reqs[:8]:
        proj = get_project(r['project_id'])
        lines.append(f"• #{r['id']} <code>{esc(r['pkg'])}</code> ({'pip' if r['lang'] == 'py' else 'npm'}) — "
                     f"{esc(proj['name'][:30]) if proj else 'deleted file'}")
        rows.append([btn(f"✅ {r['pkg'][:20]}", f"pky:{r['id']}"), btn('❌ Deny', f"pkn:{r['id']}")])
    if not reqs:
        lines.append('Nothing is waiting.')
    shown = ', '.join(f"{esc(a['name'])} ({'pip' if a['lang'] == 'py' else 'npm'})" for a in allowed[:40])
    lines += ['', f'<b>Allowed by admins ({len(allowed)})</b>', shown or '—']
    if is_admin(viewer):
        rows.append([btn('➕ Allow a package', 'pkadd'), btn('➖ Remove', 'pkdel')])
    rows.append([btn('🔃 Refresh', 'pk'), btn('🔙 Back', 'adm')])
    ctx.draw('\n'.join(lines), kb(*rows))

def scr_admins(ctx, viewer):
    lines = ['👑 <b>Admins and reviewers</b>', '']
    for a in sorted(staff_ids()):
        u = q1('SELECT first_name FROM users WHERE user_id=?', (a,))
        role = '👑 owner' if a == OWNER_ID else ('🛡 admin' if a in admin_ids else '🔎 reviewer')
        lines.append(f"• <code>{a}</code> {esc((u['first_name'] if u else '') or '')} — {role}")
    lines += ['', '<b>Admins</b> can do everything except change this list.',
              '<b>Reviewers</b> can only approve or reject uploads and package requests.']
    rows = []
    if viewer == OWNER_ID:
        rows.append([btn('➕ Admin', 'aadd'), btn('➕ Reviewer', 'radd')])
        rows.append([btn('➖ Remove someone', 'arem')])
    else:
        lines += ['', '<i>Only the owner can change this list.</i>']
    rows.append([btn('🔙 Back', 'adm')])
    ctx.draw('\n'.join(lines), kb(*rows))

def scr_users(ctx, page=0):
    total = q1('SELECT COUNT(*) n FROM users')['n']
    per = FILES_PER_PAGE
    pages = max(1, math.ceil(total / per))
    page = min(max(0, page or 0), pages - 1)
    rows = []
    for u in q('SELECT * FROM users ORDER BY last_active DESC LIMIT ? OFFSET ?', (per, page * per)):
        uid = u['user_id']
        icon = '🚫' if u['banned'] else {'owner': '👑', 'admin': '🛡', 'free': '🆓'}.get(plan_key(uid), '⭐')
        rows.append([btn(f"{icon} {(u['first_name'] or 'User')[:20]} · {uid} · {project_count(uid)} files",
                         f'u:{uid}')])
    rows.append(nav_row('users:', page, pages))
    rows.append([btn('📤 Export CSV', 'uexp'), btn('🔙 Back', 'adm')])
    ctx.draw(f'👥 <b>Users</b> — {total} in total, most recently active first.\n\nTap one to open it.', kb(*rows))

def users_csv() -> bytes:
    def cell(value):
        value = str(value or '')
        return "'" + value if value[:1] in ('=', '+', '-', '@') else value
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(['user_id', 'first_name', 'username', 'plan', 'subscription_until', 'files', 'uploads',
                'runs', 'first_seen', 'last_active', 'banned', 'blocked_bot', 'invited_by', 'invites'])
    for u in q('SELECT * FROM users ORDER BY user_id'):
        uid = u['user_id']
        exp = sub_expiry(uid)
        w.writerow([uid, cell(u['first_name']), cell(u['username']), plan_key(uid),
                    exp.isoformat() if exp else '', project_count(uid), u['total_uploads'], u['total_runs'],
                    u['first_seen'] or '', u['last_active'] or '', u['banned'], u['blocked'] or 0,
                    u['referred_by'] or '', u['ref_count'] or 0])
    return out.getvalue().encode('utf-8-sig')

def scr_audit(ctx, page=0):
    total = q1('SELECT COUNT(*) n FROM audit')['n']
    per = 10
    pages = max(1, math.ceil(total / per))
    page = min(max(0, page or 0), pages - 1)
    lines = [f'📜 <b>Audit log</b> — {total} entries, newest first', '']
    for r in q('SELECT * FROM audit ORDER BY id DESC LIMIT ? OFFSET ?', (per, page * per)):
        u = q1('SELECT first_name FROM users WHERE user_id=?', (r['actor'],))
        who = esc(((u['first_name'] if u else '') or str(r['actor']))[:16])
        when = datetime.fromtimestamp(r['ts'], timezone.utc).strftime('%m-%d %H:%M')
        lines.append(f"<code>{when}</code> {who} (<code>{r['actor']}</code>) — <b>{esc(r['action'])}</b> "
                     f"{esc(r['target'] or '')}" + (f" · {esc(r['detail'][:90])}" if r['detail'] else ''))
    if not total:
        lines.append('Nothing recorded yet.')
    ctx.draw('\n'.join(lines), kb(nav_row('audit:', page, pages),
                                  [btn('🔃 Refresh', f'audit:{page}'), btn('🔙 Back', 'adm')]))

# ─── broadcast ───────────────────────────────────────────────────────────────
_BC_TARGETS = {'all': '👥 Everyone', 'subs': '⭐ Subscribers', 'free': '🆓 Free users'}

def broadcast_targets(target) -> list:
    """User ids a broadcast goes to (banned users and users who blocked the bot are left out)."""
    now = utcnow()
    subs = {r['user_id'] for r in q('SELECT user_id, expiry FROM subscriptions')
            if (parse_dt(r['expiry']) or now) > now}
    ids = [r['user_id'] for r in q('SELECT user_id FROM users WHERE banned=0 AND COALESCE(blocked, 0)=0')]
    if target == 'subs':
        return [i for i in ids if i in subs]
    if target == 'free':
        return [i for i in ids if i not in subs]
    return ids

def scr_broadcast(ctx, note=None):
    sched = q("SELECT * FROM scheduled_broadcasts WHERE status='pending' ORDER BY due")
    lines = ['📢 <b>Broadcast</b>', '',
             f"Reachable users: <b>{len(broadcast_targets('all'))}</b> "
             f"(⭐ {len(broadcast_targets('subs'))} · 🆓 {len(broadcast_targets('free'))})", '']
    rows = [[btn('✍️ New broadcast', 'bcnew')]]
    if sched:
        lines.append('<b>Scheduled</b>')
        for r in sched[:8]:
            when = fmt_dt(datetime.fromtimestamp(r['due'], timezone.utc))
            lines.append(f"• #{r['id']} — {when} → {_BC_TARGETS.get(r['target'], r['target'])}")
            rows.append([btn(f"✖️ Cancel #{r['id']}", f"bcx:{r['id']}")])
    else:
        lines.append('Nothing is scheduled.')
    if note:
        lines += ['', note]
    rows.append([btn('🔙 Back', 'adm')])
    ctx.draw('\n'.join(lines), kb(*rows))

def scr_broadcast_confirm(ctx, admin_id):
    job = BROADCASTS.get(admin_id)
    if not job:
        scr_broadcast(ctx, note='⌛ That broadcast is gone — start a new one.')
        return
    n = len(broadcast_targets(job['target']))
    ctx.draw(f"📢 Send the message above to <b>{n}</b> users?\nTarget: <b>{_BC_TARGETS[job['target']]}</b>",
             kb([btn(('✅ ' if job['target'] == k else '') + label, f'bct:{k}') for k, label in _BC_TARGETS.items()],
                [btn('📨 Send now', 'bcy'), btn('🕐 Schedule', 'bcs')],
                [btn('✖️ Cancel', 'bcn')]))

def run_scheduled_broadcasts():
    for r in q("SELECT * FROM scheduled_broadcasts WHERE status='pending' AND due <= ?", (time.time(),)):
        if x("UPDATE scheduled_broadcasts SET status='sent' WHERE id=? AND status='pending'",
             (r['id'],)).rowcount == 1:
            threading.Thread(target=_execute_broadcast, daemon=True,
                             args=((r['from_chat'], r['message_id']), Ctx(r['admin_id']), r['target'])).start()

# ─── backups, daily report, alerts ───────────────────────────────────────────
def backup_database() -> str:
    """Write a consistent copy of the database to data/backups/ (zipped). Returns its path."""
    stamp = f'{utcnow():%Y%m%d_%H%M%S}'
    raw = os.path.join(BACKUP_DIR, f'hostbot_{stamp}.db')
    with _DB_LOCK:
        dst = sqlite3.connect(raw)
        try:
            _db().backup(dst)
        finally:
            dst.close()
    path = raw + '.zip'
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.write(raw, os.path.basename(raw))
    os.remove(raw)
    old = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith('hostbot_') and f.endswith('.zip'))
    for name in old[:-max(1, int(BACKUP_KEEP))]:
        try:
            os.remove(os.path.join(BACKUP_DIR, name))
        except OSError:
            pass
    return path

def send_backup(chat_id, path) -> bool:
    if os.path.getsize(path) > 45 * 1024 * 1024:
        notify(chat_id, f'💾 Backup saved on the server (too large for Telegram): <code>{esc(path)}</code>')
        return True
    try:
        with open(path, 'rb') as f:
            bot.send_document(chat_id, f, caption='💾 <b>Database backup</b>\nUsers, subscriptions, files '
                              'and settings. Keep it private — it also holds the variables users set.',
                              visible_file_name=os.path.basename(path))
        return True
    except Exception as e:
        logger.warning('Could not send the backup: %s', e)
        return False

def scheduled_backup():
    if not BACKUP_INTERVAL_HOURS:
        return
    if time.time() - float(get_setting('last_backup', '0')) < BACKUP_INTERVAL_HOURS * 3600:
        return
    set_setting('last_backup', time.time())
    send_backup(OWNER_ID, backup_database())

def _host_usage():
    """(disk %, RAM %) of the server, None where it cannot be read."""
    try:
        du = shutil.disk_usage(BASE_DIR)
        disk = du.used * 100 / du.total
    except OSError:
        disk = None
    try:
        ram = psutil.virtual_memory().percent
    except Exception:
        ram = None
    return disk, ram

def _alert(key, text):
    if time.time() - _last_alert.get(key, 0) < ALERT_COOLDOWN:
        return
    _last_alert[key] = time.time()
    notify(OWNER_ID, f'🚨 <b>Alert</b>\n{text}', kb([btn('🛠 Admin Panel', 'adm')]))

def check_alerts():
    disk, ram = _host_usage()
    if disk is not None and ALERT_DISK_PERCENT and disk >= ALERT_DISK_PERCENT:
        _alert('disk', f'The disk is {disk:.0f}% full.')
    if ram is not None and ALERT_RAM_PERCENT and ram >= ALERT_RAM_PERCENT:
        _alert('ram', f"The server's RAM is {ram:.0f}% used ({running_count()} scripts running).")
    if ALERT_CRASH_LOOPS:
        n = count_events('gave_up', time.time() - 3600)
        if n >= ALERT_CRASH_LOOPS:
            _alert('crashes', f'{n} scripts crashed repeatedly and were given up on within the last hour.')

def daily_report_text() -> str:
    since = time.time() - 86400
    since_iso = (utcnow() - timedelta(days=1)).isoformat(timespec='seconds')
    now = utcnow()
    subs = [(parse_dt(r['expiry']), r['user_id']) for r in q('SELECT user_id, expiry FROM subscriptions')]
    active = [s for s in subs if s[0] and s[0] > now]
    ending = [str(uid) for exp, uid in active if exp <= now + timedelta(days=SUB_REMIND_DAYS)]
    disk, ram = _host_usage()
    stars = q1('SELECT COALESCE(SUM(stars), 0) n FROM payments WHERE ts>=?', (since,))['n']
    lines = [f'📊 <b>Daily report</b> — {now:%Y-%m-%d}', '',
             f"👥 New users: <b>{q1('SELECT COUNT(*) n FROM users WHERE first_seen>=?', (since_iso,))['n']}</b>"
             f" (total {q1('SELECT COUNT(*) n FROM users')['n']})",
             f"📨 Review requests: <b>{q1('SELECT COUNT(*) n FROM approvals WHERE submitted_at>=?', (since,))['n']}</b>"
             f" · approved {q1('SELECT COUNT(*) n FROM audit WHERE action=? AND ts>=?', ('approved', since))['n']}"
             f" · rejected {q1('SELECT COUNT(*) n FROM audit WHERE action=? AND ts>=?', ('rejected', since))['n']}"
             f" · waiting {pending_count()}",
             f"▶️ Starts: {count_events('start', since)} · 💥 Crashes: <b>{count_events('crash', since)}</b>"
             f" · ❌ Gave up: {count_events('gave_up', since)}",
             f"🟢 Running now: <b>{running_count()}</b> of {q1('SELECT COUNT(*) n FROM projects')['n']} files",
             f'💳 Active subscriptions: <b>{len(active)}</b>'
             + (f" · ending within {SUB_REMIND_DAYS} days: {', '.join(ending[:8])}" if ending else '')]
    if stars:
        lines.append(f'💰 Stars received: <b>{stars}</b> ⭐')
    lines.append('💽 Disk ' + (f'{disk:.0f}%' if disk is not None else '—')
                 + ' · 🧠 RAM ' + (f'{ram:.0f}%' if ram is not None else '—'))
    return '\n'.join(lines)

def daily_report():
    if DAILY_REPORT_HOUR_UTC is None or DAILY_REPORT_HOUR_UTC < 0:
        return
    now = utcnow()
    today = f'{now:%Y-%m-%d}'
    if now.hour < DAILY_REPORT_HOUR_UTC or get_setting('last_report') == today:
        return
    set_setting('last_report', today)
    notify(OWNER_ID, daily_report_text(), kb([btn('🛠 Admin Panel', 'adm')]))
    x('DELETE FROM events WHERE ts < ?', (time.time() - 30 * 86400,))

# ════════════════════════════════════════════════════════════════════════════
#  COMMANDS
# ════════════════════════════════════════════════════════════════════════════
def _private(m):
    return m.chat.type == 'private'

@bot.message_handler(commands=['start'], func=_private)
@guard
def cmd_start(m):
    uid = m.from_user.id
    AWAIT.pop(uid, None)
    parts = (m.text or '').split(maxsplit=1)
    if len(parts) == 2 and parts[1].startswith('ref'):       # t.me/<bot>?start=ref<user id>
        ref = to_int(parts[1][3:])
        if ref and ref != uid and not q1('SELECT 1 FROM users WHERE user_id=?', (uid,)):
            if len(_pending_ref) > 5000:
                _pending_ref.clear()
            _pending_ref[uid] = ref
    if gate(m):
        scr_home(Ctx(m.chat.id), m.from_user)

@bot.message_handler(commands=['help'], func=_private)
@guard
def cmd_help(m):
    if gate(m):
        scr_help(Ctx(m.chat.id))

@bot.message_handler(commands=['files'], func=_private)
@guard
def cmd_files(m):
    if gate(m):
        scr_files(Ctx(m.chat.id), m.from_user.id, m.from_user.id)

@bot.message_handler(commands=['stats'], func=_private)
@guard
def cmd_stats(m):
    if gate(m):
        scr_stats(Ctx(m.chat.id), m.from_user.id)

@bot.message_handler(commands=['admin', 'users'], func=_private)
@guard
def cmd_admin(m):
    if not is_staff(m.from_user.id):
        notify(m.chat.id, '⚠️ Admins only.')
        return
    scr_admin(Ctx(m.chat.id), viewer=m.from_user.id)

@bot.message_handler(commands=['git'], func=_private)
@guard
def cmd_git(m):
    if not gate(m):
        return
    parts = (m.text or '').split(maxsplit=1)
    if len(parts) < 2:
        notify(m.chat.id, 'Usage: <code>/git https://github.com/user/repository</code>')
        return
    handle_git(Ctx(m.chat.id), m.from_user, parts[1].strip())

@bot.message_handler(commands=['redeem'], func=_private)
@guard
def cmd_redeem(m):
    if not gate(m):
        return
    parts = (m.text or '').split(maxsplit=1)
    if len(parts) < 2:
        notify(m.chat.id, 'Usage: <code>/redeem CODE</code>')
        return
    ok, text = redeem_code(m.from_user.id, parts[1])
    notify(m.chat.id, text, kb([btn('⭐ Plans', 'prem')]))

@bot.message_handler(commands=['myid'], func=_private)
@guard
def cmd_myid(m):
    notify(m.chat.id, f'🆔 Your ID: <code>{m.from_user.id}</code>')

@bot.message_handler(commands=['ping'], func=_private)
@guard
def cmd_ping(m):
    t = time.time()
    ctx = Ctx(m.chat.id)
    ctx.draw('🏓 Pong…')
    ctx.draw(f'🏓 Pong! <code>{(time.time() - t) * 1000:.0f} ms</code>')

@bot.message_handler(commands=['cancel'], func=_private)
@guard
def cmd_cancel(m):
    had = AWAIT.pop(m.from_user.id, None) or BROADCASTS.pop(m.from_user.id, None)
    notify(m.chat.id, '✖️ Cancelled.' if had else 'Nothing to cancel.', kb([btn('🏠 Menu', 'home')]))

@bot.message_handler(commands=['stopstream'], func=_private)
@guard
def cmd_stopstream(m):
    keys = [k for k in list(LIVE) if k[0] == m.chat.id]
    for k in keys:
        LIVE.pop(k, None)
    notify(m.chat.id, '⏹ Live log stopped.' if keys else 'No live log is running.')

# ════════════════════════════════════════════════════════════════════════════
#  TYPED ANSWERS  (prompts opened with ask())
# ════════════════════════════════════════════════════════════════════════════
INPUTS = {}

def on_input(kind):
    def deco(fn):
        INPUTS[kind] = fn
        return fn
    return deco

def _take_await(m, kind=None):
    st = AWAIT.get(m.from_user.id)
    if not st or (kind and st['kind'] != kind):
        return None
    AWAIT.pop(m.from_user.id, None)
    return st if st['exp'] >= time.time() else None

_ANY_CONTENT = ['text', 'photo', 'video', 'document', 'audio', 'voice', 'sticker',
                'animation', 'video_note', 'location', 'contact', 'poll']

@bot.message_handler(content_types=_ANY_CONTENT,
                     func=lambda m: _private(m) and (AWAIT.get(m.from_user.id) or {}).get('kind') == 'broadcast')
@guard
def on_broadcast_message(m):
    """The message an admin wants to broadcast (any type) — ask for confirmation."""
    uid = m.from_user.id
    st = _take_await(m, 'broadcast')
    if not is_admin(uid):
        return
    if not st:
        notify(m.chat.id, '⌛ That took too long — open Broadcast again.', kb([btn('🛠 Admin Panel', 'adm')]))
        return
    BROADCASTS[uid] = {'src': (m.chat.id, m.message_id), 'target': 'all'}
    scr_broadcast_confirm(Ctx(m.chat.id), uid)

@on_input('git_url')
def in_git_url(m, st):
    if gate(m):
        handle_git(Ctx(m.chat.id), m.from_user, (m.text or '').strip())

def _own_project(m, pid):
    """The project a typed answer refers to, if the sender may manage it."""
    proj = get_project(pid)
    if not proj or (proj['user_id'] != m.from_user.id and not is_admin(m.from_user.id)):
        notify(m.chat.id, '⛔ Permission denied.')
        return None
    return proj

@on_input('env_set')
def in_env_set(m, st):
    proj = _own_project(m, st['arg'])
    if not proj:
        return
    pid = proj['id']
    try:
        bot.delete_message(m.chat.id, m.message_id)      # the message may contain a secret
    except Exception:
        pass
    key, eq, value = (m.text or '').partition('=')
    key, value = key.strip(), value.strip()
    ctx = Ctx(m.chat.id)
    if not eq or not valid_env_key(key):
        scr_env(ctx, proj, note='⚠️ Use the form <code>NAME=value</code>. Names use letters, digits and '
                                '_ and a few (PATH, HOME, PYTHONPATH …) are reserved.')
        return
    if len(value) > 2000:
        scr_env(ctx, proj, note='⚠️ That value is too long (max 2000 characters).')
        return
    env = project_env(pid)
    if key not in env and len(env) >= MAX_ENV_VARS:
        scr_env(ctx, proj, note=f'⚠️ You can set at most {MAX_ENV_VARS} variables.')
        return
    x('INSERT OR REPLACE INTO project_env (project_id, key, value) VALUES (?,?,?)', (pid, key, value))
    scr_env(ctx, proj, note=f'✅ <code>{esc(key)}</code> saved (your message was deleted). '
                            'Restart the script to apply.')

@on_input('env_del')
def in_env_del(m, st):
    proj = _own_project(m, st['arg'])
    if not proj:
        return
    key = (m.text or '').strip()
    done = x('DELETE FROM project_env WHERE project_id=? AND key=?', (proj['id'], key)).rowcount
    scr_env(Ctx(m.chat.id), proj, note=f'✅ <code>{esc(key)}</code> removed.' if done
            else 'ℹ️ There is no variable with that name.')

@on_input('args')
def in_args(m, st):
    proj = _own_project(m, st['arg'])
    if not proj:
        return
    text = (m.text or '').strip()
    if text in ('-', 'none', 'None'):
        text = ''
    ctx = Ctx(m.chat.id)
    try:
        shlex.split(text, posix=os.name != 'nt')
    except ValueError:
        scr_settings(ctx, m.from_user.id, proj, note='⚠️ Those arguments have an unclosed quote.')
        return
    if len(text) > 300:
        scr_settings(ctx, m.from_user.id, proj, note='⚠️ Too long (max 300 characters).')
        return
    x('UPDATE projects SET args=? WHERE id=?', (text, proj['id']))
    scr_settings(ctx, m.from_user.id, get_project(proj['id']),
                 note='✅ Arguments saved. Restart the script to apply.')

@on_input('sched_daily')
def in_sched_daily(m, st):
    proj = _own_project(m, st['arg'])
    if not proj:
        return
    mt = re.match(r'^\s*(\d{1,2}):(\d{2})\s*$', m.text or '')
    ctx = Ctx(m.chat.id)
    if not mt or int(mt.group(1)) > 23 or int(mt.group(2)) > 59:
        scr_schedule(ctx, proj, note='⚠️ Send the time as <code>HH:MM</code>, e.g. <code>06:30</code>.')
        return
    set_schedule(proj['id'], 'run', f'{int(mt.group(1)):02d}:{mt.group(2)}')
    scr_schedule(ctx, get_project(proj['id']), note='✅ Saved.')

@on_input('log_search')
def in_log_search(m, st):
    proj = _own_project(m, st['arg'])
    if not proj:
        return
    needle = (m.text or '').strip()[:100]
    pid = proj['id']
    markup = kb([btn('🔎 Search again', f'fsr:{pid}'), btn('📋 Logs', f'fl:{pid}')])
    if len(needle) < 2:
        notify(m.chat.id, '⚠️ Send at least 2 characters.', markup)
        return
    total, hits = search_log(pid, needle)
    body = esc('\n'.join(hits))[-3000:] or '(nothing found)'
    notify(m.chat.id, f"🔎 <b>{esc(needle)}</b> in {esc(proj['name'])}: <b>{total}</b> matching line(s)"
                      + (f', last {len(hits)} shown' if total > len(hits) else '')
                      + f'\n<pre>{body}</pre>', markup)

@on_input('redeem')
def in_redeem(m, st):
    ok, text = redeem_code(m.from_user.id, m.text)
    notify(m.chat.id, text, kb([btn('⭐ Plans', 'prem')]))

@on_input('stdin')
def in_stdin(m, st):
    pid, uid = st['arg'], m.from_user.id
    p = get_project(pid)
    back = kb([btn('🔙 Back to file', f'f:{pid}')])
    if not p or (p['user_id'] != uid and not is_admin(uid)):
        notify(m.chat.id, '⛔ Permission denied.')
        return
    run = RUNS.get(pid)
    if not run or run.proc.poll() is not None:
        notify(m.chat.id, '❌ The script is no longer running.', back)
        return
    text = (m.text or '')[:1000]
    try:
        run.proc.stdin.write(text.encode('utf-8') + b'\n')
        run.proc.stdin.flush()
        notify(m.chat.id, f'✅ Sent to <b>{esc(p["name"])}</b>:\n<code>{esc(text[:300])}</code>', back)
    except (OSError, ValueError) as e:
        notify(m.chat.id, f'❌ Could not send: {esc(e)}', back)

@on_input('reject_reason')
def in_reject_reason(m, st):
    if not is_staff(m.from_user.id):
        return
    ok, text = decide(st['arg'], m.from_user, False, reason=(m.text or '').strip()[:300])
    notify(m.chat.id, text if ok else f'ℹ️ {esc(text)}', kb([btn('⏳ Approvals', 'ap:0')]))

@on_input('bc_when')
def in_broadcast_when(m, st):
    uid = m.from_user.id
    if not is_admin(uid):
        return
    job = BROADCASTS.get(uid)
    ctx = Ctx(m.chat.id)
    if not job:
        scr_broadcast(ctx, note='⌛ That broadcast is gone — start a new one.')
        return
    text = (m.text or '').strip()
    due = None
    mt = re.match(r'^(\d{1,2}):(\d{2})$', text)
    if mt and int(mt.group(1)) < 24 and int(mt.group(2)) < 60:
        due = _next_daily(f'{int(mt.group(1)):02d}:{mt.group(2)}')
    elif re.match(r'^\+\d{1,5}$', text):
        due = time.time() + int(text[1:]) * 60
    if due is None:
        AWAIT[uid] = dict(st, exp=time.time() + INPUT_TIMEOUT)
        notify(m.chat.id, '⚠️ Send <code>HH:MM</code> (UTC) or <code>+minutes</code>.',
               kb([btn('✖️ Cancel', 'bc')]))
        return
    BROADCASTS.pop(uid, None)
    x('INSERT INTO scheduled_broadcasts (admin_id, from_chat, message_id, target, due) VALUES (?,?,?,?,?)',
      (uid, job['src'][0], job['src'][1], job['target'], due))
    when = fmt_dt(datetime.fromtimestamp(due, timezone.utc))
    audit(uid, 'broadcast scheduled', _BC_TARGETS[job['target']], when)
    scr_broadcast(ctx, note=f'🕐 Scheduled for {when}. Do not delete the message — it is copied from this chat.')

@on_input('sub_add')
def in_sub_add(m, st):
    if not is_admin(m.from_user.id):
        return
    parts = (m.text or '').split()
    uid, days = (to_int(parts[0]), to_int(parts[1])) if len(parts) in (2, 3) else (None, None)
    plan = parts[2].lower() if len(parts) == 3 else first_plan()
    if not uid or not days or uid <= 0 or not 0 < days <= 3650 or plan not in PLANS:
        AWAIT[m.from_user.id] = dict(st, exp=time.time() + INPUT_TIMEOUT)
        notify(m.chat.id, '⚠️ Use the format <code>USER_ID DAYS</code> or <code>USER_ID DAYS PLAN</code>, '
                          f"e.g. <code>123456789 30</code>. Plans: {esc(', '.join(PLANS))}.",
               kb([btn('✖️ Cancel', 'subs')]))
        return
    plan, expiry = grant_sub(uid, plan, days)
    audit(m.from_user.id, 'subscription added', f'user {uid}', f'{days} days of {plan}')
    notify(m.chat.id, f'✅ <code>{uid}</code>: +{days} days of {plan_name(plan)} → {fmt_dt(expiry)}',
           kb([btn('💳 Subscriptions', 'subs')]))
    notify(uid, f'🎉 Your subscription was extended by <b>{days} days</b> — {plan_name(plan)} '
                f'until {fmt_dt(expiry)}.')

@on_input('promo_add')
def in_promo_add(m, st):
    if not is_admin(m.from_user.id):
        return
    parts = (m.text or '').split()
    ctx = Ctx(m.chat.id)
    code = parts[0].upper() if parts else ''
    days = to_int(parts[1]) if len(parts) > 1 else None
    plan = parts[2].lower() if len(parts) > 2 else first_plan()
    uses = to_int(parts[3]) if len(parts) > 3 else 0
    if (not re.match(r'^[A-Z0-9_-]{3,32}$', code) or not days or not 0 < days <= 3650
            or plan not in PLANS or uses is None or uses < 0 or len(parts) > 4):
        scr_promo(ctx, note='⚠️ Use <code>CODE DAYS [PLAN] [MAX_USES]</code>, e.g. '
                            f"<code>WELCOME 7</code> or <code>VIP30 30 {esc(list(PLANS)[-1] if PLANS else 'premium')} 100</code>.")
        return
    x('INSERT OR REPLACE INTO promo (code, plan, days, max_uses, used) VALUES (?,?,?,?,0)',
      (code, plan, days, uses))
    x('DELETE FROM promo_uses WHERE code=?', (code,))
    audit(m.from_user.id, 'promo created', code, f'{days} days of {plan}, max uses {uses or "unlimited"}')
    scr_promo(ctx, note=f'✅ Code <code>{esc(code)}</code> created.')

@on_input('promo_del')
def in_promo_del(m, st):
    if not is_admin(m.from_user.id):
        return
    code = (m.text or '').strip().upper()
    done = x('DELETE FROM promo WHERE code=?', (code,)).rowcount
    x('DELETE FROM promo_uses WHERE code=?', (code,))
    if done:
        audit(m.from_user.id, 'promo deleted', code)
    scr_promo(Ctx(m.chat.id), note=f'✅ <code>{esc(code)}</code> deleted.' if done else 'ℹ️ No such code.')

@on_input('sub_rem')
def in_sub_rem(m, st):
    if not is_admin(m.from_user.id):
        return
    uid = to_int(m.text)
    back = kb([btn('💳 Subscriptions', 'subs')])
    if not uid:
        notify(m.chat.id, '⚠️ That is not a user ID.', back)
    elif db_remove_sub(uid):
        audit(m.from_user.id, 'subscription removed', f'user {uid}')
        notify(m.chat.id, f'✅ Subscription removed for <code>{uid}</code>.', back)
        notify(uid, 'ℹ️ Your subscription was removed by an admin.')
    else:
        notify(m.chat.id, f'ℹ️ <code>{uid}</code> has no subscription.', back)

@on_input('sub_chk')
def in_sub_chk(m, st):
    if not is_admin(m.from_user.id):
        return
    uid = to_int(m.text)
    back = kb([btn('💳 Subscriptions', 'subs')])
    if not uid:
        notify(m.chat.id, '⚠️ That is not a user ID.', back)
        return
    exp = sub_expiry(uid)
    if not exp:
        notify(m.chat.id, f'ℹ️ No subscription for <code>{uid}</code>.', back)
    elif exp > utcnow():
        notify(m.chat.id, f'✅ <b>Active</b> — <code>{uid}</code>\nExpires: {fmt_dt(exp)} '
                          f'({(exp - utcnow()).days} days left)', back)
    else:
        notify(m.chat.id, f'❌ <b>Expired</b> — <code>{uid}</code>\nEnded: {fmt_dt(exp)}', back)

def _parse_pkg(text):
    parts = (text or '').split()
    if len(parts) != 2 or parts[0].lower() not in ('pip', 'npm') or not _PKG_OK.match(parts[1]):
        return None, None
    return ('py' if parts[0].lower() == 'pip' else 'js'), parts[1].lower()

@on_input('pkg_add')
def in_pkg_add(m, st):
    if not is_admin(m.from_user.id):
        return
    lang, name = _parse_pkg(m.text)
    back = kb([btn('📦 Packages', 'pk')])
    if not lang:
        notify(m.chat.id, '⚠️ Use <code>pip NAME</code> or <code>npm NAME</code>.', back)
        return
    x('INSERT OR IGNORE INTO allowed_packages (lang, name) VALUES (?,?)', (lang, name))
    audit(m.from_user.id, 'package allowed', name)
    notify(m.chat.id, f'✅ <code>{esc(name)}</code> is now allowed.', back)

@on_input('pkg_del')
def in_pkg_del(m, st):
    if not is_admin(m.from_user.id):
        return
    lang, name = _parse_pkg(m.text)
    back = kb([btn('📦 Packages', 'pk')])
    if lang and x('DELETE FROM allowed_packages WHERE lang=? AND name=?', (lang, name)).rowcount:
        audit(m.from_user.id, 'package removed', name)
        notify(m.chat.id, f'✅ <code>{esc(name)}</code> was removed from the allowed list.', back)
    else:
        notify(m.chat.id, 'ℹ️ That package is not on the list.', back)

@on_input('user_lookup')
def in_user_lookup(m, st):
    if not is_admin(m.from_user.id):
        return
    uid = to_int(m.text)
    if not uid:
        notify(m.chat.id, '⚠️ That is not a user ID.', kb([btn('🔙 Back', 'adm')]))
        return
    scr_user(Ctx(m.chat.id), uid)

@on_input('adm_add')
def in_adm_add(m, st, role='admin'):
    if m.from_user.id != OWNER_ID:
        return
    uid = to_int(m.text)
    back = kb([btn('👑 Admins', 'adms')])
    label = 'an admin' if role == 'admin' else 'a reviewer'
    if not uid or uid <= 0:
        notify(m.chat.id, '⚠️ That is not a user ID.', back)
    elif uid == OWNER_ID or (uid in admin_ids and role == 'admin') or (uid in reviewer_ids and role == 'reviewer'):
        notify(m.chat.id, f'ℹ️ <code>{uid}</code> already has that role.', back)
    else:
        db_add_admin(uid, role)
        set_banned(uid, False)
        audit(m.from_user.id, f'made {label}', f'user {uid}')
        notify(m.chat.id, f'✅ <code>{uid}</code> is now {label}.', back)
        notify(uid, f'🎉 You are now {label} of this bot. Send /start.')

@on_input('rev_add')
def in_rev_add(m, st):
    in_adm_add(m, st, role='reviewer')

@on_input('adm_rem')
def in_adm_rem(m, st):
    if m.from_user.id != OWNER_ID:
        return
    uid = to_int(m.text)
    back = kb([btn('👑 Admins', 'adms')])
    if uid and db_remove_admin(uid):
        audit(m.from_user.id, 'removed from staff', f'user {uid}')
        notify(m.chat.id, f'✅ <code>{uid}</code> is no longer an admin or reviewer.', back)
        notify(uid, 'ℹ️ You are no longer an admin or reviewer of this bot.')
    else:
        notify(m.chat.id, '⚠️ Not an admin (the owner cannot be removed).', back)

# ════════════════════════════════════════════════════════════════════════════
#  FILE UPLOAD
# ════════════════════════════════════════════════════════════════════════════
@bot.message_handler(content_types=['document'], func=_private)
@guard
def on_document(m):
    u, doc = m.from_user, m.document
    uid = u.id
    st = _take_await(m)                    # a document can be the answer to "send the new file"
    if not gate(m):
        return
    if st and st['kind'] in ('replace_file', 'add_file'):
        handle_file_change(m, st)
        return
    ctx = Ctx(m.chat.id)
    ext = os.path.splitext(doc.file_name or '')[1].lower()
    if ext not in ('.py', '.js', '.zip'):
        ctx.draw('⚠️ Only <code>.py</code>, <code>.js</code> and <code>.zip</code> files are accepted.')
        return
    if (doc.file_size or 0) > MAX_FILE_SIZE_MB * 1024 * 1024:
        ctx.draw(f'⚠️ That file is too big — the maximum is {MAX_FILE_SIZE_MB} MB.')
        return
    name = safe_name(doc.file_name)
    if not _may_upload(ctx, uid, name):
        return
    ctx.draw(f'⏳ Receiving <b>{esc(name)}</b>…')
    data = _download(ctx, uid, doc)
    if data is None or not _scan_ok(ctx, u, name, data):
        return
    try:
        staged = stage_upload(uid, name, data)
    except UploadError as e:
        ctx.draw(f'❌ {esc(e)}')
        return
    _finish_upload(ctx, uid, name, staged, hashlib.sha256(data).hexdigest(), tg_file_id=doc.file_id)

def _may_upload(ctx, uid, name) -> bool:
    """File limit and upload cooldown."""
    replacing = q1('SELECT 1 FROM projects WHERE user_id=? AND name=?', (uid, name)) is not None
    queued = q1("SELECT 1 FROM approvals WHERE user_id=? AND name=? AND status='pending'", (uid, name))
    lim = file_limit(uid)
    if not replacing and not queued and project_count(uid) + pending_count(uid) >= lim:
        ctx.draw(f'⚠️ You have reached your limit of {int(lim)} files. Delete one first.',
                 kb([btn('📂 My Files', 'files:0'), btn('⭐ More files', 'prem')]))
        return False
    if not is_admin(uid) and not check_rate(_last_upload, uid, UPLOAD_COOLDOWN):
        ctx.draw(f'⏳ Please wait {UPLOAD_COOLDOWN}s between uploads.')
        return False
    return True

def _download(ctx, uid, doc):
    try:
        return bot.download_file(bot.get_file(doc.file_id).file_path)
    except Exception as e:
        logger.warning('Download failed for %s: %s', uid, e)
        ctx.draw('❌ I could not download that file from Telegram. Please try again.')
        return None

def _scan_ok(ctx, u, name, data) -> bool:
    safe, reason = scan_file(data, name, u.id)
    if not safe:
        ctx.draw(f'🚨 <b>Blocked by the security scan:</b> {esc(reason)}')
        notify(OWNER_ID, f'🚨 Blocked upload from <code>{u.id}</code>'
                         + (f' (@{esc(u.username)})' if u.username else '') +
                         f'\nFile: <code>{esc(name)}</code>\nReason: {esc(reason)}')
    return safe

def _finish_upload(ctx, uid, name, staged, sha, tg_file_id=None, document=None, kind='upload', source=''):
    """Common end of every upload: admins deploy at once, everyone else goes to review."""
    replacing = q1('SELECT 1 FROM projects WHERE user_id=? AND name=?', (uid, name)) is not None
    if is_admin(uid):                      # admins are their own reviewers
        pid = install_project(uid, name, staged['main_file'], staged['file_type'],
                              staged['stage_dir'], sha)
        if source:
            x('UPDATE projects SET source=? WHERE id=?', (source, pid))
        ctx.draw(f'✅ <b>{esc(name)}</b> saved.')
        threading.Thread(target=deploy, args=(pid, ctx), daemon=True).start()
        return
    report = analyze_tree(staged['stage_dir'])
    aid = submit_for_approval(uid, name, staged, sha, report, tg_file_id, kind=kind,
                              source=source, document=document)
    ctx.draw(f'📨 <b>{esc(name)}</b> was sent for review (request #{aid}).\n\n'
             + ('♻️ It will replace your current file once approved — the current one keeps '
                'running until then.\n' if replacing else '')
             + f"You'll get a message as soon as an admin has looked at it "
               f'(requests expire after {human_dur(APPROVE_TIMEOUT)}).',
             kb([btn('📂 My Files', 'files:0')]))

def handle_git(ctx, user, url):
    """Deploy from a public Git repository: clone → vet → review (same rules as a zip)."""
    uid = user.id
    if not GIT_ENABLED:
        ctx.draw('ℹ️ Deploying from Git is switched off on this bot.')
        return
    try:
        clean, repo = parse_git_url(url)
    except UploadError as e:
        ctx.draw(f'❌ {esc(e)}', kb([btn('🔙 Back', 'up')]))
        return
    name = safe_name(repo + '.git')
    if not _may_upload(ctx, uid, name):
        return
    ctx.draw(f'⏳ Cloning <code>{esc(clean)}</code>…')
    try:
        staged = stage_git(uid, clean)
        archive = zip_tree(staged['stage_dir'])
    except UploadError as e:
        ctx.draw(f'❌ {esc(e)}', kb([btn('🔙 Back', 'up')]))
        return
    if len(archive) > 45 * 1024 * 1024:
        shutil.rmtree(staged['stage_dir'], ignore_errors=True)
        ctx.draw('❌ The repository is too large to be sent to the reviewers.')
        return
    _finish_upload(ctx, uid, name, staged, hashlib.sha256(archive).hexdigest(),
                   document=archive, kind='git', source=clean)

def handle_file_change(m, st):
    """The user sent the new version of one file of a project (📁 Files → Replace / Add)."""
    u, doc = m.from_user, m.document
    uid, ctx = u.id, Ctx(m.chat.id)
    pid, rel = st['arg']
    p = get_project(pid)
    if not p or (p['user_id'] != uid and not is_admin(uid)):
        ctx.draw('⛔ Permission denied.')
        return
    back = kb([btn('📁 Files', f'fm:{pid}:0'), btn('⚙️ Open', f'f:{pid}')])
    if (doc.file_size or 0) > MAX_FILE_SIZE_MB * 1024 * 1024:
        ctx.draw(f'⚠️ That file is too big — the maximum is {MAX_FILE_SIZE_MB} MB.', back)
        return
    if rel is None:                        # a new file at the top level of the project
        rel = safe_name(doc.file_name, maxlen=60)
        if os.path.exists(os.path.join(project_dir(pid), rel)):
            ctx.draw(f'ℹ️ <code>{esc(rel)}</code> already exists — open it under 📁 Files and use Replace.', back)
            return
    if not is_admin(uid) and not check_rate(_last_upload, uid, UPLOAD_COOLDOWN):
        ctx.draw(f'⏳ Please wait {UPLOAD_COOLDOWN}s between uploads.', back)
        return
    ctx.draw(f'⏳ Receiving <code>{esc(rel)}</code>…')
    data = _download(ctx, uid, doc)
    if data is None or not _scan_ok(ctx, u, rel, data):
        return
    stage = os.path.join(PENDING_DIR, uuid.uuid4().hex[:16])
    os.makedirs(stage)
    staged_file = os.path.join(stage, os.path.basename(rel))
    with open(staged_file, 'wb') as f:
        f.write(data)
    if is_admin(uid):                      # admins are their own reviewers
        try:
            if p['user_id'] != uid:
                audit(uid, 'file change', f'file {pid}', f"{rel} in {p['name']} of {p['user_id']}")
            apply_file_change(pid, rel, staged_file, ctx)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        return
    staged = {'stage_dir': stage, 'main_file': p['main_file'], 'file_type': p['file_type']}
    aid = submit_for_approval(uid, p['name'], staged, hashlib.sha256(data).hexdigest(),
                              analyze_tree(stage), doc.file_id, kind='patch', target=rel, project_id=pid)
    ctx.draw(f'📨 Your change to <code>{esc(rel)}</code> in <b>{esc(p["name"])}</b> was sent for review '
             f'(request #{aid}). The current version keeps running until it is approved.', back)

def _parse_plan_payload(payload):
    parts = (payload or '').split(':')
    if len(parts) == 3 and parts[0] == 'plan' and parts[1] in PLANS and to_int(parts[2]):
        return parts[1], to_int(parts[2])
    return None, None

@bot.pre_checkout_query_handler(func=lambda pq: True)
def on_pre_checkout(pq):
    """Telegram asks "is this order still valid?" just before taking the Stars."""
    try:
        plan, days = _parse_plan_payload(pq.invoice_payload)
        ok = bool(STARS_ENABLED and plan and pq.currency == 'XTR'
                  and pq.total_amount == int(PLANS[plan].get('stars') or 0)
                  and days == int(PLANS[plan].get('days', 30)) and not is_banned(pq.from_user.id))
        bot.answer_pre_checkout_query(pq.id, ok, error_message=None if ok else
                                      'This offer is no longer available. Open ⭐ Plans again.')
    except Exception:
        logger.exception('pre-checkout failed')

@bot.message_handler(content_types=['successful_payment'])
@guard
def on_successful_payment(m):
    sp, uid = m.successful_payment, m.from_user.id
    plan, days = _parse_plan_payload(sp.invoice_payload)
    if x('INSERT OR IGNORE INTO payments (charge_id, user_id, plan, days, stars, ts) VALUES (?,?,?,?,?,?)',
         (sp.telegram_payment_charge_id, uid, plan, days, sp.total_amount, time.time())).rowcount != 1:
        return                                   # this payment was already processed
    if not plan:
        logger.error('Payment %s from %s has an unknown payload %r', sp.telegram_payment_charge_id,
                     uid, sp.invoice_payload)
        notify(OWNER_ID, f'⚠️ Payment of {sp.total_amount} ⭐ from <code>{uid}</code> could not be matched '
                         f'to a plan (charge <code>{esc(sp.telegram_payment_charge_id)}</code>).')
        return
    plan, expiry = grant_sub(uid, plan, days)
    audit(uid, 'payment', plan, f'{sp.total_amount} stars for {days} days')
    notify(m.chat.id, f'🎉 Thank you! {plan_name(plan)} is active until {fmt_dt(expiry)}.',
           kb([btn('⭐ Plans', 'prem'), btn('🏠 Menu', 'home')]))
    notify(OWNER_ID, f'💰 <code>{uid}</code> bought {plan_name(plan)} ({days} days) for {sp.total_amount} ⭐.')

@bot.message_handler(content_types=['text'], func=_private)
@guard
def on_text(m):
    """Typed answers to prompts; anything else gets a pointer to the menu."""
    st = _take_await(m)
    if is_banned(m.from_user.id):
        return
    handler = INPUTS.get(st['kind']) if st else None
    if handler:
        handler(m, st)
    elif gate(m):
        notify(m.chat.id, 'Send me a <code>.py</code>, <code>.js</code> or <code>.zip</code> file, '
                          'or open the menu.', kb([btn('🏠 Menu', 'home')]))

# ════════════════════════════════════════════════════════════════════════════
#  BUTTON ROUTER
#  Callback data is "<action>" or "<action>:<argument>" — always short, and
#  every action is looked up by its exact name. Callback data can be forged,
#  so permissions are checked here and again per file in proj_for().
# ════════════════════════════════════════════════════════════════════════════
ROUTES = {}
OPEN_WHEN_LOCKED = {'home', 'help', 'ping', 'stats', 'join', 'noop'}

def route(name, admin=False, owner=False, reviewer=False):
    """Register a button. Who may press it: everyone, reviewers and admins
    (reviewer=True), admins only (admin=True) or the owner only (owner=True)."""
    level = 'owner' if owner else 'admin' if admin else 'reviewer' if reviewer else 'all'

    def deco(fn):
        ROUTES[name] = (fn, level)
        return fn
    return deco

@bot.callback_query_handler(func=lambda c: True)
def on_callback(c):
    try:
        _dispatch(c)
    except Exception:
        logger.exception('Button %r from %s failed', c.data, c.from_user.id)
        ans(c, '⚠️ Something went wrong. Please try again.', True)
    finally:
        ans(c)

def _dispatch(c):
    uid = c.from_user.id
    action, _, arg = (c.data or '').partition(':')
    if c.message is None:
        ans(c, 'This menu is too old — send /start.', True)
        return
    if is_banned(uid):
        ans(c, '🚫 You are banned from using this bot.', True)
        return
    if not check_rate(_last_action, uid, ACTION_COOLDOWN):
        ans(c, '⏳ One moment…')
        return
    ctx = Ctx.of(c)
    if action != 'fll':
        LIVE.pop((ctx.chat_id, ctx.message_id), None)   # leaving a live log view stops it
    AWAIT.pop(uid, None)                                # pressing a button cancels a pending prompt

    if action == 'join':
        _join_cache.pop(uid, None)
        if is_joined_all(uid):
            register_user(c.from_user)
            ans(c, '✅ Verified!')
            scr_home(ctx, c.from_user)
        else:
            ans(c, '❌ You have not joined yet.', True)
        return
    if not is_joined_all(uid):
        scr_join(ctx)
        return
    register_user(c.from_user)
    if STATE['locked'] and not is_staff(uid) and action not in OPEN_WHEN_LOCKED:
        ans(c, '🔒 The bot is locked by the admin.', True)
        return
    entry = ROUTES.get(action)
    if not entry:
        ans(c, 'Unknown button — send /start.', True)
        return
    fn, level = entry
    allowed = (level == 'all' or (level == 'reviewer' and is_staff(uid))
               or (level == 'admin' and is_admin(uid)) or (level == 'owner' and uid == OWNER_ID))
    if not allowed:
        logger.warning('User %s pressed a restricted button: %r', uid, c.data)
        ans(c, '👑 Owner only.' if level == 'owner' else '⚠️ Admins only.', True)
        return
    fn(c, ctx, arg)

def proj_for(c, arg):
    """The project a button refers to — only if the presser may control it."""
    uid, pid = c.from_user.id, to_int(arg)
    p = get_project(pid) if pid is not None else None
    if not p:
        ans(c, 'This file no longer exists.', True)
        return None
    if p['user_id'] != uid and not is_admin(uid):
        logger.warning('User %s tried to use project %s of user %s', uid, pid, p['user_id'])
        ans(c, '⛔ Permission denied.', True)
        return None
    return p

# ─── navigation ──────────────────────────────────────────────────────────────
@route('noop')
def r_noop(c, ctx, arg):
    pass

@route('home')
def r_home(c, ctx, arg):
    scr_home(ctx, c.from_user)

@route('help')
def r_help(c, ctx, arg):
    scr_help(ctx)

@route('up')
def r_upload(c, ctx, arg):
    scr_upload(ctx, c.from_user.id)

@route('files')
def r_files(c, ctx, arg):
    scr_files(ctx, c.from_user.id, c.from_user.id, to_int(arg) or 0)

@route('stats')
def r_stats(c, ctx, arg):
    scr_stats(ctx, c.from_user.id)

@route('ping')
def r_ping(c, ctx, arg):
    t = time.time()
    try:
        bot.get_me()
        ans(c, f'🏓 Pong! {(time.time() - t) * 1000:.0f} ms\n🟢 {running_count()} scripts running', True)
    except Exception:
        ans(c, '⚠️ Telegram did not answer in time.', True)

@route('upg')
def r_upload_git(c, ctx, arg):
    if not GIT_ENABLED:
        ans(c, 'Deploying from Git is switched off.', True)
        return
    ask(ctx, c.from_user.id, 'git_url',
        '🔗 <b>Deploy from Git</b>\n\nSend the link of a <b>public</b> repository, e.g.\n'
        '<code>https://github.com/user/repository</code>\n\n'
        f"Allowed sites: {esc(', '.join(GIT_ALLOWED_HOSTS))}. It is reviewed like any other upload.",
        back='up')

# ─── file control ────────────────────────────────────────────────────────────
@route('f')
def r_file(c, ctx, arg):
    p = proj_for(c, arg)
    if p:
        scr_file(ctx, c.from_user.id, p)

def _audit_foreign(c, p, action):
    """Record an admin's action on a file that is not their own."""
    if p['user_id'] != c.from_user.id:
        audit(c.from_user.id, action, f"file {p['id']}", f"{p['name']} of {p['user_id']}")

def _start_and_report(c, ctx, p, restart=False):
    uid, pid = c.from_user.id, p['id']
    _audit_foreign(c, p, 'restarted' if restart else 'started')
    ans(c, '🔄 Restarting…' if restart else '⏳ Starting…')
    scr_file(ctx, uid, p, note='⏳ <i>Restarting…</i>' if restart else '⏳ <i>Starting…</i>')
    if restart:
        stop_project(pid, keep_flag=True)
    ok, msg = start_project(pid)
    p = get_project(pid)
    if not p:
        return
    if not ok:
        scr_file(ctx, uid, p, note=f'❌ {esc(msg)}')
        return
    time.sleep(START_CHECK_DELAY)
    p = get_project(pid)
    if not p:
        return
    if is_running(pid):
        scr_file(ctx, uid, p, note='✅ Restarted.' if restart else '✅ Started.')
    elif pid in INSTALLING:
        scr_file(ctx, uid, p, note="📦 A package is missing — installing it now. I'll message you when it's up.")
    else:
        scr_file(ctx, uid, p, note='⚠️ It stopped right after starting. Last output:', show_tail=True)

@route('fs')
def r_start(c, ctx, arg):
    p = proj_for(c, arg)
    if not p:
        return
    if is_running(p['id']):
        ans(c, 'Already running.')
        scr_file(ctx, c.from_user.id, p)
        return
    _start_and_report(c, ctx, p)

@route('fr')
def r_restart(c, ctx, arg):
    p = proj_for(c, arg)
    if p:
        _start_and_report(c, ctx, p, restart=True)

@route('fx')
def r_stop(c, ctx, arg):
    p = proj_for(c, arg)
    if not p:
        return
    ans(c, '🔴 Stopping…')
    _audit_foreign(c, p, 'stopped')
    stop_project(p['id'])
    scr_file(ctx, c.from_user.id, get_project(p['id']), note='🔴 Stopped.')

@route('fd')
def r_delete_ask(c, ctx, arg):
    p = proj_for(c, arg)
    if not p:
        return
    ctx.draw(f"🗑 <b>Delete {esc(p['name'])}?</b>\n\n"
             + ('It is running and will be stopped. ' if is_running(p['id']) else '')
             + 'The code, its packages and its logs are removed for good.',
             kb([btn('🗑 Yes, delete', f"fdy:{p['id']}"), btn('✖️ Cancel', f"f:{p['id']}")]))

@route('fdy')
def r_delete(c, ctx, arg):
    p = proj_for(c, arg)
    if not p:
        return
    _audit_foreign(c, p, 'deleted')
    delete_project(p['id'])
    logger.info('Project %s (%s) deleted by %s', p['id'], p['name'], c.from_user.id)
    ans(c, f"🗑 {p['name'][:60]} deleted")
    scr_files(ctx, c.from_user.id, p['user_id'])

@route('fl')
def r_logs(c, ctx, arg):
    p = proj_for(c, arg)
    if p:
        ctx.draw(*logs_view(p))

@route('fll')
def r_logs_live(c, ctx, arg):
    p = proj_for(c, arg)
    if not p:
        return
    token = object()
    LIVE[(ctx.chat_id, ctx.message_id)] = token
    ans(c, f'▶️ Live for {human_dur(LIVE_LOG_DURATION)}')
    ctx.draw(*logs_view(p, live=True))
    threading.Thread(target=_live_loop, args=(ctx, p['id'], token), daemon=True).start()

@route('fdl')
def r_log_download(c, ctx, arg):
    p = proj_for(c, arg)
    if not p:
        return
    files = [f for f in log_files(p['id']) if os.path.getsize(f) > 0]
    if not files:
        ans(c, 'There is no log yet.', True)
        return
    ans(c, '📥 Sending the log…')
    budget, parts = 45 * 1024 * 1024, []
    for path in reversed(files):                 # newest first, as much history as fits
        size = os.path.getsize(path)
        if size > budget:
            break
        with open(path, 'rb') as f:
            parts.append(f.read())
        budget -= size
    buf = io.BytesIO(b''.join(reversed(parts)))
    buf.name = f"{os.path.splitext(p['name'])[0]}.log.txt"
    bot.send_document(ctx.chat_id, buf, caption=f"📋 Log of <b>{esc(p['name'])}</b>"
                      + (f' ({len(parts)} files joined, oldest first)' if len(parts) > 1 else ''),
                      visible_file_name=buf.name)

@route('fg')
def r_source_download(c, ctx, arg):
    p = proj_for(c, arg)
    if not p:
        return
    pdir, files, total = project_dir(p['id']), [], 0
    for cur, dirs, names in os.walk(pdir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for n in names:
            full = os.path.join(cur, n)
            if os.path.isfile(full) and not os.path.islink(full):
                files.append(full)
                total += os.path.getsize(full)
    if not files:
        ans(c, 'The files are missing on disk.', True)
        return
    if total > 45 * 1024 * 1024:
        ans(c, 'This project is too large to send through Telegram.', True)
        return
    ans(c, '📥 Sending…')
    caption = f"📦 <b>{esc(p['name'])}</b>"
    if len(files) == 1:
        with open(files[0], 'rb') as f:
            bot.send_document(ctx.chat_id, f, caption=caption,
                              visible_file_name=os.path.basename(files[0]))
        return
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for full in files:
            zf.write(full, os.path.relpath(full, pdir))
    buf.seek(0)
    zname = os.path.splitext(p['name'])[0] + '.zip'
    buf.name = zname
    bot.send_document(ctx.chat_id, buf, caption=caption, visible_file_name=zname)

@route('fc')
def r_send_input(c, ctx, arg):
    p = proj_for(c, arg)
    if not p:
        return
    if not is_running(p['id']):
        ans(c, 'The script is not running.', True)
        return
    ask(ctx, c.from_user.id, 'stdin',
        f"⌨️ <b>Send input to {esc(p['name'])}</b>\n\nYour next message is written to the "
        "script's standard input (what <code>input()</code> reads).",
        arg=p['id'], back=f"f:{p['id']}")

# ─── file settings ───────────────────────────────────────────────────────────
def _split(arg):
    """'12:rest' → ('12', 'rest')"""
    head, _, rest = (arg or '').partition(':')
    return head, rest

@route('fset')
def r_settings(c, ctx, arg):
    p = proj_for(c, arg)
    if p:
        scr_settings(ctx, c.from_user.id, p)

@route('fe')
def r_env(c, ctx, arg):
    p = proj_for(c, arg)
    if p:
        scr_env(ctx, p)

@route('fea')
def r_env_add(c, ctx, arg):
    p = proj_for(c, arg)
    if p:
        ask(ctx, c.from_user.id, 'env_set',
            f"🔑 <b>Add or change a variable — {esc(p['name'])}</b>\n\nSend it as "
            '<code>NAME=value</code>, e.g. <code>BOT_TOKEN=123:abc</code>.\n'
            'Your message is deleted right after it is read.', arg=p['id'], back=f"fe:{p['id']}")

@route('fer')
def r_env_remove(c, ctx, arg):
    p = proj_for(c, arg)
    if p:
        ask(ctx, c.from_user.id, 'env_del',
            f"🔑 <b>Remove a variable — {esc(p['name'])}</b>\n\nSend its name.",
            arg=p['id'], back=f"fe:{p['id']}")

@route('fa')
def r_args(c, ctx, arg):
    p = proj_for(c, arg)
    if p:
        ask(ctx, c.from_user.id, 'args',
            f"▶️ <b>Arguments — {esc(p['name'])}</b>\n\nSend what should follow the file name when it "
            'is started, e.g. <code>--mode fast --port 8000</code>.\nSend <code>-</code> to clear them.',
            arg=p['id'], back=f"fset:{p['id']}")

@route('fmute')
def r_mute(c, ctx, arg):
    p = proj_for(c, arg)
    if not p:
        return
    x('UPDATE projects SET mute=? WHERE id=?', (0 if p['mute'] else 1, p['id']))
    ans(c, '🔔 Messages on' if p['mute'] else '🔕 Muted')
    scr_settings(ctx, c.from_user.id, get_project(p['id']))

@route('fver')
def r_version(c, ctx, arg):
    p = proj_for(c, arg)
    if not p:
        return
    if sandbox_backend() != 'docker':
        ans(c, 'Versions can only be chosen when scripts run in Docker.', True)
        return
    versions = PY_VERSIONS if p['file_type'] == 'py' else NODE_VERSIONS
    pid = p['id']
    rows = [[btn(('✅ ' if v == p['runtime'] else '') + v, f'fvs:{pid}:{i}') for i, v in enumerate(versions)],
            [btn(('✅ ' if not p['runtime'] else '') + 'Default', f'fvs:{pid}:d')],
            [btn('🔙 Back', f'fset:{pid}')]]
    ctx.draw(f"🧬 <b>Version — {esc(p['name'])}</b>\n\nWhich "
             f"{'Python' if p['file_type'] == 'py' else 'Node.js'} should run it? "
             'It applies at the next start; packages are installed again for the new version.', kb(*rows))

@route('fvs')
def r_version_set(c, ctx, arg):
    head, choice = _split(arg)
    p = proj_for(c, head)
    if not p:
        return
    versions = PY_VERSIONS if p['file_type'] == 'py' else NODE_VERSIONS
    idx = to_int(choice)
    runtime = '' if choice == 'd' else (versions[idx] if idx is not None and 0 <= idx < len(versions) else None)
    if runtime is None:
        ans(c, 'Bad request.', True)
        return
    x('UPDATE projects SET runtime=? WHERE id=?', (runtime, p['id']))
    shutil.rmtree(os.path.join(project_dir(p['id']), '.deps'), ignore_errors=True)   # built for the old version
    ans(c, f'🧬 {runtime or "Default"}')
    scr_settings(ctx, c.from_user.id, get_project(p['id']),
                 note='✅ Version saved. Restart the script to apply.')

@route('fsch')
def r_schedule(c, ctx, arg):
    p = proj_for(c, arg)
    if p:
        scr_schedule(ctx, p)

@route('fss')
def r_schedule_set(c, ctx, arg):
    head, code = _split(arg)
    p = proj_for(c, head)
    if not p:
        return
    if code == 'off':
        set_schedule(p['id'], '')
    elif code in ('r6', 'r12', 'r24'):
        set_schedule(p['id'], 'restart', int(code[1:]))
    else:
        ans(c, 'Bad request.', True)
        return
    ans(c, '⏰ Saved')
    scr_schedule(ctx, get_project(p['id']))

@route('fsd')
def r_schedule_daily(c, ctx, arg):
    p = proj_for(c, arg)
    if p:
        ask(ctx, c.from_user.id, 'sched_daily',
            f"⏰ <b>Start daily — {esc(p['name'])}</b>\n\nSend the time as <code>HH:MM</code> in UTC "
            f'(it is {utcnow():%H:%M} UTC now).', arg=p['id'], back=f"fsch:{p['id']}")

@route('fsr')
def r_log_search(c, ctx, arg):
    p = proj_for(c, arg)
    if p:
        ask(ctx, c.from_user.id, 'log_search',
            f"🔎 <b>Search the log — {esc(p['name'])}</b>\n\nSend a word or phrase "
            '(for example <code>Error</code>).', arg=p['id'], back=f"fl:{p['id']}")

@route('fok', admin=True)
def r_reapprove(c, ctx, arg):
    p = proj_for(c, arg)
    if not p:
        return
    set_manifest(p['id'])
    x('UPDATE projects SET last_reason=NULL WHERE id=?', (p['id'],))
    audit(c.from_user.id, 're-approved code', f"file {p['id']}", f"{p['name']} of {p['user_id']}")
    ans(c, '✅ Re-approved')
    scr_file(ctx, c.from_user.id, get_project(p['id']), note='✅ The code as it is now on disk is approved.')

# ─── file manager ────────────────────────────────────────────────────────────
def _file_for(c, arg):
    """(project, relative path) for a file-manager button, or (None, None)."""
    head, key = _split(arg)
    p = proj_for(c, head)
    if not p:
        return None, None
    rel = _file_by_key(p['id'], key)
    if not rel:
        ans(c, 'That file no longer exists.', True)
        return None, None
    return p, rel

@route('fm')
def r_file_manager(c, ctx, arg):
    head, page = _split(arg)
    p = proj_for(c, head)
    if p:
        scr_file_manager(ctx, p, to_int(page) or 0)

@route('fmf')
def r_one_file(c, ctx, arg):
    p, rel = _file_for(c, arg)
    if p:
        scr_one_file(ctx, p, rel)

@route('fmg')
def r_one_file_get(c, ctx, arg):
    p, rel = _file_for(c, arg)
    if not p:
        return
    full = os.path.join(project_dir(p['id']), *rel.split('/'))
    if os.path.getsize(full) > 45 * 1024 * 1024:
        ans(c, 'This file is too large to send through Telegram.', True)
        return
    if os.path.getsize(full) == 0:
        ans(c, 'This file is empty.', True)
        return
    ans(c, '📥 Sending…')
    with open(full, 'rb') as f:
        bot.send_document(ctx.chat_id, f, caption=f"📄 <code>{esc(rel)}</code> from <b>{esc(p['name'])}</b>",
                          visible_file_name=os.path.basename(rel))

@route('fmr')
def r_one_file_replace(c, ctx, arg):
    p, rel = _file_for(c, arg)
    if p:
        ask(ctx, c.from_user.id, 'replace_file',
            f"♻️ <b>Replace</b> <code>{esc(rel)}</code> in <b>{esc(p['name'])}</b>\n\n"
            'Send the new version. An admin reviews the change (they see exactly which lines differ); '
            'the current version keeps running until then.',
            arg=(p['id'], rel), back=f"fm:{p['id']}:0", want='file')

@route('fmn')
def r_file_add(c, ctx, arg):
    p = proj_for(c, arg)
    if p:
        ask(ctx, c.from_user.id, 'add_file',
            f"➕ <b>Add a file to {esc(p['name'])}</b>\n\nSend it; it is placed next to the main file "
            'under its own name. New files are reviewed before they are added.',
            arg=(p['id'], None), back=f"fm:{p['id']}:0", want='file')

@route('fmd')
def r_one_file_delete_ask(c, ctx, arg):
    p, rel = _file_for(c, arg)
    if not p:
        return
    if rel.lower().endswith(_CODE_EXT):
        ans(c, 'Code files cannot be deleted one by one — replace it or upload the project again.', True)
        return
    ctx.draw(f"🗑 Delete <code>{esc(rel)}</code> from <b>{esc(p['name'])}</b>?\n\nThis cannot be undone.",
             kb([btn('🗑 Yes, delete', f"fmdy:{p['id']}:{_fkey(rel)}"), btn('✖️ Cancel', f"fm:{p['id']}:0")]))

@route('fmdy')
def r_one_file_delete(c, ctx, arg):
    p, rel = _file_for(c, arg)
    if not p:
        return
    if rel.lower().endswith(_CODE_EXT):
        ans(c, 'Code files cannot be deleted one by one.', True)
        return
    try:
        os.remove(os.path.join(project_dir(p['id']), *rel.split('/')))
    except OSError as e:
        ans(c, f'Could not delete: {e}', True)
        return
    if p['user_id'] != c.from_user.id:
        audit(c.from_user.id, 'file deleted', f"file {p['id']}", f"{rel} in {p['name']} of {p['user_id']}")
    ans(c, '🗑 Deleted')
    scr_file_manager(ctx, p, note=f'🗑 <code>{esc(rel)}</code> deleted.')

# ─── plans, payments, codes, invites ─────────────────────────────────────────
@route('prem')
def r_plans(c, ctx, arg):
    scr_plans(ctx, c.from_user)

@route('trial')
def r_trial(c, ctx, arg):
    uid = c.from_user.id
    if not TRIAL_DAYS or plan_key(uid) != 'free':
        ans(c, 'The trial is not available for you.', True)
        return
    if x('UPDATE users SET trial_used=1 WHERE user_id=? AND trial_used=0', (uid,)).rowcount != 1:
        ans(c, 'You have already used your trial.', True)
        return
    plan, expiry = grant_sub(uid, first_plan(), TRIAL_DAYS)
    audit(uid, 'trial started', plan, f'{TRIAL_DAYS} days')
    ans(c, '🎁 Trial started!')
    scr_plans(ctx, c.from_user, note=f'🎁 Your free trial of {plan_name(plan)} runs until {fmt_dt(expiry)}.')

@route('redeem')
def r_redeem(c, ctx, arg):
    ask(ctx, c.from_user.id, 'redeem', '🎟 <b>Redeem a code</b>\n\nSend your code.', back='prem')

@route('ref')
def r_referral(c, ctx, arg):
    uid = c.from_user.id
    u = q1('SELECT ref_count FROM users WHERE user_id=?', (uid,))
    name = bot_username()
    link = f'https://t.me/{name}?start=ref{uid}' if name else '(link not available right now)'
    ctx.draw('👥 <b>Invite friends</b>\n\n'
             f'For every new user who joins with your link you get <b>{REFERRAL_BONUS_DAYS} days</b> of '
             f'{plan_name(first_plan())} (up to {REFERRAL_MAX} invites).\n\n'
             f'Your link:\n<code>{esc(link)}</code>\n\n'
             f"Invited so far: <b>{u['ref_count'] if u else 0}</b>", kb([btn('🔙 Back', 'prem')]))

@route('buy')
def r_buy(c, ctx, arg):
    pl = PLANS.get(arg)
    if not STARS_ENABLED or not pl or not pl.get('stars'):
        ans(c, 'This plan cannot be bought here.', True)
        return
    days = int(pl.get('days', 30))
    ans(c)
    bot.send_invoice(ctx.chat_id, title=f'{plan_name(arg)} — {days} days'[:32],
                     description=(f"{pl.get('files', SUBSCRIBED_USER_LIMIT)} hosted files, "
                                  f"{pl.get('ram', MAX_RAM_MB)} MB RAM and {pl.get('cpus', MAX_CPUS):g} CPU "
                                  f'per script for {days} days.')[:255],
                     invoice_payload=f'plan:{arg}:{days}', provider_token='', currency='XTR',
                     prices=[types.LabeledPrice(label=f'{days} days', amount=int(pl['stars']))])

# ─── admin: panel, dashboards ────────────────────────────────────────────────
@route('adm', reviewer=True)
def r_admin(c, ctx, arg):
    scr_admin(ctx, viewer=c.from_user.id)

@route('run', admin=True)
def r_running(c, ctx, arg):
    scr_running(ctx, to_int(arg) or 0)

@route('uf', admin=True)
def r_user_files(c, ctx, arg):
    target, _, page = arg.partition(':')
    if to_int(target) is None:
        ans(c, 'Bad request.', True)
        return
    scr_files(ctx, c.from_user.id, to_int(target), to_int(page) or 0)

@route('ul', admin=True)
def r_user_lookup(c, ctx, arg):
    ask(ctx, c.from_user.id, 'user_lookup', '👤 <b>Find user</b>\n\nSend the numeric user ID.', back='adm')

@route('u', admin=True)
def r_user(c, ctx, arg):
    if to_int(arg) is None:
        ans(c, 'Bad request.', True)
        return
    scr_user(ctx, to_int(arg))

@route('uban', admin=True)
def r_ban(c, ctx, arg):
    target = to_int(arg)
    if target is None or not ban_user(target):
        ans(c, 'Admins and the owner cannot be banned.', True)
        return
    audit(c.from_user.id, 'banned', f'user {target}')
    notify(target, '🚫 You have been banned from this bot. Your scripts were stopped.')
    ans(c, '🚫 Banned')
    scr_user(ctx, target)

@route('uunban', admin=True)
def r_unban(c, ctx, arg):
    target = to_int(arg)
    if target is None:
        return
    set_banned(target, False)
    audit(c.from_user.id, 'unbanned', f'user {target}')
    notify(target, '✅ Your ban was lifted. Send /start.')
    ans(c, '✅ Unbanned')
    scr_user(ctx, target)

@route('users', admin=True)
def r_users(c, ctx, arg):
    scr_users(ctx, to_int(arg) or 0)

@route('uexp', admin=True)
def r_users_export(c, ctx, arg):
    ans(c, '📤 Exporting…')
    buf = io.BytesIO(users_csv())
    buf.name = f'users_{utcnow():%Y%m%d}.csv'
    audit(c.from_user.id, 'users exported')
    bot.send_document(ctx.chat_id, buf, caption='👥 All users (CSV — opens in Excel or Google Sheets).',
                      visible_file_name=buf.name)

@route('audit', admin=True)
def r_audit(c, ctx, arg):
    scr_audit(ctx, to_int(arg) or 0)

@route('bkp', owner=True)
def r_backup(c, ctx, arg):
    ans(c, '💾 Creating a backup…')
    try:
        path = backup_database()
    except Exception as e:
        logger.exception('Backup failed')
        scr_admin(ctx, note=f'❌ Backup failed: {esc(e)}')
        return
    audit(c.from_user.id, 'backup')
    ok = send_backup(ctx.chat_id, path)
    scr_admin(ctx, note='💾 Backup created' + (' and sent to you.' if ok else f' on the server: <code>{esc(path)}</code>'))

@route('lock', admin=True)
def r_lock(c, ctx, arg):
    STATE['locked'] = not STATE['locked']
    set_setting('locked', '1' if STATE['locked'] else '0')
    logger.warning('Bot %s by admin %s', 'LOCKED' if STATE['locked'] else 'unlocked', c.from_user.id)
    audit(c.from_user.id, 'locked the bot' if STATE['locked'] else 'unlocked the bot')
    ans(c, '🔒 Locked' if STATE['locked'] else '🔓 Unlocked')
    scr_admin(ctx)

@route('runall', admin=True)
def r_run_all(c, ctx, arg):
    ans(c, '🟢 Starting everything…')
    audit(c.from_user.id, 'run all')

    def work():
        started = failed = 0
        for p in q('SELECT p.id FROM projects p LEFT JOIN users u ON u.user_id = p.user_id '
                   'WHERE COALESCE(u.banned, 0) = 0'):
            if is_running(p['id']):
                continue
            ok, _ = start_project(p['id'])
            started += ok
            failed += not ok
            time.sleep(0.2)
        scr_admin(ctx, note=f'✅ Started {started} script(s)' + (f', {failed} could not start.' if failed else '.'))
    threading.Thread(target=work, daemon=True).start()

@route('stopall', admin=True)
def r_stop_all_ask(c, ctx, arg):
    ctx.draw(f'🔴 <b>Stop all {running_count()} running scripts?</b>\n\n'
             'They stay stopped until someone starts them again.',
             kb([btn('🔴 Yes, stop all', 'stopally'), btn('✖️ Cancel', 'adm')]))

@route('stopally', admin=True)
def r_stop_all(c, ctx, arg):
    ans(c, '🔴 Stopping…')
    pids = list(RUNS) + list(PENDING_RESTART)
    for pid in pids:
        stop_project(pid)
    logger.warning('Admin %s stopped all scripts (%d).', c.from_user.id, len(pids))
    audit(c.from_user.id, 'stop all', detail=f'{len(pids)} scripts')
    scr_admin(ctx, note=f'🔴 Stopped {len(pids)} script(s).')

# ─── admin: approvals ────────────────────────────────────────────────────────
@route('ap', reviewer=True)
def r_approvals(c, ctx, arg):
    scr_approvals(ctx, to_int(arg) or 0)

@route('a', reviewer=True)
def r_approval(c, ctx, arg):
    a = get_approval(to_int(arg) or 0)
    if not a or a['status'] != 'pending':
        ans(c, f"Already {a['status']}." if a else 'Not found.', True)
        scr_approvals(ctx)
        return
    ctx.draw(approval_text(a), approval_buttons(a['id'], in_panel=True, full=is_admin(c.from_user.id),
                                                has_diff=os.path.isfile(_diff_path(a))))

def _decide_from_button(c, ctx, arg, approved, ban=False):
    ok, text = decide(to_int(arg) or 0, c.from_user, approved, ban=ban,
                      skip_card=(ctx.chat_id, ctx.message_id))
    if ok:
        ans(c, '✅ Approved' if approved else '❌ Rejected')
        ctx.draw(text, kb([btn('⏳ Approvals', 'ap:0')]) if ctx.is_text else None)
    else:
        ans(c, text, True)
        if ctx.is_text:
            scr_approvals(ctx)

@route('ay', reviewer=True)
def r_approve(c, ctx, arg):
    _decide_from_button(c, ctx, arg, True)

@route('an', reviewer=True)
def r_reject(c, ctx, arg):
    _decide_from_button(c, ctx, arg, False)

@route('ab', admin=True)
def r_reject_ban(c, ctx, arg):
    _decide_from_button(c, ctx, arg, False, ban=True)

@route('anr', reviewer=True)
def r_reject_reason(c, ctx, arg):
    a = get_approval(to_int(arg) or 0)
    if not a or a['status'] != 'pending':
        ans(c, f"Already {a['status']}." if a else 'Not found.', True)
        return
    # keep the review card intact: ask in a separate message when it is a document card
    ask(ctx if ctx.is_text else Ctx(ctx.chat_id), c.from_user.id, 'reject_reason',
        f"📝 <b>Reject #{a['id']}</b> (<code>{esc(a['name'])}</code>)\n\nSend the reason — "
        'the user will see it.', arg=a['id'], back=f"a:{a['id']}")

@route('ai', reviewer=True)
def r_approval_user(c, ctx, arg):
    a = get_approval(to_int(arg) or 0)
    if not a:
        ans(c, 'Not found.', True)
        return
    uid = a['user_id']
    u = q1('SELECT * FROM users WHERE user_id=?', (uid,))
    ans(c, f"👤 {(u['first_name'] if u else '') or 'User'} · {uid}\n"
           f"{status_str(uid)}\n"
           f"Files: {project_count(uid)} · Uploads: {u['total_uploads'] if u else 0} · "
           f"Runs: {u['total_runs'] if u else 0}\n"
           f"First seen: {fmt_dt(u['first_seen']) if u else '—'}", True)

@route('afile', reviewer=True)
def r_approval_file(c, ctx, arg):
    a = get_approval(to_int(arg) or 0)
    if not a or not a['tg_file_id']:
        ans(c, 'Not found.', True)
        return
    ans(c, '📎 Sending…')
    bot.send_document(ctx.chat_id, a['tg_file_id'], caption=f"📎 Upload #{a['id']} · <code>{esc(a['name'])}</code>")

@route('adiff', reviewer=True)
def r_approval_diff(c, ctx, arg):
    a = get_approval(to_int(arg) or 0)
    if not a:
        ans(c, 'Not found.', True)
        return
    try:
        sent = _send_diff(ctx.chat_id, a)
    except Exception as e:
        logger.warning('Could not send diff: %s', e)
        sent = False
    ans(c, '🔀 Sending…' if sent else 'There is nothing to compare with.', not sent)

# ─── packages ────────────────────────────────────────────────────────────────
def _decide_package_button(c, ctx, arg, allow):
    ok, text = decide_package(to_int(arg) or 0, c.from_user, allow)
    ans(c, None if ok else text, not ok)
    if ok:
        ctx.draw(text, kb([btn('📦 Packages', 'pk')]))

@route('pky', reviewer=True)
def r_package_allow(c, ctx, arg):
    _decide_package_button(c, ctx, arg, True)

@route('pkn', reviewer=True)
def r_package_deny(c, ctx, arg):
    _decide_package_button(c, ctx, arg, False)

@route('pk', reviewer=True)
def r_packages(c, ctx, arg):
    scr_packages(ctx, c.from_user.id)

@route('pkadd', admin=True)
def r_package_add(c, ctx, arg):
    ask(ctx, c.from_user.id, 'pkg_add',
        '📦 <b>Allow a package</b>\n\nSend <code>pip NAME</code> or <code>npm NAME</code>, '
        'e.g. <code>pip pillow</code>.', back='pk')

@route('pkdel', admin=True)
def r_package_del(c, ctx, arg):
    ask(ctx, c.from_user.id, 'pkg_del',
        '📦 <b>Remove a package from the allowed list</b>\n\nSend <code>pip NAME</code> or '
        '<code>npm NAME</code>.', back='pk')

# ─── admin: subscriptions, admins, broadcast ─────────────────────────────────
@route('subs', admin=True)
def r_subs(c, ctx, arg):
    scr_subs(ctx)

@route('sadd', admin=True)
def r_sub_add(c, ctx, arg):
    ask(ctx, c.from_user.id, 'sub_add',
        '💳 <b>Add / extend a subscription</b>\n\nSend <code>USER_ID DAYS</code>, '
        'e.g. <code>123456789 30</code>.', back='subs')

@route('srem', admin=True)
def r_sub_rem(c, ctx, arg):
    ask(ctx, c.from_user.id, 'sub_rem', '💳 <b>Remove a subscription</b>\n\nSend the user ID.', back='subs')

@route('schk', admin=True)
def r_sub_chk(c, ctx, arg):
    ask(ctx, c.from_user.id, 'sub_chk', '💳 <b>Check a subscription</b>\n\nSend the user ID.', back='subs')

@route('promo', admin=True)
def r_promo(c, ctx, arg):
    scr_promo(ctx)

@route('promoadd', admin=True)
def r_promo_add(c, ctx, arg):
    ask(ctx, c.from_user.id, 'promo_add',
        '🎟 <b>New promo code</b>\n\nSend <code>CODE DAYS [PLAN] [MAX_USES]</code>.\n'
        f"Plans: {esc(', '.join(PLANS))}. Leave MAX_USES out for unlimited.", back='promo')

@route('promodel', admin=True)
def r_promo_del(c, ctx, arg):
    ask(ctx, c.from_user.id, 'promo_del', '🎟 <b>Delete a promo code</b>\n\nSend the code.', back='promo')

@route('adms', admin=True)
def r_admins(c, ctx, arg):
    scr_admins(ctx, c.from_user.id)

@route('aadd', owner=True)
def r_admin_add(c, ctx, arg):
    ask(ctx, c.from_user.id, 'adm_add', '👑 <b>Add an admin</b>\n\nSend the user ID to promote.', back='adms')

@route('radd', owner=True)
def r_reviewer_add(c, ctx, arg):
    ask(ctx, c.from_user.id, 'rev_add',
        '🔎 <b>Add a reviewer</b>\n\nSend the user ID. Reviewers can only approve or reject uploads '
        'and package requests.', back='adms')

@route('arem', owner=True)
def r_admin_rem(c, ctx, arg):
    ask(ctx, c.from_user.id, 'adm_rem', '👑 <b>Remove an admin</b>\n\nSend the user ID to demote.', back='adms')

@route('bc', admin=True)
def r_broadcast(c, ctx, arg):
    scr_broadcast(ctx)

@route('bcnew', admin=True)
def r_broadcast_new(c, ctx, arg):
    ask(ctx, c.from_user.id, 'broadcast',
        '📢 <b>New broadcast</b>\n\nSend the message to deliver — text, photo, file, anything. '
        'You then choose who gets it and whether to send it now or later.', back='bc')

@route('bct', admin=True)
def r_broadcast_target(c, ctx, arg):
    job = BROADCASTS.get(c.from_user.id)
    if job and arg in _BC_TARGETS:
        job['target'] = arg
    scr_broadcast_confirm(ctx, c.from_user.id)

@route('bcs', admin=True)
def r_broadcast_schedule(c, ctx, arg):
    if c.from_user.id not in BROADCASTS:
        ans(c, 'Nothing to schedule — start the broadcast again.', True)
        return
    ask(ctx, c.from_user.id, 'bc_when',
        '🕐 <b>When should it go out?</b>\n\nSend a time as <code>HH:MM</code> (UTC, '
        f'now {utcnow():%H:%M}) or a delay like <code>+30</code> (minutes).', back='bc')

@route('bcx', admin=True)
def r_broadcast_unschedule(c, ctx, arg):
    done = x("UPDATE scheduled_broadcasts SET status='cancelled' WHERE id=? AND status='pending'",
             (to_int(arg) or 0,)).rowcount
    ans(c, 'Cancelled.' if done else 'Already sent or cancelled.')
    scr_broadcast(ctx)

@route('bcn', admin=True)
def r_broadcast_cancel(c, ctx, arg):
    BROADCASTS.pop(c.from_user.id, None)
    ans(c, 'Cancelled.')
    scr_broadcast(ctx, note='✖️ Broadcast cancelled.')

@route('bcy', admin=True)
def r_broadcast_send(c, ctx, arg):
    job = BROADCASTS.pop(c.from_user.id, None)
    if not job:
        ans(c, 'Nothing to send — start the broadcast again.', True)
        return
    ans(c, '📢 Broadcasting…')
    audit(c.from_user.id, 'broadcast', _BC_TARGETS[job['target']])
    threading.Thread(target=_execute_broadcast, args=(job['src'], ctx, job['target']), daemon=True).start()

def _execute_broadcast(src, ctx, target='all'):
    from_chat, msg_id = src
    users = broadcast_targets(target)
    sent = failed = blocked = 0
    ctx.draw(f'📢 Broadcasting to {len(users)} users…')
    for i, uid in enumerate(users, 1):
        for attempt in (1, 2):
            try:
                bot.copy_message(uid, from_chat, msg_id)
                sent += 1
                break
            except ApiTelegramException as e:
                code = getattr(e, 'error_code', 0)
                if code == 429 and attempt == 1:       # flood limit: wait as told, then retry once
                    wait = ((getattr(e, 'result_json', None) or {}).get('parameters') or {}).get('retry_after', 5)
                    time.sleep(wait + 1)
                    continue
                if code in (400, 403):                 # blocked the bot / account gone
                    blocked += 1
                    x('UPDATE users SET blocked=1 WHERE user_id=?', (uid,))
                else:
                    failed += 1
                break
            except Exception:
                failed += 1
                break
        time.sleep(0.05)                               # stay under ~20 messages / second
        if i % 100 == 0:
            ctx.draw(f'📢 Broadcasting… {i}/{len(users)}')
        if SHUTDOWN.is_set():
            break
    ctx.draw(f'📢 <b>Broadcast finished</b> ({_BC_TARGETS.get(target, target)})\n\n✅ Delivered: {sent}\n'
             f'🚫 Blocked / unreachable: {blocked}'
             + (' — left out of future broadcasts until they message the bot again' if blocked else '')
             + f'\n❌ Failed: {failed}', kb([btn('🛠 Admin Panel', 'adm')]))

# ════════════════════════════════════════════════════════════════════════════
#  SHUTDOWN
# ════════════════════════════════════════════════════════════════════════════
def _cleanup():
    """Stop every script. They stay flagged as "should run" and come back on the next start."""
    runs = list(RUNS.values())
    SHUTDOWN.set()
    if not runs:
        return
    logger.warning('Shutting down — stopping %d script(s)…', len(runs))
    threads = []
    for run in runs:
        run.stopping = True
        t = threading.Thread(target=_kill_run, args=(run,), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(timeout=12)
    logger.warning('Cleanup done.')

atexit.register(_cleanup)

def _on_signal(signum, frame):
    logger.warning('Signal %s received — shutting down.', signum)
    SHUTDOWN.set()
    # hard stop if a clean exit hangs for any reason
    threading.Thread(target=lambda: (time.sleep(25), os._exit(0)), daemon=True).start()
    raise SystemExit(0)

# ════════════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════════════
def main():
    logger.info('=' * 50)
    logger.info('🤖 HostBot starting…')
    logger.info('Owner: %s  |  Python: %s', OWNER_ID, sys.version.split()[0])
    logger.info('=' * 50)
    init_db()
    sandbox_backend()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            pass
    keep_alive()
    try:
        psutil.cpu_percent(interval=None)    # prime the CPU counter
    except Exception:
        pass
    threading.Thread(target=_maintenance_loop, daemon=True, name='maintenance').start()
    threading.Thread(target=resume_projects, daemon=True, name='resume').start()
    try:
        bot.set_my_commands([
            types.BotCommand('start', 'Open the menu'),
            types.BotCommand('files', 'Your hosted files'),
            types.BotCommand('git', 'Deploy from a Git link'),
            types.BotCommand('redeem', 'Redeem a promo code'),
            types.BotCommand('stats', 'Your stats'),
            types.BotCommand('ping', 'Check latency'),
            types.BotCommand('myid', 'Show your Telegram ID'),
            types.BotCommand('help', 'How it works'),
            types.BotCommand('cancel', 'Cancel the current input'),
        ])
    except Exception as e:
        logger.warning('Could not set the command menu: %s', e)
    if WEBHOOK_URL:
        secret = _webhook_secret()
        try:
            bot.remove_webhook()
            bot.set_webhook(url=f"{WEBHOOK_URL.rstrip('/')}/telegram/{secret}", secret_token=secret)
            logger.info('🚀 Webhook mode — Telegram delivers updates to %s/telegram/…', WEBHOOK_URL.rstrip('/'))
            while not SHUTDOWN.wait(1):
                pass
            return
        except Exception as e:
            logger.error('Could not set the webhook (%s) — falling back to polling.', e)
    try:
        bot.remove_webhook()                 # polling does not work while a webhook is set
    except Exception as e:
        logger.warning('Could not clear the webhook: %s', e)
    logger.info('🚀 Polling…')
    while not SHUTDOWN.is_set():
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=30,
                                 logger_level=logging.WARNING)
        except requests.exceptions.ReadTimeout:
            logger.warning('ReadTimeout — restarting poll in 5s')
            time.sleep(5)
        except requests.exceptions.ConnectionError as e:
            logger.error('ConnectionError: %s — retry in 15s', e)
            time.sleep(15)
        except Exception as e:
            logger.critical('Polling crash: %s', e, exc_info=True)
            time.sleep(30)
        else:
            if not SHUTDOWN.is_set():
                time.sleep(3)

if __name__ == '__main__':
    main()
