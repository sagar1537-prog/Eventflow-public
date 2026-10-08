"""The event day as a task list ("task-based guidance").

Every confirmed participant gets one ordered list for the whole event day (a fest is one day, one ticket):

    1. Entry           scan the ENTRY QR at the gate                    -> marks attendance
    2. Their events    in the order the organisers set (drag and drop), each at the time slot allocated to them.
                       Scan the EVENT QR at that event's desk, in their own slot -> unlocks that event's certificate
    3. Meals           when the event serves food. A meal with a time sits where its time falls among their events;
                       a meal without a time keeps its place in the order. Scan the FOOD QR at the counter.

A task unlocks only when the one before it is done (or skipped by the organisers). Each task has its own QR. A QR
carries the task kind, the ticket code and a short signature, and the event QR also carries the slot, so:
  * a QR can't be faked from a ticket code,
  * an event QR only works at that event's desk, and only while the desk runs that person's slot. A team in slot 1
    can't be accepted in slot 2 or the other way round,
  * when the organisers move someone to another slot, the old QR stops working and the ticket shows the new one.
"""
import hashlib
import hmac
import re

from flask import current_app, url_for

import core
import scheduler
from db import q, ex, scalar

KIND_NAMES = {"IN": "entry", "EV": "event", "FOOD": "food"}
DESKS = {"IN": "entry gate", "EV": "event desk", "FOOD": "food counter"}
ICONS = {"IN": "🚪", "EV": "🎯", "FOOD": "🍽️"}
_PAYLOAD = re.compile(r"^EF:(IN|EV|FOOD|SLOT|DONE):([A-Z0-9-]{4,40}):(?:([A-F0-9]{6}):)?([A-F0-9]{6,12})$")
BAD_QR = "This QR code isn't valid. Ask the participant to open their ticket in EventFlow."


# ------------------------------------------------------------------ QR payloads
def _sig(kind, code, extra=""):
    key = str(current_app.config["SECRET_KEY"]).encode()
    return hmac.new(key, f"{kind}:{code}:{extra}".encode(), hashlib.sha256).hexdigest()[:8].upper()


def payload(kind, code, extra=""):
    """What the QR for one task contains, e.g. EF:IN:EVF-ABC123:1F2E3D4C, or for an event (with its slot)
    EF:EV:EVF-ABC123:9A1B2C:1F2E3D4C."""
    return f"EF:{kind}:{code}:{extra + ':' if extra else ''}{_sig(kind, code, extra)}"


def parse(text):
    """-> (kind or None, ticket code, extra) from a scanned QR or a typed code. Raises ValueError for a forged or
    outdated QR."""
    text = (text or "").strip().upper()
    if text.startswith("HTTP"):
        text = text.rstrip("/").rsplit("/", 1)[-1]
    m = _PAYLOAD.match(text)
    if m:
        kind, code, extra, sig = m.groups()
        extra = extra or ""
        if kind in ("SLOT", "DONE"):
            raise ValueError("That QR is from an older ticket. Ask them to reopen their ticket in EventFlow for the new one.")
        if (kind in ("EV", "FOOD")) != bool(extra) or not hmac.compare_digest(sig, _sig(kind, code, extra)):
            raise ValueError(BAD_QR)
        return kind, code, extra
    if text.startswith("EF:"):
        raise ValueError(BAD_QR)
    return None, text, ""


# ------------------------------------------------------------------ the task list
def scope_id(reg):
    """A fest is one event day (one entry, one ticket); a standalone event is its own day."""
    return reg["parent_id"] or reg["event_id"]


def scope_regs(user_id, scope):
    return q("""SELECT r.*, e.title, e.start_dt, e.end_dt, e.venue, e.meals_count, e.parent_id, e.label, e.category
                FROM registrations r JOIN events e ON e.id=r.event_id
                WHERE r.user_id=? AND r.status='confirmed' AND (e.id=? OR e.parent_id=?) AND e.is_removed=0
                ORDER BY e.start_dt, r.id""", (user_id, scope, scope))


def _fmt(v, f="%I:%M %p"):
    d = core.parse_dt(v)
    return d.strftime(f).lstrip("0") if d else ""


def steps(user_id, scope, regs=None):
    """Ordered tasks for one participant at one event day. Each: key, kind, title, sub, hint, reg, done_at, state,
    when (sort time), qr (only for the current task)."""
    regs = regs if regs is not None else scope_regs(user_id, scope)
    if not regs:
        return []
    ev = q("SELECT id, title, venue, kind FROM events WHERE id=?", (scope,), one=True)
    events = scheduler.scope_events(scope)
    items = scheduler.flow(scope, events)
    marks = {m["task_key"]: m for m in q("SELECT * FROM task_marks WHERE user_id=? AND scope_id=?", (user_id, scope))}
    mine = {r["event_id"]: r for r in regs}
    slot_rows = {s["id"]: s for s in q(f"""SELECT * FROM event_slots WHERE event_id IN ({",".join("?" * len(mine))})""",
                                         list(mine))} if mine else {}
    by_event = {}
    for s in slot_rows.values():
        by_event.setdefault(s["event_id"], []).append(s)
    for lst in by_event.values():
        lst.sort(key=lambda s: (s["start_dt"], s["position"], s["id"]))

    entered = any(r["attended"] for r in regs)
    entry_at = min((r["checkin_time"] for r in regs if r["attended"] and r["checkin_time"]), default=None)
    first = regs[0]
    out = [{"key": "entry", "kind": "IN", "title": "Check in at the entrance", "icon": "🚪",
            "sub": ev["venue"] or "", "hint": f"Show this QR at the {ev['title']} entry desk" + (f" ({ev['venue']})" if ev["venue"] else ""),
            "reg": first, "done_at": entry_at if entered else None, "done": entered, "skipped": False, "extra": "",
            "when": None}]
    wants_food = any(r["food_pref"] != "none" for r in regs)
    pref = next((r["food_pref"] for r in regs if r["food_pref"] != "none"), "veg")
    legacy_fed = next((r["food_at"] for r in regs if r["food_at"]), None)
    food_marked = any(k.startswith("food") for k in marks)
    seq, first_food = [], True
    for it in items:
        if it["kind"] == "event" and it["event_id"] in mine:
            r = mine[it["event_id"]]
            slot = slot_rows.get(r["slot_id"]) if r["slot_id"] else None
            key = f"ev-{r['id']}"
            mark = marks.get(key)
            if slot:
                sub = scheduler.slot_text(slot, by_event.get(r["event_id"]), r["venue"])
                hint = f"{sub} · show this QR at the {r['title']} desk in your slot"
            else:
                sub = "Slot not allocated yet"
                hint = "Your time slot isn't allocated yet. You'll get a notification the moment it is."
            seq.append({"key": key, "kind": "EV", "title": r["title"], "icon": "🎯", "sub": sub, "hint": hint, "reg": r,
                        "slot": slot, "extra": scheduler.slot_key(slot) if slot else "", "cert": True,
                        "done_at": r["completed_at"] or (mark["created_at"] if mark else None),
                        "done": bool(r["completed_at"]), "skipped": bool(mark and mark["state"] == "skipped" and not r["completed_at"]),
                        "when": core.parse_dt(slot["start_dt"]) if slot else core.parse_dt(r["start_dt"])})
        elif it["kind"] == "food" and wants_food:
            key = it["key"]
            mark = marks.get(key)
            done_at = mark["created_at"] if mark and mark["state"] == "done" else None
            if not done_at and first_food and legacy_fed and not food_marked:
                done_at = legacy_fed                       # meal served before meals were tracked one by one
            first_food = False
            window = f"{_fmt(it['start'])}–{_fmt(it['end'])}" if it["start"] and it["end"] else (_fmt(it["start"]) if it["start"] else "")
            sub = " · ".join(x for x in (window, it["venue"]) if x)
            seq.append({"key": key, "kind": "FOOD", "title": it["title"] or "Meal", "icon": "🍽️", "sub": sub,
                        "hint": f"{'Veg' if pref == 'veg' else 'Non-veg'}{' · ' + sub if sub else ''} · show this QR at the food counter",
                        "reg": first, "extra": scheduler.food_key(scope, key), "food": it,
                        "done_at": done_at, "done": bool(done_at), "skipped": bool(mark and mark["state"] == "skipped"),
                        "timed": bool(it["start"]), "when": core.parse_dt(it["start"])})
    # a meal with a time goes where its time falls among this person's events
    timed = [s for s in seq if s["kind"] == "FOOD" and s.get("timed")]
    base = [s for s in seq if not (s["kind"] == "FOOD" and s.get("timed"))]
    for f in sorted(timed, key=lambda s: s["when"]):
        at = next((i for i, s in enumerate(base) if s["kind"] == "EV" and s["when"] and s["when"] > f["when"]), len(base))
        base.insert(at, f)
    out += base
    current = next((i for i, s in enumerate(out) if not s["done"] and not s["skipped"]), None)
    for i, s in enumerate(out):
        s["n"] = i + 1
        s["state"] = "done" if s["done"] else ("skipped" if s["skipped"] else ("current" if i == current else "locked"))
        code = s["reg"]["pass_code"]
        s["qr"] = None
        if s["state"] == "current" and (s["kind"] != "EV" or s["extra"]):
            s["qr"] = payload(s["kind"], code, s["extra"])
    return out


def signature(st):
    """Changes whenever any task changes (done, skipped, moved to another slot), so the ticket can refresh itself."""
    return ".".join(f"{s['key']}:{s['state'][0]}:{s.get('extra', '')}" for s in st)


def current(st):
    return next((s for s in st if s["state"] == "current"), None)


# ------------------------------------------------------------------ desks
def desk_slots(event_id):
    return scheduler.slots_of(event_id)


def desk_meals(scope):
    return [it for it in scheduler.flow(scope) if it["kind"] == "food"]


def _skip(st, upto, user_id, scope, actor_id):
    """Organiser override: everything before task `upto` that isn't done is skipped (entry counts as arrived)."""
    regs = scope_regs(user_id, scope)
    now = core.now_iso()
    for s in st:
        if s is upto or s["key"] == upto["key"]:
            break
        if s["state"] in ("done", "skipped"):
            continue
        if s["kind"] == "IN":
            ids = [r["id"] for r in regs]
            ex(f"UPDATE registrations SET attended=1, checkin_time=? WHERE id IN ({','.join('?' * len(ids))}) AND attended=0", (now, *ids))
        else:
            ex("""INSERT INTO task_marks (user_id, scope_id, task_key, state, actor_id) VALUES (?,?,?,'skipped',?)
                  ON CONFLICT(user_id, scope_id, task_key) DO UPDATE SET state='skipped', actor_id=excluded.actor_id""",
               (user_id, scope, s["key"], actor_id))
    core.audit("task.skip", f"user #{user_id} at #{scope} before {upto['key']}")


def scan(text, station, desk_event, can_staff=None, actor_id=None, slot_id=None, meal="", skip=False):
    """One scan at a desk. station: IN (entry gate), EV (an event's desk running one slot) or FOOD (a food counter
    serving one meal). desk_event: the event day for IN/FOOD, the event itself for EV. Returns JSON-ready data."""
    station = station if station in DESKS else ""
    if not station:
        return {"ok": False, "level": "error", "message": "Pick what this desk is scanning for first."}
    try:
        kind, code, extra = parse(text)
    except ValueError as e:
        return {"ok": False, "level": "error", "message": str(e)}
    r = q("""SELECT r.*, u.name, u.department, u.year, u.college_name, e.title, e.parent_id
             FROM registrations r JOIN users u ON u.id=r.user_id JOIN events e ON e.id=r.event_id
             WHERE r.pass_code=?""", (code,), one=True)
    if not r:
        return {"ok": False, "level": "error", "message": f"No ticket found for “{code}”."}
    if can_staff and not can_staff(r["event_id"]):
        return {"ok": False, "level": "error", "message": "This ticket is for an event you don't manage."}
    desk = q("SELECT * FROM events WHERE id=?", (desk_event,), one=True) if desk_event else None
    if not desk:
        return {"ok": False, "level": "error", "message": "This desk isn't set up for an event."}
    scope = scope_id(r)
    desk_scope = desk["parent_id"] or desk["id"]
    if scope != desk_scope:
        return {"ok": False, "level": "error", "message": f"Wrong event. This ticket is for {r['title']}."}
    if kind and kind != station:
        return {"ok": False, "level": "error",
                "message": f"That's the {KIND_NAMES[kind]} QR. This is the {DESKS[station]}: ask them for the {KIND_NAMES[station]} QR on their ticket."}
    person = {"name": r["name"], "department": r["department"], "year": r["year"], "college": r["college_name"],
              "team": r["team_name"], "food": r["food_pref"], "slot": None}
    if r["status"] != "confirmed":
        label = {"payment_review": "payment is still being verified", "pending_payment": "hasn't paid yet",
                 "cancelled": "was cancelled", "waitlisted": "is still on the waitlist"}.get(r["status"], r["status"])
        return {"ok": False, "level": "error", "person": person, "message": f"{r['name']}'s ticket {label}."}
    user_id = r["user_id"]
    st = steps(user_id, scope)
    target = None
    if station == "IN":
        target = st[0]
    elif station == "EV":
        if desk["kind"] == "fest":
            return {"ok": False, "level": "error", "message": "Choose which event this desk is for."}
        target = next((s for s in st if s["kind"] == "EV" and s["reg"]["event_id"] == desk["id"]), None)
        if not target:
            return {"ok": False, "level": "error", "person": person, "message": f"{r['name']} isn't registered for {desk['title']}."}
        if kind == "EV" and code != target["reg"]["pass_code"]:
            return {"ok": False, "level": "error", "person": person,
                    "message": f"That's {r['name']}'s QR for {r['title']}, not {desk['title']}."}
        slot = target["slot"]
        who = f"Team {target['reg']['team_name']}" if target["reg"]["team_name"] else r["name"]
        if not slot:
            return {"ok": False, "level": "error", "person": person,
                    "message": f"{who} has no slot for {desk['title']} yet. Allocate slots, or give them one under Slots & flow."}
        person["slot"] = target["sub"]
        person["team"] = target["reg"]["team_name"]
        slots = desk_slots(desk["id"])
        running = next((s for s in slots if str(s["id"]) == str(slot_id or "")), None)
        if not running and len(slots) == 1:
            running = slots[0]
        if not running:
            return {"ok": False, "level": "error", "person": person, "message": "Choose which slot this desk is running first."}
        if kind == "EV" and extra != target["extra"]:
            return {"ok": False, "level": "error", "person": person, "steps": _brief(st),
                    "message": f"Old QR. {who}'s slot was changed to {target['sub']}. Ask them to reopen their ticket."}
        if running["id"] != slot["id"]:
            return {"ok": False, "level": "error", "person": person, "steps": _brief(st), "wrong_slot": True,
                    "message": f"Wrong slot. {who} is booked for {target['sub']}, not {scheduler.slot_name(running, slots)}."}
    else:
        meals = [s for s in st if s["kind"] == "FOOD"]
        if not meals:
            return {"ok": False, "level": "error", "person": person, "message": f"No meal is included for {r['name']}."}
        if kind == "FOOD":
            target = next((s for s in meals if s["extra"] == extra), None)
            if not target:
                return {"ok": False, "level": "error", "person": person, "message": "That meal QR is out of date. Ask them to reopen their ticket."}
        if meal:
            chosen = next((s for s in meals if s["key"] == meal), None)
            if not chosen:
                return {"ok": False, "level": "error", "person": person, "message": f"{r['name']} doesn't have this meal."}
            if target and target["key"] != chosen["key"]:
                return {"ok": False, "level": "error", "person": person,
                        "message": f"That's the QR for {target['title']}. This counter is serving {chosen['title']}."}
            target = chosen
        if not target:
            target = next((s for s in meals if s["state"] == "current"), None) or meals[0]
    if target["state"] == "done":
        when = core.parse_dt(target["done_at"])
        return {"ok": False, "level": "warn", "person": person, "steps": _brief(st),
                "message": f"{r['name']}: “{target['title']}” was already done" + (f" at {when.strftime('%I:%M %p').lstrip('0')}." if when else ".")}
    if target["state"] == "skipped":
        pass                                    # skipped earlier by the organisers, but they made it after all
    elif target["state"] != "current":
        cur = current(st)
        if not skip:
            return {"ok": False, "level": "error", "person": person, "steps": _brief(st), "can_skip": True,
                    "message": f"Not yet. {r['name']} must first: {cur['title']} (task {cur['n']} of {len(st)})."}
        _skip(st, target, user_id, scope, actor_id)
    now = core.now_iso()
    regs = scope_regs(user_id, scope)
    ids = [x["id"] for x in regs]
    marks = ",".join("?" * len(ids))
    if target["kind"] == "IN":
        ex(f"UPDATE registrations SET attended=1, checkin_time=? WHERE id IN ({marks}) AND attended=0", (now, *ids))
    elif target["kind"] == "EV":
        ex("""UPDATE registrations SET completed_at=?, slot_in_at=?, attended=1, checkin_time=COALESCE(checkin_time, ?)
              WHERE id=?""", (now, now, now, target["reg"]["id"]))
        ex("DELETE FROM task_marks WHERE user_id=? AND scope_id=? AND task_key=?", (user_id, scope, target["key"]))
    else:
        ex("""INSERT INTO task_marks (user_id, scope_id, task_key, state, actor_id) VALUES (?,?,?,'done',?)
              ON CONFLICT(user_id, scope_id, task_key) DO UPDATE SET state='done', actor_id=excluded.actor_id""",
           (user_id, scope, target["key"], actor_id))
        ex(f"UPDATE registrations SET food_at=? WHERE id IN ({marks}) AND food_at IS NULL", (now, *ids))
    st = steps(user_id, scope)
    nxt = current(st)
    title = target["title"]
    if target["kind"] == "IN":
        msg = f"Welcome, {r['name']}!"
        note = f"You're checked in at {desk['title'] if desk['kind'] == 'fest' else r['title']}. +{core.POINTS_ATTEND} points."
    elif target["kind"] == "EV":
        msg = f"{r['name']} is in the right slot for {title}. Certificate unlocked!"
        note = f"You're checked in to {title} ({target['sub']}). Your certificate is ready. +{core.POINTS_COMPLETE} points."
    else:
        msg = f"{title} served to {r['name']} ({'veg' if r['food_pref'] == 'veg' else 'non-veg'})."
        note = f"Enjoy your {title.lower()}!"
    note += f" Next: {nxt['title']}." if nxt else " That's every task done. 🎉"
    link = url_for("events.certificate", code=target["reg"]["pass_code"]) if target["kind"] == "EV" else \
        url_for("events.ticket", code=r["pass_code"])
    core.notify(user_id, "event", note, link, actor_id)
    try:
        import live
        live.refresh_user(user_id)
    except Exception:                       # the live notice is a nicety; never fail a scan over it
        pass
    return {"ok": True, "level": "success", "message": msg, "person": person, "done": target["kind"],
            "next": nxt["title"] if nxt else None, "steps": _brief(st), "reg": target["reg"]["pass_code"]}


def _brief(st):
    return [{"title": s["title"], "state": s["state"], "icon": s["icon"]} for s in st]


def counts(scope):
    """Desk numbers for an event day: entered, events checked in, meals served (people)."""
    row = q("""SELECT COUNT(DISTINCT r.user_id) people,
                      COUNT(DISTINCT CASE WHEN r.attended=1 THEN r.user_id END) entered,
                      COALESCE(SUM(CASE WHEN r.completed_at IS NOT NULL THEN 1 ELSE 0 END),0) events_done,
                      COUNT(*) tickets
               FROM registrations r JOIN events e ON e.id=r.event_id
               WHERE (e.id=? OR e.parent_id=?) AND r.status='confirmed' AND e.is_removed=0""", (scope, scope), one=True)
    fed = scalar("SELECT COUNT(DISTINCT user_id) FROM task_marks WHERE scope_id=? AND state='done' AND task_key LIKE 'food%'", (scope,)) or 0
    legacy = scalar("""SELECT COUNT(DISTINCT r.user_id) FROM registrations r JOIN events e ON e.id=r.event_id
                       WHERE (e.id=? OR e.parent_id=?) AND r.food_at IS NOT NULL AND r.user_id NOT IN
                       (SELECT user_id FROM task_marks WHERE scope_id=? AND task_key LIKE 'food%')""", (scope, scope, scope)) or 0
    return {"people": row["people"], "entered": row["entered"], "events_done": row["events_done"], "tickets": row["tickets"],
            "fed": fed + legacy}
