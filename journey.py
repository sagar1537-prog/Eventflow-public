"""Step-by-step event day ("task-based guidance").

Every confirmed participant walks through the same ordered steps, each unlocked only after the one before it:

    1. Entry       scan the ENTRY QR at the gate             -> marks attendance for the whole event / fest
    2. Event       scan the COMPLETION QR when the event ends -> unlocks the certificate for that event
    3. Food        scan the FOOD QR at the food counter       -> only when the event serves meals
    4. More events one completion QR per further event in the same fest (each unlocks its own certificate)

Each step has its own QR code. A QR carries the step kind, the ticket code and a short signature, so a student can't
make a "food" QR from their ticket code, and the server refuses any step that isn't the current one.
"""
import hashlib
import hmac
import re

from flask import current_app, url_for

import core
from db import q, ex

KIND_NAMES = {"IN": "entry", "DONE": "event completion", "FOOD": "food"}
STATIONS = {"": "Any step (auto)", "IN": "Entry gate", "DONE": "Event completion", "FOOD": "Food counter"}
_PAYLOAD = re.compile(r"^EF:(IN|DONE|FOOD):([A-Z0-9-]{4,40}):([A-F0-9]{6,12})$")


# ------------------------------------------------------------------ QR payloads
def _sig(kind, code):
    key = str(current_app.config["SECRET_KEY"]).encode()
    return hmac.new(key, f"{kind}:{code}".encode(), hashlib.sha256).hexdigest()[:8].upper()


def payload(kind, code):
    """What the QR for one step contains, e.g. EF:FOOD:EVF-ABC123:1F2E3D4C."""
    return f"EF:{kind}:{code}:{_sig(kind, code)}"


def parse(text):
    """-> (kind or None, ticket code) from a scanned QR or a typed code. Raises ValueError for a forged QR."""
    text = (text or "").strip().upper()
    if text.startswith("HTTP"):
        text = text.rstrip("/").rsplit("/", 1)[-1]
    m = _PAYLOAD.match(text)
    if m:
        kind, code, sig = m.groups()
        if not hmac.compare_digest(sig, _sig(kind, code)):
            raise ValueError("This QR code isn't valid. Ask the participant to open their ticket in EventFlow.")
        return kind, code
    if text.startswith("EF:"):
        raise ValueError("This QR code isn't valid. Ask the participant to open their ticket in EventFlow.")
    return None, text


# ------------------------------------------------------------------ steps
def scope_id(reg):
    """A fest is one visit (one entry, one meal); a standalone event is its own visit."""
    return reg["parent_id"] or reg["event_id"]


def scope_regs(user_id, scope):
    return q("""SELECT r.*, e.title, e.start_dt, e.end_dt, e.venue, e.meals_count, e.parent_id, e.label
                FROM registrations r JOIN events e ON e.id=r.event_id
                WHERE r.user_id=? AND r.status='confirmed' AND (e.id=? OR e.parent_id=?)
                ORDER BY COALESCE(r.slot_start, e.start_dt), e.start_dt, r.id""", (user_id, scope, scope))


def steps(user_id, scope):
    """Ordered steps for one participant at one event or fest. Each: key, kind, title, hint, reg, done_at, state."""
    regs = scope_regs(user_id, scope)
    if not regs:
        return []
    ev = q("SELECT id, title, venue, meals_count, kind FROM events WHERE id=?", (scope,), one=True)
    entry_at = min((r["checkin_time"] for r in regs if r["attended"] and r["checkin_time"]), default=None)
    entered = any(r["attended"] for r in regs)
    out = [{"key": "entry", "kind": "IN", "title": "Check in at the entrance",
            "hint": f"Show this QR at the {ev['title']} entry desk" + (f" ({ev['venue']})" if ev["venue"] else ""),
            "reg": regs[0], "done_at": entry_at if entered else None, "done": entered, "icon": "🚪"}]
    events = []
    for r in regs:
        slot = ""
        if r["slot_start"]:
            slot = f"Your slot {core.parse_dt(r['slot_start']).strftime('%I:%M %p').lstrip('0')}" + \
                   (f", {r['slot_venue']}" if r["slot_venue"] else "")
        else:
            slot = f"Starts {core.parse_dt(r['start_dt']).strftime('%a %d %b, %I:%M %p').replace(' 0', ' ')}"
        events.append({"key": f"event-{r['id']}", "kind": "DONE", "title": f"Take part in {r['title']}",
                       "hint": f"{slot} · show this QR when you finish to get your certificate",
                       "reg": r, "done_at": r["completed_at"], "done": bool(r["completed_at"]), "icon": "🎯",
                       "cert": True})
    out.append(events[0])
    wants_food = any(r["food_pref"] != "none" for r in regs)
    serves_food = (ev["meals_count"] or 0) > 0 or any((r["meals_count"] or 0) > 0 for r in regs)
    if wants_food and serves_food:
        fed = next((r["food_at"] for r in regs if r["food_at"]), None)
        pref = next((r["food_pref"] for r in regs if r["food_pref"] != "none"), "veg")
        out.append({"key": "food", "kind": "FOOD", "title": "Collect your meal",
                    "hint": f"{'Veg' if pref == 'veg' else 'Non-veg'} · show this QR at the food counter",
                    "reg": regs[0], "done_at": fed, "done": bool(fed), "icon": "🍽️"})
    out.extend(events[1:])
    current = next((i for i, s in enumerate(out) if not s["done"]), None)
    for i, s in enumerate(out):
        s["n"] = i + 1
        s["state"] = "done" if s["done"] else ("current" if i == current else "locked")
        s["qr"] = payload(s["kind"], s["reg"]["pass_code"]) if s["state"] == "current" else None
    return out


def signature(st):
    """Changes whenever any step changes, so a ticket page can refresh itself after a scan."""
    return ".".join(f"{s['key']}:{1 if s['done'] else 0}" for s in st)


# ------------------------------------------------------------------ scanning
def scan(text, station="", desk_event=None, can_staff=None, actor_id=None):
    """Handle one scan at a check-in desk. Returns a dict for the JSON response."""
    station = station if station in STATIONS else ""
    try:
        kind, code = parse(text)
    except ValueError as e:
        return {"ok": False, "level": "error", "message": str(e)}
    r = q("""SELECT r.*, u.name, u.department, u.year, u.college_name, e.title, e.parent_id
             FROM registrations r JOIN users u ON u.id=r.user_id JOIN events e ON e.id=r.event_id
             WHERE r.pass_code=?""", (code,), one=True)
    if not r:
        return {"ok": False, "level": "error", "message": f"No ticket found for “{code}”."}
    if can_staff and not can_staff(r["event_id"]):
        return {"ok": False, "level": "error", "message": "This ticket is for an event you don't manage."}
    scope = scope_id(r)
    if desk_event:
        desk = q("SELECT id, parent_id FROM events WHERE id=?", (desk_event,), one=True)
        if not desk or scope not in (desk["id"], desk["parent_id"]):
            return {"ok": False, "level": "error", "message": f"Wrong event. This ticket is for {r['title']}."}
    if r["status"] != "confirmed":
        label = {"payment_review": "payment is still being verified", "pending_payment": "hasn't paid yet",
                 "cancelled": "was cancelled", "waitlisted": "is still on the waitlist"}.get(r["status"], r["status"])
        return {"ok": False, "level": "error", "message": f"{r['name']}'s ticket {label}."}
    person = {"name": r["name"], "department": r["department"], "year": r["year"], "college": r["college_name"],
              "team": r["team_name"], "food": r["food_pref"],
              "slot": f"{core.parse_dt(r['slot_start']).strftime('%I:%M %p').lstrip('0')}, {r['slot_venue']}" if r["slot_start"] else None}
    st = steps(r["user_id"], scope)
    if station and kind and kind != station:
        return {"ok": False, "level": "error", "person": person,
                "message": f"That's the {KIND_NAMES[kind]} QR. This desk is set to {STATIONS[station].lower()}."}
    want = kind or station
    if want == "IN":
        target = st[0]
    elif want == "FOOD":
        target = next((s for s in st if s["kind"] == "FOOD"), None)
        if not target:
            return {"ok": False, "level": "error", "person": person, "message": f"No meal is included for {r['name']}."}
    elif want == "DONE":
        target = next((s for s in st if s["kind"] == "DONE" and s["reg"]["id"] == r["id"]), None)
    else:                                   # typed code at an "any step" desk: do whatever is next
        target = next((s for s in st if s["state"] == "current"), None)
        if not target:
            return {"ok": False, "level": "warn", "person": person, "message": f"{r['name']} has finished every step. 🎉"}
    if target["done"]:
        when = core.parse_dt(target["done_at"])
        return {"ok": False, "level": "warn", "person": person, "steps": _brief(st),
                "message": f"{r['name']}: “{target['title']}” was already done" + (f" at {when.strftime('%I:%M %p').lstrip('0')}." if when else ".")}
    if target["state"] != "current":
        cur = next(s for s in st if s["state"] == "current")
        return {"ok": False, "level": "error", "person": person, "steps": _brief(st),
                "message": f"Not yet. {r['name']} must first: {cur['title']} (step {cur['n']} of {len(st)})."}
    now = core.now_iso()
    regs = scope_regs(r["user_id"], scope)
    ids = [x["id"] for x in regs]
    marks = ",".join("?" * len(ids))
    if target["kind"] == "IN":
        ex(f"UPDATE registrations SET attended=1, checkin_time=? WHERE id IN ({marks}) AND attended=0", (now, *ids))
    elif target["kind"] == "DONE":
        ex("UPDATE registrations SET completed_at=?, attended=1, checkin_time=COALESCE(checkin_time, ?) WHERE id=?",
           (now, now, target["reg"]["id"]))
    else:
        ex(f"UPDATE registrations SET food_at=? WHERE id IN ({marks})", (now, *ids))
    st = steps(r["user_id"], scope)
    nxt = next((s for s in st if s["state"] == "current"), None)
    title = target["reg"]["title"]
    if target["kind"] == "IN":
        msg = f"Welcome, {r['name']}!"
        note = f"You're checked in at {title}. +{core.POINTS_ATTEND} points."
    elif target["kind"] == "DONE":
        msg = f"{r['name']} completed {title}. Certificate unlocked!"
        note = f"You completed {title}. Your certificate is ready. +{core.POINTS_COMPLETE} points."
    else:
        msg = f"Meal served to {r['name']} ({'veg' if r['food_pref'] == 'veg' else 'non-veg'})."
        note = "Enjoy your meal!"
    note += f" Next: {nxt['title']}." if nxt else " That's every step done. 🎉"
    link = url_for("events.certificate", code=target["reg"]["pass_code"]) if target["kind"] == "DONE" else \
        url_for("events.ticket", code=r["pass_code"])
    core.notify(r["user_id"], "event", note, link, actor_id)
    return {"ok": True, "level": "success", "message": msg, "person": person, "done": target["kind"],
            "next": nxt["title"] if nxt else None, "steps": _brief(st)}


def _brief(st):
    return [{"title": s["title"], "state": s["state"], "icon": s["icon"]} for s in st]
