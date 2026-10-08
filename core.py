"""Shared helpers: auth & permissions, settings, notifications, uploads, formatting, event maths."""
import io
import math
import mimetypes
import os
import re
import secrets
import uuid
from datetime import datetime
from functools import wraps

import qrcode
import qrcode.image.svg
from flask import g, redirect, url_for, request, abort, flash, current_app, jsonify, has_app_context
from markupsafe import Markup, escape
from werkzeug.utils import secure_filename

import db
from db import q, ex, scalar, in_clause

# ------------------------------------------------------------------ constants
CATEGORIES = {
    "Technical": "#4C6FFF",
    "Cultural": "#E0457B",
    "Workshop": "#10A37F",
    "Business": "#E8890C",
    "Sports": "#4F9D2A",
    "Gaming": "#8B5CF6",
    "Fest": "#D9418C",
    "Other": "#7A8399",
}
CATEGORY_EMOJI = {"Technical": "💻", "Cultural": "🎭", "Workshop": "🛠️", "Business": "💼",
                  "Sports": "🏆", "Gaming": "🎮", "Fest": "🎪", "Other": "✨"}
EVENT_STATUSES = ["draft", "open", "closed", "completed"]
ACTIVE_REG = ("confirmed", "payment_review")  # statuses that hold a seat
IMAGE_EXT = {"jpg", "jpeg", "png", "gif", "webp"}
VIDEO_EXT = {"mp4", "webm", "mov", "m4v"}
FILE_EXT = {"pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "zip", "txt", "csv"}
DEFAULT_SETTINGS = {
    "platform_fee_pct": "5",
    "signups_open": "1",
    "college_signups_open": "1",
    "maintenance": "0",
    "banner_text": "",
    "demo_payments": "1",
    "require_verified_to_publish": "0",
}


# ------------------------------------------------------------------ settings & audit
def memo(key, fn):
    """Cache a lookup for the rest of this request. db.ex() clears it on every write, so it never goes stale.
    Saves database round trips on pages that ask the same thing several times."""
    if not has_app_context():
        return fn()
    cache = g.setdefault("_ef_cache", {})
    if key not in cache:
        cache[key] = fn()
    return cache[key]


def setting(key):
    values = memo("settings", lambda: {r[0]: r[1] for r in q("SELECT key, value FROM settings")})
    v = values.get(key)
    return DEFAULT_SETTINGS.get(key, "") if v is None else v


def set_setting(key, value):
    ex("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def audit(action, detail=""):
    ex("INSERT INTO audit_log (actor_id, action, detail) VALUES (?,?,?)",
       (g.user["id"] if g.get("user") else None, action, detail[:500]))


def clean_url(url):
    """Allow only http(s) links; add https:// when the scheme is missing."""
    url = (url or "").strip()[:200]
    if not url:
        return ""
    if re.match(r"^https?://", url, re.I):
        return url
    if re.match(r"^[a-z][a-z0-9+.-]*:", url, re.I):
        return ""
    return "https://" + url


# ------------------------------------------------------------------ time
def now():
    return datetime.now()


def parse_dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace(" ", "T")[:16])
    except ValueError:
        return None


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M") if dt else None


def now_iso():
    return iso(now())


# ------------------------------------------------------------------ auth decorators
def wants_json():
    return request.path.startswith("/api/") or request.accept_mimetypes.best == "application/json"


def login_required(view):
    @wraps(view)
    def wrapper(*a, **kw):
        if not g.user:
            if wants_json():
                return jsonify(error="Please log in first.", login=url_for("auth.login")), 401
            flash("Log in to continue.", "info")
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        return view(*a, **kw)
    return wrapper


def role_required(*roles):
    def deco(view):
        @wraps(view)
        def wrapper(*a, **kw):
            if not g.user:
                return redirect(url_for("auth.login", next=request.path))
            if g.user["role"] not in roles:
                abort(403)
            return view(*a, **kw)
        return wrapper
    return deco


# ------------------------------------------------------------------ event permissions (hierarchy)
def managed_event_ids(user):
    """College: all its events. Staff (event lead): only events assigned to them. Dev: everything."""
    if not user:
        return []
    if user["role"] == "dev":
        return [r["id"] for r in q("SELECT id FROM events ORDER BY start_dt")]
    ids = []
    if user["role"] == "college":
        ids = [r["id"] for r in q("SELECT id FROM events WHERE college_id=? ORDER BY start_dt", (user["id"],))]
    staff = [r["event_id"] for r in q(
        "SELECT es.event_id FROM event_staff es JOIN events e ON e.id=es.event_id WHERE es.user_id=? ORDER BY e.start_dt",
        (user["id"],))]
    if staff:  # staff on a fest manage all of its events
        c, a = in_clause(staff)
        staff += [r[0] for r in q(f"SELECT id FROM events WHERE parent_id IN {c} ORDER BY position, id", a)]
    return ids + [i for i in dict.fromkeys(staff) if i not in ids]


def can_manage(user, event_id):
    if not user:
        return False
    if user["role"] == "dev":
        return True
    if scalar("SELECT 1 FROM events WHERE id=? AND college_id=?", (event_id, user["id"])):
        return True
    parent = scalar("SELECT parent_id FROM events WHERE id=?", (event_id,)) or 0
    return bool(scalar("SELECT 1 FROM event_staff WHERE event_id IN (?, ?) AND user_id=?", (event_id, parent, user["id"])))


def require_manage(event_id):
    if not can_manage(g.user, event_id):
        abort(403)


def is_studio_user(user):
    if not user:
        return False
    if user["role"] in ("college", "dev"):
        return True
    return bool(scalar("SELECT 1 FROM event_staff WHERE user_id=?", (user["id"],)))


# ------------------------------------------------------------------ social graph
def friend_ids(uid):
    return list(memo(("friend_ids", uid), lambda: _friend_ids(uid)))


def _friend_ids(uid):
    return [r[0] for r in q("""SELECT CASE WHEN requester_id=? THEN addressee_id ELSE requester_id END
                               FROM friendships WHERE status='accepted' AND (requester_id=? OR addressee_id=?)""",
                            (uid, uid, uid))]


def friend_status(me, other_id):
    if not me:
        return "anon"
    if me["id"] == other_id:
        return "self"
    mine = memo(("friendships", me["id"]), lambda: {
        (r["addressee_id"] if r["requester_id"] == me["id"] else r["requester_id"]): r
        for r in q("SELECT * FROM friendships WHERE requester_id=? OR addressee_id=?", (me["id"], me["id"]))})
    row = mine.get(other_id)
    if not row:
        return "none"
    if row["status"] == "accepted":
        return "friends"
    return "outgoing" if row["requester_id"] == me["id"] else "incoming"


def is_following(me, college_id):
    return bool(me and scalar("SELECT 1 FROM follows WHERE follower_id=? AND college_id=?", (me["id"], college_id)))


def can_see_events_of(viewer, owner):
    if viewer and viewer["id"] == owner["id"]:
        return True
    if owner["show_events"] == "everyone":
        return True
    if owner["show_events"] == "friends" and viewer and friend_status(viewer, owner["id"]) == "friends":
        return True
    return False


def can_message(me, other):
    if not me or not other or me["id"] == other["id"] or other["status"] != "active":
        return False
    if other["allow_messages"] == "everyone":
        return True
    if other["allow_messages"] == "friends":
        return friend_status(me, other["id"]) == "friends" or other["role"] == "college" and is_following(me, other["id"])
    return False


# ------------------------------------------------------------------ notifications
PREF_FOR = {"friend_request": "notify_friends", "friend_accept": "notify_friends", "message": "notify_friends",
            "post": "notify_posts", "like": "notify_posts", "comment": "notify_posts",
            "announcement": "notify_events", "slot": "notify_events", "event": "notify_events"}


def notify(user_id, kind, text, link=None, actor_id=None):
    if not user_id or (actor_id and actor_id == user_id):
        return
    pref = PREF_FOR.get(kind)
    if pref:
        row = q(f"SELECT {pref} FROM users WHERE id=?", (user_id,), one=True)
        if row is not None and not row[0]:
            return
    ex("INSERT INTO notifications (user_id, actor_id, kind, text, link) VALUES (?,?,?,?,?)",
       (user_id, actor_id, kind, text[:300], link))


def notify_many(user_ids, kind, text, link=None, actor_id=None):
    for uid in set(user_ids):
        notify(uid, kind, text, link, actor_id)


# ------------------------------------------------------------------ uploads
def upload_root():
    path = os.path.join(current_app.instance_path, "uploads")
    os.makedirs(path, exist_ok=True)
    return path


def media_kind(filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in FILE_EXT:
        return "file"
    return None


def save_upload(file, allowed=("image", "video", "file"), max_mb=40):
    """Store an uploaded file under instance/uploads/<yyyy-mm>/<uuid>.<ext>. Returns dict or raises ValueError."""
    if not file or not file.filename:
        raise ValueError("No file selected.")
    original = secure_filename(file.filename) or "file"
    kind = media_kind(original)
    if kind not in allowed:
        nice = {"image": "images", "video": "videos", "file": "documents"}
        raise ValueError(f"“{file.filename}” isn't supported here. Upload {', '.join(nice[a] for a in allowed)}.")
    file.stream.seek(0, os.SEEK_END)
    size = file.stream.tell()
    file.stream.seek(0)
    if size > max_mb * 1024 * 1024:
        raise ValueError(f"“{file.filename}” is larger than {max_mb} MB.")
    ext = original.rsplit(".", 1)[-1].lower()
    sub = datetime.now().strftime("%Y-%m")
    rel = f"{sub}/{uuid.uuid4().hex}.{ext}"
    if db.is_postgres():                    # Render: the disk is wiped on restart, so keep files in the database
        mime = mimetypes.guess_type(original)[0] or "application/octet-stream"
        db.file_put(rel, file.stream.read(), mime)
    else:
        os.makedirs(os.path.join(upload_root(), sub), exist_ok=True)
        file.save(os.path.join(upload_root(), rel))
    return {"path": rel, "kind": kind, "size": size, "original": file.filename[:120]}


def media_bytes(rel):
    """Bytes of any stored picture (demo media, an upload on disk, or an upload kept in PostgreSQL), or None."""
    if not rel:
        return None
    try:
        if rel.startswith("seed/"):
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed_media", rel[5:])
            with open(path, "rb") as f:
                return f.read()
        if db.is_postgres():
            data = db.file_read(rel)
            return data or None
        full = os.path.normpath(os.path.join(upload_root(), rel))
        if full.startswith(os.path.normpath(upload_root())) and os.path.isfile(full):
            with open(full, "rb") as f:
                return f.read()
    except OSError:
        return None
    return None


def delete_upload(rel):
    if not rel or rel.startswith("seed/"):
        return
    if db.is_postgres():
        db.file_delete(rel)
        return
    full = os.path.normpath(os.path.join(upload_root(), rel))
    if full.startswith(os.path.normpath(upload_root())) and os.path.isfile(full):
        try:
            os.remove(full)
        except OSError:
            pass


# ------------------------------------------------------------------ codes, QR, calendar
def gen_code(prefix, table, column, n=6):
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        code = prefix + "".join(secrets.choice(alphabet) for _ in range(n))
        if not scalar(f"SELECT 1 FROM {table} WHERE {column}=?", (code,)):
            return code


def qr_svg(data):
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    svg = buf.getvalue().decode()
    return Markup(svg[svg.find("<svg"):])


def upi_link(upi_id, payee, amount, note):
    from urllib.parse import quote
    return (f"upi://pay?pa={quote(upi_id)}&pn={quote(payee)}&am={amount:.2f}&cu=INR&tn={quote(note[:60])}")


def ics_for(event, college_name):
    def f(d):
        return parse_dt(d).strftime("%Y%m%dT%H%M00")
    desc = (event["tagline"] or "").replace("\n", " ")
    return "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//EventFlow//EN", "BEGIN:VEVENT",
        f"UID:eventflow-{event['id']}@eventflow", f"DTSTAMP:{datetime.now().strftime('%Y%m%dT%H%M%S')}",
        f"DTSTART:{f(event['start_dt'])}", f"DTEND:{f(event['end_dt'])}",
        f"SUMMARY:{event['title']} ({college_name})", f"LOCATION:{event['venue']}", f"DESCRIPTION:{desc}",
        "END:VEVENT", "END:VCALENDAR", ""])


# ------------------------------------------------------------------ events
def seats_taken(event_id):
    return scalar(f"SELECT COUNT(*) FROM registrations WHERE event_id=? AND status IN {ACTIVE_REG}", (event_id,))


def reg_state(event, taken=None):
    """Return (open: bool, reason: str)."""
    taken = seats_taken(event["id"]) if taken is None else taken
    if event["is_removed"]:
        return False, "This event was removed."
    if event["status"] == "completed":
        return False, "This event has finished."
    if event["status"] != "open":
        return False, "Registration isn't open."
    deadline = parse_dt(event["reg_deadline"])
    if deadline and deadline < now():
        return False, "Registration has closed."
    if taken >= event["capacity"]:
        return False, "All seats are taken."
    return True, ""


def platform_fee(amount):
    try:
        pct = float(setting("platform_fee_pct"))
    except ValueError:
        pct = 0
    return int(round(amount * pct / 100))


def event_stats(event_id):
    """Per event; for a fest, totals across all of its events."""
    scope = "(SELECT id FROM events WHERE id=? OR parent_id=?)"
    a = (event_id, event_id)
    row = q(f"""SELECT COUNT(*) c, COALESCE(SUM(attended),0) a,
                      COALESCE(SUM(CASE WHEN completed_at IS NOT NULL THEN 1 ELSE 0 END),0) done,
                      COALESCE(SUM(CASE WHEN slot_in_at IS NOT NULL THEN 1 ELSE 0 END),0) slot_in,
                      COUNT(DISTINCT CASE WHEN food_at IS NOT NULL THEN user_id END) fed,
                      COALESCE(SUM(CASE WHEN food_pref='veg' THEN 1 ELSE 0 END),0) v, COALESCE(SUM(CASE WHEN food_pref='nonveg' THEN 1 ELSE 0 END),0) nv,
                      COALESCE(SUM(CASE WHEN food_pref='none' THEN 1 ELSE 0 END),0) nf, COALESCE(SUM(CASE WHEN slot_start IS NOT NULL THEN 1 ELSE 0 END),0) slotted
               FROM registrations WHERE event_id IN {scope} AND status='confirmed'""", a, one=True)
    review = scalar(f"SELECT COUNT(*) FROM registrations WHERE event_id IN {scope} AND status='payment_review'", a)
    cancelled = scalar(f"SELECT COUNT(*) FROM registrations WHERE event_id IN {scope} AND status='cancelled'", a)
    revenue = scalar(f"SELECT COALESCE(SUM(amount),0) FROM payments WHERE event_id IN {scope} AND status='paid'", a)
    fees = scalar(f"SELECT COALESCE(SUM(platform_fee),0) FROM payments WHERE event_id IN {scope} AND status='paid'", a)
    dept = q(f"""SELECT COALESCE(NULLIF(u.department,''),'Other') label, COUNT(*) c FROM registrations r JOIN users u ON u.id=r.user_id
                WHERE r.event_id IN {scope} AND r.status='confirmed' GROUP BY label ORDER BY c DESC LIMIT 8""", a)
    colleges = q(f"""SELECT COALESCE(NULLIF(u.college_name,''),'Not set') label, COUNT(*) c FROM registrations r JOIN users u ON u.id=r.user_id
                    WHERE r.event_id IN {scope} AND r.status='confirmed' GROUP BY label ORDER BY c DESC LIMIT 8""", a)
    return {"registered": row["c"], "attended": row["a"], "completed": row["done"], "fed": row["fed"], "slot_in": row["slot_in"], "veg": row["v"], "nonveg": row["nv"], "nofood": row["nf"],
            "slotted": row["slotted"], "review": review, "cancelled": cancelled, "revenue": revenue, "fees": fees,
            "attendance_rate": (row["a"] / row["c"]) if row["c"] else 0,
            "dept": [dict(r) for r in dept], "colleges": [dict(r) for r in colleges]}


def historical_show_rate(college_id):
    row = q("""SELECT COALESCE(SUM(r.attended),0) a, COUNT(*) c FROM registrations r JOIN events e ON e.id=r.event_id
               WHERE e.status='completed' AND r.status='confirmed' AND e.college_id=?""", (college_id,), one=True)
    if row["c"] >= 5 and row["a"]:
        return row["a"] / row["c"], True
    return 0.85, False


def food_estimate(event, stats):
    reg = stats["registered"]
    rate, learned = historical_show_rate(event["college_id"])
    if event["status"] == "completed":
        heads, basis = stats["attended"], "actual attendance"
    else:
        heads = math.ceil(reg * rate)
        basis = f"{rate:.0%} predicted turnout" + (" (learned from your past events)" if learned else "")
    eat = ((reg - stats["nofood"]) / reg) if reg else 1
    diners = math.ceil(heads * eat)
    vs = (stats["veg"] / (stats["veg"] + stats["nonveg"])) if (stats["veg"] + stats["nonveg"]) else 1
    meals, cost, buf = event["meals_count"] or 0, event["food_cost_per_head"] or 0, event["food_buffer_pct"] or 0
    plates = diners * meals
    base = plates * cost
    return {"basis": basis, "rate": rate, "learned": learned, "diners": diners, "veg": round(diners * vs),
            "nonveg": diners - round(diners * vs), "meals": meals, "cost": cost, "plates": plates, "base": base,
            "buffer": base * buf / 100, "buffer_pct": buf, "total": base * (1 + buf / 100),
            "worst": reg * eat * meals * cost * (1 + buf / 100)}


def user_badges(user_id):
    att = scalar("SELECT COUNT(*) FROM registrations WHERE user_id=? AND attended=1", (user_id,)) or 0
    reg = scalar("SELECT COUNT(*) FROM registrations WHERE user_id=? AND status='confirmed'", (user_id,)) or 0
    cats = {r[0] for r in q("""SELECT e.category FROM registrations r JOIN events e ON e.id=r.event_id
                                WHERE r.user_id=? AND r.status='confirmed'""", (user_id,))}
    friends = len(friend_ids(user_id))
    out = []
    if reg >= 1:
        out.append(("🎟️", "First ticket", "Registered for a first event"))
    if att >= 1:
        out.append(("✅", "Showed up", "Checked in at an event"))
    if att >= 3:
        out.append(("🔥", "Regular", "Attended 3 or more events"))
    if "Technical" in cats:
        out.append(("💻", "Builder", "Joined a technical event"))
    if "Cultural" in cats:
        out.append(("🎭", "Performer", "Joined a cultural event"))
    if len(cats) >= 3:
        out.append(("🧭", "Explorer", "Tried 3 different categories"))
    if friends >= 5:
        out.append(("🤝", "Connector", "Has 5 or more friends"))
    wins = scalar("SELECT COUNT(*) FROM event_winners WHERE user_id=? AND position BETWEEN 1 AND 3", (user_id,)) or 0
    if wins:
        out.append(("🏆", "Podium", f"Placed in {wins} event{'s' if wins > 1 else ''}"))
    return out


# ------------------------------------------------------------------ points (gamification)
# Points are derived from facts (confirmed registrations, check-ins, wins, redemptions), so they
# can never drift: cancel a ticket and its points disappear; a rejected payment returns the points it used.
POINTS_PAID_REG = 50      # confirmed registration for a paid event
POINTS_FREE_REG = 20      # confirmed registration for a free event
POINTS_ATTEND = 30        # checked in at the event
POINTS_COMPLETE = 20      # completed the event (completion QR scanned)
WIN_POINTS = {1: 250, 2: 150, 3: 100, 0: 50}
WIN_TITLES = {1: "Winner", 2: "Runner-up", 3: "Second runner-up", 0: "Special mention"}
POINT_VALUE = 1           # ₹ per point
POINTS_MAX_PCT = 50       # points can pay for at most this % of a ticket
LEVELS = [(0, "Rookie", "🌱"), (100, "Explorer", "🧭"), (300, "Regular", "🔥"), (700, "Champion", "🏆"), (1500, "Legend", "👑")]


PAID_REG_SQL = "(r.amount > 0 OR COALESCE(r.covers, '') != '')"


def reg_points(reg):
    """Points a confirmed registration is worth: paid tickets (or the one carrying a pass/team fee) earn more."""
    if not reg:
        return 0
    return POINTS_PAID_REG if (reg["amount"] or 0) > 0 or reg["covers"] else POINTS_FREE_REG


def points_earned_sql(alias="u"):
    return f"""(COALESCE((SELECT SUM(CASE WHEN {PAID_REG_SQL} THEN {POINTS_PAID_REG} ELSE {POINTS_FREE_REG} END)
                          + SUM(CASE WHEN r.attended = 1 THEN {POINTS_ATTEND} ELSE 0 END)
                          + SUM(CASE WHEN r.completed_at IS NOT NULL THEN {POINTS_COMPLETE} ELSE 0 END)
                          FROM registrations r JOIN events e ON e.id = r.event_id
                          WHERE r.user_id = {alias}.id AND r.status = 'confirmed'), 0)
               + COALESCE((SELECT SUM(w.points) FROM event_winners w WHERE w.user_id = {alias}.id), 0))"""


def points_spent(user_id, exclude_payment=None):
    return scalar("""SELECT COALESCE(SUM(points_used), 0) FROM payments
                     WHERE user_id=? AND status IN ('created','submitted','paid') AND id != ?""",
                  (user_id, exclude_payment or 0)) or 0


def points_summary(user_id):
    return dict(memo(("points", user_id), lambda: _points_summary(user_id)))


def _points_summary(user_id):
    earned = scalar(f"SELECT {points_earned_sql()} FROM users u WHERE u.id=?", (user_id,)) or 0
    spent = points_spent(user_id)
    level = LEVELS[0]
    nxt = None
    for i, lv in enumerate(LEVELS):
        if earned >= lv[0]:
            level = lv
            nxt = LEVELS[i + 1] if i + 1 < len(LEVELS) else None
    progress = 100 if not nxt else int((earned - level[0]) * 100 / (nxt[0] - level[0]))
    return {"earned": earned, "spent": spent, "balance": max(0, earned - spent), "level": level[1], "level_emoji": level[2],
            "next": nxt, "to_next": (nxt[0] - earned) if nxt else 0, "progress": max(0, min(100, progress))}


def points_history(user_id, limit=60):
    rows = []
    for r in q("""SELECT r.created_at, r.attended, r.checkin_time, r.completed_at, r.amount, r.covers, e.title, e.fee, e.id event_id FROM registrations r
                  JOIN events e ON e.id=r.event_id WHERE r.user_id=? AND r.status='confirmed'""", (user_id,)):
        rows.append({"at": r["created_at"], "delta": reg_points(r),
                     "text": f"Registered for {r['title']}", "icon": "🎟️", "event_id": r["event_id"]})
        if r["attended"]:
            rows.append({"at": r["checkin_time"] or r["created_at"], "delta": POINTS_ATTEND,
                         "text": f"Checked in at {r['title']}", "icon": "✅", "event_id": r["event_id"]})
        if r["completed_at"]:
            rows.append({"at": r["completed_at"], "delta": POINTS_COMPLETE,
                         "text": f"Completed {r['title']}", "icon": "🎓", "event_id": r["event_id"]})
    for w in q("""SELECT w.created_at, w.points, w.title, e.title ev, e.id event_id FROM event_winners w JOIN events e ON e.id=w.event_id
                  WHERE w.user_id=?""", (user_id,)):
        rows.append({"at": w["created_at"], "delta": w["points"], "text": f"{w['title']} · {w['ev']}", "icon": "🏆",
                     "event_id": w["event_id"]})
    for p in q("""SELECT p.created_at, p.points_used, e.title, e.id event_id FROM payments p JOIN events e ON e.id=p.event_id
                  WHERE p.user_id=? AND p.points_used > 0 AND p.status IN ('created','submitted','paid')""", (user_id,)):
        rows.append({"at": p["created_at"], "delta": -p["points_used"], "text": f"Redeemed on {p['title']}", "icon": "🎁",
                     "event_id": p["event_id"]})
    rows.sort(key=lambda x: str(x["at"] or ""), reverse=True)
    return rows[:limit]


def max_points_for(amount_after_coupon, balance):
    """How many points can be used on a ticket of this price."""
    cap = int(amount_after_coupon * POINTS_MAX_PCT / 100) // POINT_VALUE
    return max(0, min(balance, cap))


def user_certificates(user_id):
    """Certificates a user has earned: participation (event completed) and achievement (winner)."""
    out = []
    for r in q("""SELECT r.pass_code, r.checkin_time, r.completed_at, r.attended, e.title, e.category, e.start_dt, e.id event_id,
                         c.name college, c.avatar college_avatar, w.title win_title, w.position
                  FROM registrations r JOIN events e ON e.id=r.event_id JOIN users c ON c.id=e.college_id
                  LEFT JOIN event_winners w ON w.event_id=r.event_id AND w.user_id=r.user_id
                  WHERE r.user_id=? AND r.status='confirmed' AND (r.completed_at IS NOT NULL OR w.id IS NOT NULL)
                  ORDER BY e.start_dt DESC""", (user_id,)):
        out.append(r)
    return out


def winner_groups(event_id):
    """Winners grouped per award (a team shows once with all its members)."""
    groups = {}
    for w in event_winners(event_id):
        key = (w["position"], w["title"], (w["team_name"] or "").lower() or f"u{w['user_id']}")
        gr = groups.setdefault(key, {"position": w["position"], "title": w["title"], "team": w["team_name"],
                                     "label": f"Team {w['team_name']}" if w["team_name"] else w["name"], "members": []})
        gr["members"].append(w)
    return list(groups.values())


def event_winners(event_id):
    return q("""SELECT w.*, u.name, u.username, u.avatar, u.college_name FROM event_winners w JOIN users u ON u.id=w.user_id
                WHERE w.event_id=? ORDER BY CASE w.position WHEN 0 THEN 9 ELSE w.position END, w.team_name, u.name""", (event_id,))


# ------------------------------------------------------------------ template filters
def register_filters(app):
    def _fmt(d, fmt):
        return d.strftime(fmt.replace("%I", str(int(d.strftime("%I")))))

    @app.template_filter("dt")
    def f_dt(v, fmt="%a, %d %b, %I:%M %p"):
        d = parse_dt(v)
        return _fmt(d, fmt) if d else "—"

    @app.template_filter("date")
    def f_date(v, fmt="%d %b %Y"):
        d = parse_dt(v)
        return d.strftime(fmt) if d else "—"

    @app.template_filter("time")
    def f_time(v):
        d = parse_dt(v)
        return _fmt(d, "%I:%M %p") if d else "—"

    @app.template_filter("day")
    def f_day(v):
        d = parse_dt(v)
        return d.strftime("%d") if d else ""

    @app.template_filter("mon")
    def f_mon(v):
        d = parse_dt(v)
        return d.strftime("%b") if d else ""

    @app.template_filter("rel")
    def f_rel(v):
        d = parse_dt(v)
        if not d:
            return ""
        secs = (d - datetime.now()).total_seconds()
        future, secs = secs > 0, abs(secs)
        if secs < 60:
            return "just now"
        if secs < 3600:
            n, u = int(secs // 60), "m"
        elif secs < 86400:
            n, u = int(secs // 3600), "h"
        elif secs < 86400 * 7:
            n, u = int(secs // 86400), "d"
        else:
            return d.strftime("%d %b")
        return f"in {n}{u}" if future else f"{n}{u}"

    @app.template_filter("until")
    def f_until(v):
        d = parse_dt(v)
        if not d:
            return ""
        days = (d.date() - datetime.now().date()).days
        if days == 0:
            return "Today"
        if days == 1:
            return "Tomorrow"
        if 1 < days < 7:
            return d.strftime("%A")
        if days < 0:
            return d.strftime("%d %b")
        return f"In {days} days"

    @app.template_filter("inr")
    def f_inr(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return "₹0"
        s = f"{v:,.0f}"
        return "₹" + s

    @app.template_filter("pct")
    def f_pct(v):
        return f"{(v or 0) * 100:.0f}%"

    @app.template_filter("compact")
    def f_compact(n):
        n = n or 0
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
        if n >= 1000:
            return f"{n / 1000:.1f}K".replace(".0K", "K")
        return str(n)

    @app.template_filter("initials")
    def f_initials(name):
        parts = [p for p in re.split(r"\s+", name or "?") if p and p[0].isalnum()]
        return "".join(p[0] for p in parts[:2]).upper() or "?"

    @app.template_filter("cat_color")
    def f_cat(c):
        return CATEGORIES.get(c, CATEGORIES["Other"])

    @app.template_filter("cat_emoji")
    def f_emoji(c):
        return CATEGORY_EMOJI.get(c, "✨")

    @app.template_filter("hue")
    def f_hue(s):
        """Stable hue per name, used for generated avatar gradients."""
        return sum(ord(c) * (i + 1) for i, c in enumerate(str(s or "x"))) % 360

    @app.template_filter("filesize")
    def f_size(n):
        n = n or 0
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.0f} {unit}"
            n /= 1024
        return f"{n:.1f} TB"

    @app.template_filter("rich")
    def f_rich(text):
        """Escape, then link #hashtags and @mentions, keep **bold** and line breaks."""
        s = str(escape(text or ""))
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<![\w&])#(\w{2,40})", lambda m: f'<a class="tag" href="/explore?q=%23{m.group(1)}">#{m.group(1)}</a>', s)
        s = re.sub(r"(?<![\w.])@(\w{3,30})", lambda m: f'<a class="tag" href="/u/{m.group(1)}">@{m.group(1)}</a>', s)
        return Markup(s.replace("\n", "<br>"))

    @app.template_filter("qr")
    def f_qr(data):
        return qr_svg(data)
