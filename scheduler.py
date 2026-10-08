"""Smart slot planner: gives every participant (or team) a personal time and room, around their availability.

How it decides
  * A team is one unit: every member gets the same slot, and the team is free only when *all* members are free.
  * Someone is busy when they have another slot (any event, any college), another short event they registered for,
    or a "busy time" they added on their ticket. A buffer (default 10 min) is kept around each busy period for walking.
  * Slot times run from the first slot, every (duration + gap) minutes, skipping breaks (lunch etc.) and stopping at
    the finish time if one is set. Each room holds one unit per time.
  * Units with the fewest possible times are placed first, so nobody with a tight schedule is left out; among equals,
    bigger teams and earlier registrations go first. Each unit takes the earliest free time it can make, which keeps
    the whole session as short as possible.
  * Slots set by hand (organiser exceptions) are locked: the planner keeps them and works around them.
  * Anyone who can't be placed is listed with the reason, so the organiser can add a room, extend the finish time or
    move someone by hand.
"""
from datetime import timedelta

import core
from db import q

MAX_ROUNDS = 400          # safety cap when no finish time is given


def parse_breaks(text, day):
    """'13:00-14:00, 16:00-16:15' -> [(start, end)] on the given date. Bad parts are ignored."""
    out = []
    for part in (text or "").replace(";", ",").split(","):
        if "-" not in part:
            continue
        a, b = (x.strip() for x in part.split("-", 1))
        try:
            ha, ma = (int(x) for x in a.split(":"))
            hb, mb = (int(x) for x in b.split(":"))
        except ValueError:
            continue
        s = day.replace(hour=ha, minute=ma, second=0, microsecond=0)
        e = day.replace(hour=hb, minute=mb, second=0, microsecond=0)
        if e > s:
            out.append((s, e))
    return out


def _overlaps(a1, a2, b1, b2):
    return a1 < b2 and b1 < a2


def units_for(event_id):
    """Confirmed participants grouped into units (teams stay together)."""
    regs = q("""SELECT r.id, r.user_id, r.team_name, r.slot_start, r.slot_end, r.slot_venue, r.slot_locked, r.created_at,
                       u.name FROM registrations r JOIN users u ON u.id=r.user_id
                WHERE r.event_id=? AND r.status='confirmed' ORDER BY r.created_at, r.id""", (event_id,))
    units = {}
    for r in regs:
        key = ("team", r["team_name"].strip().lower()) if r["team_name"] else ("solo", r["id"])
        u = units.setdefault(key, {"key": key, "label": f"Team {r['team_name']}" if r["team_name"] else r["name"],
                                   "regs": [], "created": r["created_at"]})
        u["regs"].append(r)
    return list(units.values())


def busy_map(user_ids, event_id, buffer_min):
    """{user_id: [(start, end)]} of times each person can't do this event."""
    if not user_ids:
        return {}
    c, a = core.in_clause(user_ids)
    pad = timedelta(minutes=buffer_min)
    busy = {u: [] for u in user_ids}
    for r in q(f"""SELECT r.user_id, r.slot_start, r.slot_end, e.start_dt, e.end_dt FROM registrations r
                   JOIN events e ON e.id=r.event_id
                   WHERE r.user_id IN {c} AND r.status='confirmed' AND r.event_id!=? AND e.kind!='fest'""", a + [event_id]):
        if r["slot_start"] and r["slot_end"]:
            s, e = core.parse_dt(r["slot_start"]), core.parse_dt(r["slot_end"])
        else:
            s, e = core.parse_dt(r["start_dt"]), core.parse_dt(r["end_dt"])
            if not s or not e or (e - s) > timedelta(hours=4):   # long / multi-day events don't block a whole day
                continue
        if s and e:
            busy[r["user_id"]].append((s - pad, e + pad))
    for b in q(f"""SELECT user_id, start_dt, end_dt FROM busy_times WHERE user_id IN {c}
                   AND (event_id IS NULL OR event_id=?)""", a + [event_id]):
        s, e = core.parse_dt(b["start_dt"]), core.parse_dt(b["end_dt"])
        if s and e:
            busy[b["user_id"]].append((s, e))
    return busy


def plan(event_id, start, duration, gap, rooms, finish=None, breaks=(), buffer_min=10, keep_locked=True):
    """Work out slots without saving anything. Returns a dict the Studio shows as a preview and `apply` saves."""
    rooms = [r.strip() for r in rooms if r and r.strip()] or ["Main Hall"]
    rooms = list(dict.fromkeys(rooms))            # unique, keep order
    dur, step = timedelta(minutes=duration), timedelta(minutes=duration + gap)
    units = units_for(event_id)
    locked = [u for u in units if keep_locked and any(r["slot_locked"] and r["slot_start"] for r in u["regs"])]
    free_units = [u for u in units if u not in locked]
    busy = busy_map(sorted({r["user_id"] for u in units for r in u["regs"]}), event_id, buffer_min)

    # every possible (time) in order; breaks and the finish time cut it short
    times, t = [], start
    for _ in range(MAX_ROUNDS):
        if finish and t + dur > finish:
            break
        if not any(_overlaps(t, t + dur, bs, be) for bs, be in breaks):
            times.append(t)
        t += step
        if not finish and len(times) * len(rooms) >= len(units) * 3 + len(rooms) * 4:
            break                                  # plenty of room for everyone, even with tight schedules
    taken = {}                                     # (time, room) -> unit label
    for u in locked:                               # exceptions keep their slot and block that cell
        r0 = next(r for r in u["regs"] if r["slot_start"])
        taken[(core.parse_dt(r0["slot_start"]), r0["slot_venue"])] = u["label"]

    def free_at(u, tm):
        return all(not any(_overlaps(tm, tm + dur, bs, be) for bs, be in busy.get(r["user_id"], [])) for r in u["regs"])

    for u in free_units:
        u["options"] = [tm for tm in times if free_at(u, tm)]
    order = sorted(free_units, key=lambda u: (len(u["options"]), -len(u["regs"]), str(u["created"])))
    placed, unplaced, moved_for_clash = [], [], 0
    for u in order:
        cell = None
        for tm in u["options"]:
            room = next((rm for rm in rooms if (tm, rm) not in taken), None)
            if room:
                cell = (tm, room)
                break
        if not cell:
            reason = ("busy at every possible time" if not u["options"] else
                      "all rooms are full at the times they're free")
            unplaced.append({"label": u["label"], "people": len(u["regs"]), "reason": reason})
            continue
        if times and cell[0] != times[0] and not free_at(u, times[0]):
            moved_for_clash += 1
        taken[cell] = u["label"]
        placed.append({"unit": u, "start": cell[0], "end": cell[0] + dur, "room": cell[1]})
    placed.sort(key=lambda p: (p["start"], rooms.index(p["room"]) if p["room"] in rooms else 99))
    people = sum(len(p["unit"]["regs"]) for p in placed)
    return {"placed": placed, "unplaced": unplaced, "locked": [{"label": u["label"], "regs": u["regs"]} for u in locked],
            "rooms": rooms, "units": len(units), "people": people, "clash_free_moves": moved_for_clash,
            "first": placed[0]["start"] if placed else None, "last": max((p["end"] for p in placed), default=None),
            "duration": duration, "gap": gap, "buffer": buffer_min}


def apply(event_id, result):
    """Save a plan. Returns [(registration row, start, end, room)] for people whose slot changed (to notify)."""
    from db import ex
    changed = []
    for p in result["placed"]:
        for r in p["unit"]["regs"]:
            s, e = core.iso(p["start"]), core.iso(p["end"])
            if (r["slot_start"] or "")[:16] != s[:16] or r["slot_venue"] != p["room"]:
                ex("""UPDATE registrations SET slot_start=?, slot_end=?, slot_venue=?, slot_locked=0, slot_in_at=NULL
                      WHERE id=?""", (s, e, p["room"], r["id"]))
                changed.append((r, p["start"], p["end"], p["room"]))
    return changed


def sessions(event_id):
    """Slot sessions in use: [{key, start, end, room, members: [rows]}], for the Studio and the slot scanner."""
    out = {}
    for r in q("""SELECT r.id, r.pass_code, r.team_name, r.slot_start, r.slot_end, r.slot_venue, r.slot_locked, r.slot_in_at,
                         r.completed_at, r.user_id, u.name, u.username, u.avatar
                  FROM registrations r JOIN users u ON u.id=r.user_id
                  WHERE r.event_id=? AND r.status='confirmed' AND r.slot_start IS NOT NULL
                  ORDER BY r.slot_start, r.slot_venue, r.team_name, u.name""", (event_id,)):
        key = session_key(r["slot_start"], r["slot_venue"])
        sx = out.setdefault(key, {"key": key, "start": r["slot_start"], "end": r["slot_end"], "room": r["slot_venue"] or "",
                                  "members": []})
        sx["members"].append(r)
    return list(out.values())


def session_key(slot_start, room):
    """Short, stable id of a slot session (time + room). Part of the slot QR, so an old QR stops working if moved."""
    import hashlib
    return hashlib.sha1(f"{(slot_start or '')[:16]}|{(room or '').strip().lower()}".encode()).hexdigest()[:6].upper()
