"""Data layer for EventFlow: PostgreSQL on Render, SQLite on your own computer. Chosen automatically.

    DATABASE_URL is set  (Render sets it from the Blueprint)  -> PostgreSQL
    DATABASE_URL not set (run.bat on a laptop)                  -> SQLite file instance/eventflow.db

The app's queries are written once, in SQL with "?" placeholders, and called through q(), ex() and
scalar(). On PostgreSQL each query is translated once (cached) and run through a small connection
pool, so the route code is identical for both databases.

On Render the server's disk is wiped on every restart, so uploaded photos, videos and PDFs are
stored in the database too (table media_files). Locally they go to instance/uploads as before.
"""
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from functools import lru_cache

from flask import g, current_app, has_app_context

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Single source of truth for the tables, written in SQLite dialect.
# pg_schema() below derives the PostgreSQL version from it.
SCHEMA = """
-- ============================================================== accounts
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  role TEXT NOT NULL CHECK (role IN ('dev','college','student')),
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  phone TEXT UNIQUE,
  password_hash TEXT NOT NULL,
  bio TEXT, avatar TEXT, cover TEXT,
  college_name TEXT, department TEXT, year TEXT, city TEXT, website TEXT,
  upi_id TEXT,
  verification TEXT NOT NULL DEFAULT 'none' CHECK (verification IN ('none','pending','verified','rejected')),
  verification_note TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended')),
  is_private INTEGER NOT NULL DEFAULT 0,
  show_events TEXT NOT NULL DEFAULT 'friends' CHECK (show_events IN ('everyone','friends','nobody')),
  allow_messages TEXT NOT NULL DEFAULT 'friends' CHECK (allow_messages IN ('everyone','friends','nobody')),
  theme TEXT NOT NULL DEFAULT 'system' CHECK (theme IN ('light','dark','system')),
  notify_posts INTEGER NOT NULL DEFAULT 1,
  notify_friends INTEGER NOT NULL DEFAULT 1,
  notify_events INTEGER NOT NULL DEFAULT 1,
  last_seen TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
-- exactly one developer account can ever exist
CREATE UNIQUE INDEX IF NOT EXISTS one_dev ON users(role) WHERE role = 'dev';

CREATE TABLE IF NOT EXISTS password_resets (
  token TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at TEXT NOT NULL
);

-- ============================================================== social graph
CREATE TABLE IF NOT EXISTS follows (
  follower_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  college_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  PRIMARY KEY (follower_id, college_id)
);
CREATE INDEX IF NOT EXISTS ix_follows_college ON follows(college_id);

CREATE TABLE IF NOT EXISTS friendships (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  requester_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  addressee_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted')),
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  UNIQUE (requester_id, addressee_id)
);
CREATE INDEX IF NOT EXISTS ix_friend_addressee ON friendships(addressee_id);

-- ============================================================== events
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  college_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT 'Technical',
  tagline TEXT, description TEXT,
  venue TEXT NOT NULL, venue_details TEXT, city TEXT, map_url TEXT,
  start_dt TEXT NOT NULL, end_dt TEXT NOT NULL, reg_deadline TEXT,
  capacity INTEGER NOT NULL DEFAULT 100,
  fee INTEGER NOT NULL DEFAULT 0,
  team_size INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('draft','open','closed','completed')),
  banner TEXT,
  food_cost_per_head REAL NOT NULL DEFAULT 0,
  meals_count INTEGER NOT NULL DEFAULT 0,
  food_buffer_pct REAL NOT NULL DEFAULT 10,
  is_featured INTEGER NOT NULL DEFAULT 0,
  is_removed INTEGER NOT NULL DEFAULT 0,
  views INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_events_college ON events(college_id);
CREATE INDEX IF NOT EXISTS ix_events_start ON events(start_dt);

-- event leads: a college gives a user access to specific events only
CREATE TABLE IF NOT EXISTS event_staff (
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role TEXT NOT NULL DEFAULT 'lead' CHECK (role IN ('lead','volunteer')),
  PRIMARY KEY (event_id, user_id)
);

CREATE TABLE IF NOT EXISTS schedule_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  title TEXT NOT NULL, start_dt TEXT NOT NULL, end_dt TEXT, venue TEXT, description TEXT
);

CREATE TABLE IF NOT EXISTS coupons (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  code TEXT NOT NULL COLLATE NOCASE,
  percent_off INTEGER NOT NULL CHECK (percent_off BETWEEN 1 AND 100),
  max_uses INTEGER NOT NULL DEFAULT 100,
  used INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  UNIQUE (event_id, code)
);

CREATE TABLE IF NOT EXISTS registrations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  pass_code TEXT NOT NULL UNIQUE,
  team_name TEXT,
  food_pref TEXT NOT NULL DEFAULT 'veg' CHECK (food_pref IN ('veg','nonveg','none')),
  status TEXT NOT NULL DEFAULT 'pending_payment'
    CHECK (status IN ('pending_payment','payment_review','confirmed','cancelled','waitlisted')),
  amount INTEGER NOT NULL DEFAULT 0,
  coupon_code TEXT,
  slot_start TEXT, slot_end TEXT, slot_venue TEXT,
  attended INTEGER NOT NULL DEFAULT 0,
  checkin_time TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  UNIQUE (event_id, user_id)
);
CREATE INDEX IF NOT EXISTS ix_reg_user ON registrations(user_id);

CREATE TABLE IF NOT EXISTS payments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  registration_id INTEGER NOT NULL REFERENCES registrations(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  receipt_no TEXT NOT NULL UNIQUE,
  base_amount INTEGER NOT NULL,
  discount INTEGER NOT NULL DEFAULT 0,
  amount INTEGER NOT NULL,
  platform_fee INTEGER NOT NULL DEFAULT 0,
  method TEXT NOT NULL CHECK (method IN ('upi','demo','free')),
  status TEXT NOT NULL DEFAULT 'created' CHECK (status IN ('created','submitted','paid','rejected','refunded')),
  utr TEXT, screenshot TEXT, note TEXT,
  reviewed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  reviewed_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_pay_event ON payments(event_id);

CREATE TABLE IF NOT EXISTS announcements (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
  title TEXT NOT NULL, body TEXT NOT NULL,
  priority TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('normal','important')),
  kind TEXT NOT NULL DEFAULT 'general' CHECK (kind IN ('general','slot')),
  created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS faqs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
  question TEXT NOT NULL, answer TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_saves (
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  PRIMARY KEY (event_id, user_id)
);

-- ============================================================== posts
CREATE TABLE IF NOT EXISTS posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  author_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  event_id INTEGER REFERENCES events(id) ON DELETE SET NULL,
  caption TEXT,
  is_removed INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_posts_author ON posts(author_id);

CREATE TABLE IF NOT EXISTS post_media (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  path TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('image','video','file')),
  original_name TEXT, size INTEGER NOT NULL DEFAULT 0,
  position INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS post_likes (
  post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  PRIMARY KEY (post_id, user_id)
);
CREATE TABLE IF NOT EXISTS post_saves (
  post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  PRIMARY KEY (post_id, user_id)
);
CREATE TABLE IF NOT EXISTS comments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ============================================================== messaging & alerts
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  recipient_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  body TEXT NOT NULL,
  event_id INTEGER REFERENCES events(id) ON DELETE SET NULL,
  read_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_msg_pair ON messages(sender_id, recipient_id);

CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  actor_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  text TEXT NOT NULL,
  link TEXT,
  is_read INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_notif_user ON notifications(user_id, is_read);

CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  reporter_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  target_type TEXT NOT NULL CHECK (target_type IN ('post','user','event','comment')),
  target_id INTEGER NOT NULL,
  reason TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved','dismissed')),
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS chat_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
  question TEXT NOT NULL, answer TEXT NOT NULL, source TEXT NOT NULL,
  answered INTEGER NOT NULL DEFAULT 1, resolved INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ============================================================== platform
CREATE TABLE IF NOT EXISTS event_winners (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  registration_id INTEGER REFERENCES registrations(id) ON DELETE CASCADE,
  position INTEGER NOT NULL DEFAULT 1 CHECK (position BETWEEN 0 AND 3),
  title TEXT NOT NULL,
  team_name TEXT,
  points INTEGER NOT NULL DEFAULT 0,
  created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  UNIQUE (event_id, user_id)
);
CREATE INDEX IF NOT EXISTS ix_win_user ON event_winners(user_id);

-- fests: a parent event (kind='fest') groups sub-events into tracks (Technical, Non-technical, Sports...)
CREATE TABLE IF NOT EXISTS fest_tracks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  fest_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  emoji TEXT NOT NULL DEFAULT '✨',
  blurb TEXT,
  pricing TEXT NOT NULL DEFAULT 'event' CHECK (pricing IN ('event','pass')),
  pass_fee INTEGER NOT NULL DEFAULT 0,
  position INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_tracks_fest ON fest_tracks(fest_id);

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  action TEXT NOT NULL, detail TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- times a student can't make it (used by the slot planner)
CREATE TABLE IF NOT EXISTS busy_times (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
  start_dt TEXT NOT NULL,
  end_dt TEXT NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_busy_user ON busy_times(user_id);

-- time slots an event runs in. Each slot holds a set number of members; people are allocated to one slot per event
CREATE TABLE IF NOT EXISTS event_slots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  label TEXT,
  start_dt TEXT NOT NULL,
  end_dt TEXT NOT NULL,
  venue TEXT,
  capacity INTEGER NOT NULL DEFAULT 20,
  position INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_slots_event ON event_slots(event_id);

-- the order of the event day for an event or a fest (entry always comes first): its events and its meals
CREATE TABLE IF NOT EXISTS flow_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scope_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('event','food')),
  event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
  title TEXT, start_dt TEXT, end_dt TEXT, venue TEXT,
  position INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_flow_scope ON flow_items(scope_id);

-- a person's finished or skipped event-day tasks that have no column of their own (meals, organiser skips)
CREATE TABLE IF NOT EXISTS task_marks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  scope_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  task_key TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'done' CHECK (state IN ('done','skipped')),
  actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  UNIQUE (user_id, scope_id, task_key)
);

-- the one live notice each participant keeps during an event day (updated as slots end, removed when it's over)
CREATE TABLE IF NOT EXISTS live_status (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  scope_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  notification_id INTEGER,
  stage TEXT NOT NULL,
  text TEXT NOT NULL,
  link TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  PRIMARY KEY (user_id, scope_id)
);

-- uploaded files, used when running on PostgreSQL (Render's disk is wiped on restart). Locally: instance/uploads
CREATE TABLE IF NOT EXISTS media_files (
  path TEXT PRIMARY KEY,
  data BLOB NOT NULL,
  mime TEXT NOT NULL,
  size INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
"""

MIGRATIONS = [
    "ALTER TABLE chat_logs ADD COLUMN event_id INTEGER REFERENCES events(id) ON DELETE CASCADE",
    "ALTER TABLE payments ADD COLUMN points_used INTEGER NOT NULL DEFAULT 0",
    # fests
    "ALTER TABLE events ADD COLUMN kind TEXT NOT NULL DEFAULT 'event'",
    "ALTER TABLE events ADD COLUMN parent_id INTEGER REFERENCES events(id) ON DELETE CASCADE",
    "ALTER TABLE events ADD COLUMN track_id INTEGER REFERENCES fest_tracks(id) ON DELETE SET NULL",
    "ALTER TABLE events ADD COLUMN fee_type TEXT NOT NULL DEFAULT 'person'",
    "ALTER TABLE events ADD COLUMN label TEXT",
    "ALTER TABLE events ADD COLUMN position INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE registrations ADD COLUMN covers TEXT",
    "ALTER TABLE payments ADD COLUMN bundle TEXT",
    "CREATE INDEX IF NOT EXISTS ix_events_parent ON events(parent_id)",
    # step-by-step event day: entry (attended/checkin_time) -> event completed -> food served
    "ALTER TABLE registrations ADD COLUMN completed_at TEXT",
    "ALTER TABLE registrations ADD COLUMN food_at TEXT",
    # certificates: each college's logo, signature and signatory
    "ALTER TABLE users ADD COLUMN cert_logo TEXT",
    "ALTER TABLE users ADD COLUMN cert_signature TEXT",
    "ALTER TABLE users ADD COLUMN cert_signatory TEXT",
    "ALTER TABLE users ADD COLUMN cert_signatory_title TEXT",
    # slots: organiser exceptions are locked (the planner keeps them); slot check-in time
    "ALTER TABLE registrations ADD COLUMN slot_locked INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE registrations ADD COLUMN slot_in_at TEXT",
    "CREATE INDEX IF NOT EXISTS ix_pay_bundle ON payments(bundle)",
    # time slots per event: the slot each registration is allocated to
    "ALTER TABLE registrations ADD COLUMN slot_id INTEGER REFERENCES event_slots(id) ON DELETE SET NULL",
    "CREATE INDEX IF NOT EXISTS ix_reg_slot ON registrations(slot_id)",
]


# ====================================================================== choosing the database
class ConfigError(RuntimeError):
    """The database can't be used; the message says exactly what to fix."""


_state = {"backend": None, "pool": None, "slots": None, "dsn": None, "pg_err": ()}
_lock = threading.Lock()


def database_url():
    url = (os.environ.get("DATABASE_URL") or "").strip()
    return url or None


def backend():
    return _state["backend"] or "sqlite"


def is_postgres():
    return backend() == "postgres"


def utc_offset():
    """The app's own UTC offset (from the TZ setting), e.g. "+05:30". The database uses the same offset for its
    timestamps, so Python's datetime.now() and the database's "now" always agree. Needs no time-zone database."""
    off = datetime.now().astimezone().utcoffset() or timedelta(0)
    mins = int(off.total_seconds() // 60)
    sign = "-" if mins < 0 else "+"
    return f"{sign}{abs(mins) // 60:02d}:{abs(mins) % 60:02d}"


def describe():
    """Short human label (startup banner, developer console)."""
    if not is_postgres():
        return "SQLite (local file)"
    host = re.sub(r"^.*@", "", _state["dsn"] or "").split("/")[0].split(":")[0]
    return "PostgreSQL" + (" · Render" if "render.com" in host or host.startswith("dpg-") else "")


def configure(app):
    """Pick the database. Called once from create_app()."""
    url = database_url()
    if url:
        try:
            import psycopg2  # noqa: F401
        except ImportError as e:
            raise ConfigError("psycopg2 is not installed. Run: pip install -r requirements.txt") from e
        _open_pool(url, int(os.environ.get("DB_POOL_SIZE") or 10))
        _state["backend"] = "postgres"
    else:
        _state["backend"] = "sqlite"
    app.config["DB_BACKEND"] = _state["backend"]
    app.config["DB_LABEL"] = describe()


# ====================================================================== PostgreSQL connection pool
WAIT_FOR_CONNECTION = 30   # seconds a request waits for a free connection when many arrive together
MAX_IDLE = 240             # seconds; idle connections older than this are replaced (sleep, network changes)
_last_used = {}


def _numeric(value, _cur):
    """NUMERIC -> int when whole, float otherwise (SQLite never returned Decimal)."""
    if value is None:
        return None
    d = Decimal(value)
    return int(d) if d == d.to_integral_value() else float(d)


def _open_pool(url, size):
    import psycopg2
    import psycopg2.extensions as ext
    from psycopg2 import pool
    ext.register_type(ext.new_type(ext.DECIMAL.values, "EF_NUMERIC", _numeric))
    params = dict(dsn=url, connect_timeout=15, application_name="EventFlow",
                  keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3)
    with _lock:
        if _state["pool"]:
            _state["pool"].closeall()
        last = None
        for attempt in range(5):           # the database may still be waking up right after a deploy
            try:
                p = pool.ThreadedConnectionPool(1, max(2, size), **params)
                break
            except psycopg2.OperationalError as e:
                last = e
                time.sleep(2 * (attempt + 1))
        else:
            msg = (str(last).strip().splitlines() or [repr(last)])[0]
            raise ConfigError(f"Could not connect to the PostgreSQL database in DATABASE_URL: {msg}") from last
        p.minconn = p.maxconn              # keep idle connections for reuse (opened lazily)
        _state.update(pool=p, slots=threading.BoundedSemaphore(max(2, size)), dsn=url,
                      pg_err=(psycopg2.OperationalError, psycopg2.InterfaceError))


def _pg_conn():
    pool = _state["pool"]
    if not _state["slots"].acquire(timeout=WAIT_FOR_CONNECTION):
        raise RuntimeError(f"No free database connection after {WAIT_FOR_CONNECTION}s. Raise DB_POOL_SIZE.")
    try:
        for _ in range(pool.maxconn + 1):
            conn = pool.getconn()
            idle = time.monotonic() - _last_used.get(id(conn), time.monotonic())
            if not conn.closed and idle < MAX_IDLE:
                break
            _last_used.pop(id(conn), None)
            pool.putconn(conn, close=True)
        conn.autocommit = True             # every statement commits on its own, exactly like the SQLite version
        return conn
    except Exception:
        _state["slots"].release()
        raise


def _purge_idle():
    pool = _state["pool"]
    with pool._lock:                       # psycopg2 keeps idle connections in pool._pool
        idle, pool._pool[:] = list(pool._pool), []
    for conn in idle:
        _last_used.pop(id(conn), None)
        try:
            conn.close()
        except Exception:
            pass


def _release(conn, broken=False):
    try:
        if broken or conn.closed:
            _last_used.pop(id(conn), None)
            _state["pool"].putconn(conn, close=True)
        else:
            _last_used[id(conn)] = time.monotonic()
            _state["pool"].putconn(conn)
    finally:
        _state["slots"].release()


def _pg_run(sql, args, mode):
    """Run one statement; on a dropped connection retry on a fresh one (reads always, writes when it never ran)."""
    for attempt in range(3):
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, args)
                if mode == "exec" or cur.description is None:
                    return []
                names = [d.name for d in cur.description]
                return [list(zip(names, r)) for r in cur.fetchall()]
        except _state["pg_err"]:
            if attempt == 2 or not conn.closed:
                raise
            g.pop("db", None)
            _release(conn, broken=True)
            _purge_idle()                  # one dead connection usually means the idle ones died too


# ====================================================================== rows
class Row:
    """Behaves like sqlite3.Row: row[0], row["name"], dict(row), row.keys(); Jinja can use row.name."""
    __slots__ = ("_v", "_i")

    def __init__(self, index, values):
        self._i = index
        self._v = values

    def __getitem__(self, key):
        if isinstance(key, (int, slice)):
            return self._v[key]
        try:
            return self._v[self._i[key]]
        except KeyError:
            raise IndexError(f"No item with that key: {key!r}") from None

    def keys(self):
        return list(self._i)

    def __iter__(self):
        return iter(self._v)

    def __len__(self):
        return len(self._v)

    def __eq__(self, other):
        return isinstance(other, Row) and self._v == other._v and self.keys() == other.keys()

    __hash__ = None

    def __repr__(self):
        return "<Row " + ", ".join(f"{k}={self[k]!r}" for k in self._i) + ">"


def _rows(pairs_list):
    rows, index = [], None
    for pairs in pairs_list:
        if index is None:
            index = {}
            for n, (name, _v) in enumerate(pairs):
                index.setdefault(name, n)  # first column wins on duplicate names, like sqlite3.Row
        rows.append(Row(index, [v for _k, v in pairs]))
    return rows


# ====================================================================== SQLite -> PostgreSQL translation
_STR = re.compile(r"'(?:[^']|'')*'")
_NOW = re.compile(r"datetime\(\s*'now'\s*,\s*'localtime'\s*\)", re.I)
_NOCASE_EQ = re.compile(r"((?:\b[\w.]+)|\?)\s*=\s*((?:[\w.]+)|\?)\s+COLLATE\s+NOCASE", re.I)
_DATE = re.compile(r"\bdate\(\s*([\w.]+)\s*\)", re.I)
_LIKE = re.compile(r"(?<![\w])LIKE\b", re.I)
_OR_IGNORE = re.compile(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO", re.I)
_INSERT_TABLE = re.compile(r"^\s*INSERT\s+INTO\s+(\w+)", re.I)
_SLOT = "\x00"


def _now_sql():
    return f"to_char(now() AT TIME ZONE INTERVAL '{utc_offset()}', 'YYYY-MM-DD HH24:MI:SS')"


@lru_cache(maxsize=2048)
def translate(sql):
    """SQLite dialect -> PostgreSQL with %s placeholders, plus the statement kind ("rows", "returning", "exec").
    Only SQL outside string literals is touched; the kind comes from the SQL itself, never from values."""
    s = _NOW.sub(_now_sql(), sql)
    ignore = bool(_OR_IGNORE.match(s))
    if ignore:
        s = _OR_IGNORE.sub("INSERT INTO", s, count=1)
    out, code, pos = [], [], 0
    for m in _STR.finditer(s):
        code.append(_code(s[pos:m.start()]))
        out += [code[-1], m.group(0).replace("%", "%%")]
        pos = m.end()
    code.append(_code(s[pos:]))
    out.append(code[-1])
    s = "".join(out).rstrip().rstrip(";")
    if ignore:
        s += " ON CONFLICT DO NOTHING"
    code_only = " ".join(code)
    first = (re.match(r"\s*\(*\s*(\w+)", code_only) or [None, ""])[1].lower()
    if first in ("select", "with", "values"):
        mode = "rows"
    elif re.search(r"\breturning\b", code_only, re.I):
        mode = "returning"
    else:
        mode = "exec"
    return s.replace(_SLOT, "%s"), mode, s.count(_SLOT)


def _code(part):
    part = part.replace("%", "%%")
    part = _NOCASE_EQ.sub(r"lower(\1)=lower(\2)", part)
    part = _DATE.sub(r"substr(\1,1,10)", part)
    part = _LIKE.sub("ILIKE", part)
    return part.replace("?", _SLOT)


def _params(args):
    return tuple(int(a) if isinstance(a, bool) else a for a in (args or ()))


# ====================================================================== public helpers
def connect(path):
    """Raw SQLite connection (local mode)."""
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def get_db():
    if "db" not in g:
        g.db = _pg_conn() if is_postgres() else connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is None:
        return
    if is_postgres():
        _release(conn)
    else:
        conn.close()


def _pg(sql, args):
    pg, mode, n = translate(sql)
    args = _params(args)
    if n != len(args):
        raise ValueError(f"Query expects {n} values but got {len(args)}: {' '.join(sql.split())[:200]}")
    return pg, args, mode


def q(sql, args=(), one=False):
    if is_postgres():
        pg, args, mode = _pg(sql, args)
        rows = _rows(_pg_run(pg, args, "rows" if mode != "exec" else "exec"))
    else:
        cur = get_db().execute(sql, args)
        rows = cur.fetchall()
        cur.close()
    if one:
        return rows[0] if rows else None
    return rows


ID_TABLES = {"users", "friendships", "events", "schedule_items", "coupons", "registrations", "payments", "announcements",
             "faqs", "posts", "post_media", "comments", "messages", "notifications", "reports", "chat_logs",
             "event_winners", "fest_tracks", "audit_log", "busy_times", "event_slots", "flow_items", "task_marks"}


def ex(sql, args=()):
    """Run a write. Returns the new row id for INSERTs (like sqlite's lastrowid)."""
    if has_app_context():
        g.pop("_ef_cache", None)           # drop per-request lookups (core.memo) so reads after a write are fresh
    if is_postgres():
        pg, args, mode = _pg(sql, args)
        m = _INSERT_TABLE.match(pg)
        if m and m.group(1).lower() in ID_TABLES and mode == "exec":
            rows = _pg_run(pg + " RETURNING id", args, "returning")
            return rows[0][0][1] if rows else None
        rows = _pg_run(pg, args, mode)
        return rows[0][0][1] if mode == "returning" and rows and rows[0] else None
    conn = get_db()
    cur = conn.execute(sql, args)
    conn.commit()
    return cur.lastrowid


def scalar(sql, args=()):
    row = q(sql, args, one=True)
    return row[0] if row else None


def in_clause(ids):
    ids = list(ids)
    return ("(" + ",".join("?" * len(ids)) + ")") if ids else "(NULL)", ids


def run_sql(sql):
    """Run finished PostgreSQL text (schema set-up). No placeholders."""
    with get_db().cursor() as cur:
        cur.execute(sql)


# ====================================================================== uploaded files stored in PostgreSQL
def file_put(path, data, mime):
    ex("INSERT INTO media_files (path, data, mime, size) VALUES (?,?,?,?)", (path, _binary(data), mime, len(data)))


def file_meta(path):
    return q("SELECT mime, size FROM media_files WHERE path=?", (path,), one=True)


def file_read(path, start=0, length=None):
    """Bytes of a stored file, or a slice of it (so videos can be streamed in pieces)."""
    if length is None:
        row = q("SELECT data FROM media_files WHERE path=?", (path,), one=True)
    else:
        row = q("SELECT substring(data FROM ? FOR ?) FROM media_files WHERE path=?", (start + 1, length, path), one=True)
    return bytes(row[0]) if row and row[0] is not None else b""


def file_delete(path):
    ex("DELETE FROM media_files WHERE path=?", (path,))


def _binary(data):
    import psycopg2
    return psycopg2.Binary(data)


# ====================================================================== schema
TABLES = [  # creation order (parents first)
    "users", "password_resets", "follows", "friendships", "events", "fest_tracks", "event_staff", "schedule_items",
    "coupons", "event_slots", "registrations", "payments", "announcements", "faqs", "event_saves", "posts", "post_media",
    "post_likes", "post_saves", "comments", "messages", "notifications", "reports", "chat_logs", "event_winners", "settings",
    "audit_log", "busy_times", "media_files", "flow_items", "task_marks", "live_status",
]


def pg_schema():
    """The PostgreSQL version of SCHEMA + MIGRATIONS, derived from the single SQLite definition above."""
    s = SCHEMA
    s = s.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    s = re.sub(r"TEXT((?: [A-Z]+)*?) COLLATE NOCASE", r"CITEXT\1", s)
    s = re.sub(r"\bREAL\b", "DOUBLE PRECISION", s)
    s = re.sub(r"\bBLOB\b", "BYTEA", s)
    s = _NOW.sub(_now_sql(), s)
    parts = ["-- EventFlow tables for PostgreSQL (generated from db.py; EventFlow creates them by itself)",
             "CREATE EXTENSION IF NOT EXISTS citext;", s.strip()]
    for m in MIGRATIONS:
        parts.append(re.sub(r"ADD COLUMN ", "ADD COLUMN IF NOT EXISTS ", m) + ";")
    return "\n".join(parts) + "\n"


SETUP_LOCK = 72700117     # any constant; makes sure only one server process creates tables / demo data


@contextmanager
def setup_lock():
    """Hold a database-wide lock while creating tables and demo data, so two processes starting at the same
    time (Render's zero-downtime deploys, several gunicorn workers) never seed twice."""
    if not is_postgres():
        yield
        return
    conn = _pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(%s)", (SETUP_LOCK,))
        try:
            yield
        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", (SETUP_LOCK,))
    finally:
        _release(conn)


def create_schema():
    if is_postgres():
        state = q("""SELECT to_regclass('public.users') IS NOT NULL AS has_tables,
                            to_regclass('public.media_files') IS NOT NULL AS has_media,
                            EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public'
                                    AND table_name='registrations' AND column_name='slot_id')
                            AND to_regclass('public.live_status') IS NOT NULL
                            AND to_regclass('public.task_marks') IS NOT NULL AS current""", one=True)
        if not (state["has_tables"] and state["current"] and state["has_media"]):   # first run, or older schema
            print("  Setting up EventFlow's tables on this database (first start or an upgrade).", flush=True)
            run_sql(pg_schema())
        return
    conn = get_db()
    conn.executescript(SCHEMA)
    for sql in MIGRATIONS:  # safe to re-run: ignored once the column exists
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
    conn.commit()


def drop_all():
    """Delete every EventFlow table (demo reset)."""
    if has_app_context():
        g.pop("_ef_cache", None)
    if is_postgres():
        run_sql("DROP TABLE IF EXISTS " + ", ".join(TABLES) + " CASCADE")
    else:
        conn = get_db()
        conn.execute("PRAGMA foreign_keys = OFF")
        for t in reversed(TABLES):
            conn.execute(f"DROP TABLE IF EXISTS {t}")
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON")


def init_app(app):
    configure(app)
    app.teardown_appcontext(close_db)
