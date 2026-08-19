# HostBot — Telegram Script Hosting Bot

Host and run Python/JS scripts directly from Telegram.  
Built for reliability, security, and admin control.

---

## Quick Start

```bash
pip install -r requirements.txt
python bot.py
```

Set your config at the top of `bot.py`:

```python
TOKEN    = 'your_bot_token'
OWNER_ID = your_telegram_id
```

That's it. No `.env` file needed — token lives in code as requested.

---

## What's New vs Old Version

### Security
| Old | New |
|-----|-----|
| Token + owner ID hardcoded — same | Same (as requested) |
| Keyword scanner (flagged "keylogger" in legit code) | **Entropy-based scanner** — detects packed/encrypted payloads by randomness score |
| Binary signature check only | Signature + entropy + extension combined |
| No rate limiting | **Upload cooldown** (15s) + **command cooldown** (5s) |
| Duplicate `is_user_joined_all` caused IndentationError | Fixed — single clean definition |
| `TELEGRAM_MODULES` used before defined | Fixed — dict defined before functions |

### Approval System (NEW)
- Every file uploaded by a **non-admin** user goes to **pending approval**
- All admins get a notification with **Approve / Reject** buttons
- **View User Info** button on approval shows upload count, run count, last active, sub status
- Approved → file saved + script starts automatically
- Rejected → file deleted from disk, user notified
- Pending approvals survive bot restart (stored in SQLite)
- `/list_approvals` inline button shows all pending items

### Watchdog Auto-Restart (NEW)
- Every script gets a **watchdog thread**
- If a script crashes, watchdog detects it within ~12s and auto-restarts
- User gets a Telegram notification when auto-restart fires
- Watchdog stops if you manually stop the script

### Log Streaming (NEW)
- **📡 Stream** button on any running script
- Sends live log chunks to your chat every 5 seconds
- Auto-stops after 12 messages or when script dies
- `/stopstream` to stop manually
- Multiple users can stream different scripts simultaneously

### Per-User Stats (NEW)
- Total uploads tracked per user
- Total runs tracked per user
- Last active timestamp
- All visible to admins via user info in approval panel

### No File Echo (NEW)
- Old bot forwarded uploaded files back — removed
- Only metadata sent to owner (user ID, filename, size)

### Broadcast Upgrade (NEW)
- Uses `copy_message` instead of manual text/photo/video handling
- Supports **all media types** automatically (stickers, voice, documents, etc.)
- No more media type checking needed

### Other Fixes
| Bug | Fix |
|-----|-----|
| `bot_locked` modified across threads with no lock | `threading.Lock()` added |
| SQLite opened per-function with no pooling | `_DB_LOCK` + consistent connection pattern |
| Log files grew unbounded | `RotatingFileHandler` (5MB × 3 backups) |
| No global `/cancel` support | `/cancel` clears step handler in any conversation |
| `parse_mode='Markdown'` broke on special chars | Switched to `parse_mode='HTML'` everywhere |
| No `/myid` command | Added |
| No `/ping` command | Added (shows latency) |

---

## Commands

| Command | Who | Description |
|---------|-----|-------------|
| `/start` | All | Welcome screen + menu |
| `/myid` | All | Shows your Telegram ID |
| `/ping` | All | Bot latency check |
| `/files` | All | List your uploaded files |
| `/stats` | All | Your usage statistics |
| `/stopstream` | All | Stop active log stream |
| `/cancel` | All | Cancel any pending input |

---

## File Limits

| Role | File Limit |
|------|-----------|
| Free user | 10 files |
| Premium (subscribed) | 20 files |
| Admin | 999 files |
| Owner | Unlimited |

---

## Approval Flow

```
User uploads file
       ↓
Security scan (entropy + signature + extension)
       ↓ pass
Admin user? ──YES──→ Auto-approve → Start script
       ↓ NO
Submit to pending_approvals table
       ↓
All admins notified with Approve/Reject buttons
       ↓
Admin approves → script starts + user notified
Admin rejects  → file deleted + user notified
```

---

## Security Scanner

Blocks files if:
- Extension is in dangerous list (`.exe`, `.dll`, `.bat`, `.apk`, `.dmg`, etc.)
- File starts with binary signature (`MZ`, `ELF`, `Mach-O`)
- Entropy of first 8KB > 7.5 (packed/encrypted payload indicator)

Owner bypasses all security checks.

---

## Watchdog

Each running script gets a watchdog thread that:
1. Waits 24 seconds (2× interval) after start for grace period
2. Checks every 12 seconds if process is alive
3. On crash: sends notification + restarts automatically
4. On manual stop: watchdog exits cleanly

---

## Database Tables

| Table | Purpose |
|-------|---------|
| `subscriptions` | Premium user expiry dates |
| `user_files` | File registry per user |
| `active_users` | All users who ever used the bot |
| `admins` | Admin user IDs |
| `pending_approvals` | Files waiting for admin review |
| `user_stats` | Upload/run counts per user |

---

## Config Reference

```python
# ── Limits ────────────────────────────────────────
FREE_USER_LIMIT       = 10
SUBSCRIBED_USER_LIMIT = 20
ADMIN_LIMIT           = 999

# ── Cooldowns (seconds) ───────────────────────────
UPLOAD_COOLDOWN   = 15
CMD_COOLDOWN      = 5

# ── Watchdog ──────────────────────────────────────
WATCHDOG_INTERVAL = 12     # seconds between checks
STREAM_INTERVAL   = 5      # seconds between stream messages
STREAM_MAX_MSGS   = 12     # max stream messages before auto-stop

# ── File limits ───────────────────────────────────
MAX_FILE_SIZE_MB  = 25
MAX_LOG_TAIL_KB   = 150
```

---

## Directory Structure

```
hostbot/
├── bot.py              ← main file, run this
├── requirements.txt
├── README.md
├── data/
│   ├── bot_data.db     ← SQLite database
│   └── bot_main.log    ← rotating log (5MB × 3)
└── upload_bots/
    └── {user_id}/      ← per-user script folders
        ├── script.py
        └── script.log  ← script output log
```

---

## Hosting on Replit / Railway / Render

The Flask keep-alive server runs on port `8080` (or `$PORT` env var).  
Set up an uptime monitor (UptimeRobot) to ping the URL every 5 minutes.

No additional config needed — just run `python bot.py`.
