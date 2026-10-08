"""The live notice: one notification per participant per event day that stays put and updates itself.

From two hours before an event day starts until it's over, each participant has a single notification (and a strip at
the top of the app) saying where things stand: "Slot 2 (11:00–12:00) is over. Next for you: Paper Presentation ·
Slot 3 · 12:00 PM · Lab 2". Each time a slot ends, or one of their tasks is scanned, the same notification is rewritten
and marked unread again (it doesn't pile up new ones). When the event day is completed, or has ended, it's removed.

There's no background worker: the notice is brought up to date whenever the participant's app checks in (every page
and the live counter it polls), which is exactly when they can see it.
"""
from datetime import timedelta

from flask import url_for

import core
import journey
from db import q, ex, scalar

LEAD = timedelta(hours=2)          # show the notice this long before the day starts
TAIL = timedelta(hours=1)          # and keep it this long after the last slot / the end


def _scope_rows(user_id, now):
    """The event days this person is confirmed for that are around now (cheap filter; compute() decides exactly)."""
    return q("""SELECT DISTINCT COALESCE(e.parent_id, e.id) scope FROM registrations r JOIN events e ON e.id=r.event_id
                LEFT JOIN events f ON f.id=e.parent_id
                WHERE r.user_id=? AND r.status='confirmed' AND e.is_removed=0 AND COALESCE(f.status, e.status)!='completed'
                AND COALESCE(f.end_dt, e.end_dt) >= ? AND COALESCE(f.start_dt, e.start_dt) <= ?""",
             (user_id, core.iso(now - timedelta(days=2)), core.iso(now + timedelta(days=1))))


def _window(scope):
    """(starts, ends, title, status, first pass code) of an event day, from its events and their slots."""
    ev = q("SELECT * FROM events WHERE id=?", (scope,), one=True)
    if not ev or ev["is_removed"]:
        return None
    span = q("""SELECT MIN(s.start_dt) a, MAX(s.end_dt) b FROM event_slots s JOIN events e ON e.id=s.event_id
                WHERE (e.id=? OR e.parent_id=?) AND e.is_removed=0""", (scope, scope), one=True)
    start = core.parse_dt(ev["start_dt"])
    end = core.parse_dt(ev["end_dt"])
    if span and span["a"]:
        start = min(start, core.parse_dt(span["a"])) if start else core.parse_dt(span["a"])
        end = max(end, core.parse_dt(span["b"])) if end else core.parse_dt(span["b"])
    return {"event": ev, "start": start, "end": end}


def _windows_over(scope, now):
    """Slot windows of the event day that have ended, latest last: [(start, end, label)]."""
    rows = q("""SELECT s.start_dt, s.end_dt, s.label, s.id, s.event_id FROM event_slots s JOIN events e ON e.id=s.event_id
                WHERE (e.id=? OR e.parent_id=?) AND e.is_removed=0 ORDER BY s.start_dt, s.end_dt""", (scope, scope))
    seen, out = {}, []
    ordinal = {}
    for r in rows:
        ordinal.setdefault(r["event_id"], []).append(r)
    for r in rows:
        k = (r["start_dt"][:16], r["end_dt"][:16])
        if k in seen:
            continue
        evslots = ordinal[r["event_id"]]
        n = next(i + 1 for i, x in enumerate(evslots) if x["id"] == r["id"])
        label = r["label"] or (f"Slot {n}" if len(evslots) > 1 else None)
        seen[k] = label
        e = core.parse_dt(r["end_dt"])
        if e and e <= now:
            out.append((core.parse_dt(r["start_dt"]), e, label))
    return out


def _fmt(d):
    return d.strftime("%I:%M %p").lstrip("0") if d else ""


def compute(user_id, scope, now=None):
    """(stage, text, link) for this person at this event day right now, or None when there's nothing to show."""
    now = now or core.now()
    w = _window(scope)
    if not w or w["event"]["status"] == "completed" or not w["start"] or not w["end"]:
        return None
    if now < w["start"] - LEAD or now > w["end"] + TAIL:
        return None
    regs = journey.scope_regs(user_id, scope)
    if not regs:
        return None
    st = journey.steps(user_id, scope, regs)
    nxt = journey.current(st)
    title = w["event"]["title"]
    over = _windows_over(scope, now)
    if now < w["start"]:
        head = f"{title} starts at {_fmt(w['start'])}."
        stage = "pre"
    elif over:
        s, e, label = over[-1]
        name = label or "The slot"
        head = f"{name} ({_fmt(s)}–{_fmt(e)}) is over."
        stage = f"over:{e.isoformat()}"
    else:
        head = f"{title} is live now."
        stage = "live"
    if nxt:
        tail = f" Next for you: {nxt['title']}" + (f" · {nxt['sub']}" if nxt.get("sub") else "") + "."
        stage += f"|{nxt['key']}|{nxt.get('extra', '')}"
    else:
        tail = " You've finished every task. 🎉"
        stage += "|done"
    link = url_for("events.ticket", code=(nxt["reg"]["pass_code"] if nxt else regs[0]["pass_code"]))
    return stage, f"🔴 Live · {head}{tail}", link


def refresh_user(user_id, now=None):
    """Bring this person's live notices up to date. Returns the current ones [{scope, text, link, stage}]."""
    now = now or core.now()
    wanted = {}
    for row in _scope_rows(user_id, now):
        res = compute(user_id, row["scope"], now)
        if res:
            wanted[row["scope"]] = res
    have = {r["scope_id"]: r for r in q("SELECT * FROM live_status WHERE user_id=?", (user_id,))}
    for scope, r in have.items():                    # finished / no longer relevant: remove
        if scope not in wanted:
            _drop(r)
    out = []
    for scope, (stage, text, link) in wanted.items():
        r = have.get(scope)
        nid = r["notification_id"] if r else None
        if nid and not scalar("SELECT 1 FROM notifications WHERE id=?", (nid,)):
            nid = None
        if not r or r["stage"] != stage or not nid:
            if nid:
                ex("UPDATE notifications SET text=?, link=?, is_read=0, created_at=? WHERE id=?",
                   (text[:300], link, now.strftime("%Y-%m-%d %H:%M:%S"), nid))
            else:
                nid = ex("INSERT INTO notifications (user_id, kind, text, link, created_at) VALUES (?,?,?,?,?)",
                         (user_id, "live", text[:300], link, now.strftime("%Y-%m-%d %H:%M:%S")))
            ex("""INSERT INTO live_status (user_id, scope_id, notification_id, stage, text, link, updated_at) VALUES (?,?,?,?,?,?,?)
                  ON CONFLICT(user_id, scope_id) DO UPDATE SET notification_id=excluded.notification_id, stage=excluded.stage,
                  text=excluded.text, link=excluded.link, updated_at=excluded.updated_at""",
               (user_id, scope, nid, stage, text, link, now.strftime("%Y-%m-%d %H:%M:%S")))
        out.append({"scope": scope, "text": text, "link": link, "stage": stage})
    return out


def _drop(row):
    if row["notification_id"]:
        ex("DELETE FROM notifications WHERE id=? AND kind='live'", (row["notification_id"],))
    ex("DELETE FROM live_status WHERE user_id=? AND scope_id=?", (row["user_id"], row["scope_id"]))


def finish_scope(scope):
    """An event (or fest) was marked completed: its events inside follow, and the live notices go."""
    ex("UPDATE events SET status='completed' WHERE parent_id=? AND status IN ('open','closed')", (scope,))
    clear_scope(scope)


def clear_scope(scope):
    """The event day was completed: remove everyone's live notice for it."""
    for row in q("SELECT * FROM live_status WHERE scope_id=?", (scope,)):
        _drop(row)


def current_for(user_id):
    return [{"scope": r["scope_id"], "text": r["text"], "link": r["link"], "stage": r["stage"]}
            for r in q("SELECT * FROM live_status WHERE user_id=? ORDER BY updated_at DESC", (user_id,))]
