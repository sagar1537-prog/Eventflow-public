"""Time slots, the slot allocator and the event-day flow.

Time slots
  Every event runs in one or more time slots (table event_slots). A slot has a start, an end, an optional room and the
  number of members it holds. Every confirmed participant is allocated to exactly one slot of each event they joined.
  A team counts as all of its members and always stays together in one slot.

The allocator (one event, or a whole fest at once)
  * capacity: a slot never gets more members than its limit
  * no collisions: nobody is put in two short slots that overlap (a slot longer than LONG_SLOT, like an all-day
    hackathon, doesn't block other events), in a slot during a busy time they marked on their ticket, or in a slot
    that clashes with their slots at other colleges' events
  * the event-day order: when the organisers put event A before event B in the flow, someone in both gets an A slot
    that starts no later than their B slot
  * fill in order: every event's first slot fills before its second, and so on. With 20 members per slot and 100
    people in each of three events, slot 1 takes 20 from each event, slot 2 the next 20, and so on, and people in
    several events are spread over different slots so they never collide
  * deadline first: events are filled in the event-day order, each slot in time order. A slot goes first to the
    people who can't wait (whose later events need an early slot here, checked by looking ahead at their other
    events), then to those with the fewest possible slots, bigger teams and earlier registrations. If a slot is only
    full because of someone who could just as well go elsewhere, that unit is moved to make room
  * an event with a single slot covering its whole time (not split into slots) only checks capacity and never blocks
    the person's other events
  * exceptions: a slot an organiser set by hand is locked; the allocator keeps it and works around it
  * anyone who can't be placed is listed with the reason, so the organisers can add a slot or move them by hand

The flow
  The order of a participant's event day: entry first (always), then the scope's events and meals in the order the
  organisers drag them into (table flow_items). Without a saved flow: events by start time, one meal after the first
  event when the event serves food.
"""
import hashlib
from datetime import timedelta

import core
from db import q, ex, scalar, in_clause

LONG_SLOT = timedelta(hours=4)


# ====================================================================== reading slots
def scope_of(event):
    """The fest an event belongs to, else the event itself (one event day)."""
    return event["parent_id"] or event["id"]


def scope_events(scope_id, include_drafts=True):
    """The events whose slots and flow belong to this event day: a fest's events, or the event itself."""
    e = q("SELECT * FROM events WHERE id=?", (scope_id,), one=True)
    if not e:
        return []
    if e["kind"] != "fest":
        return [e]
    extra = "" if include_drafts else "AND status!='draft'"
    return q(f"SELECT * FROM events WHERE parent_id=? AND is_removed=0 {extra} ORDER BY start_dt, position, id", (scope_id,))


def slots_of(event_id):
    return q("SELECT * FROM event_slots WHERE event_id=? ORDER BY start_dt, position, id", (event_id,))


def slots_for(event_ids):
    out = {i: [] for i in event_ids}
    if not event_ids:
        return out
    c, a = in_clause(event_ids)
    for s in q(f"SELECT * FROM event_slots WHERE event_id IN {c} ORDER BY start_dt, position, id", a):
        out.setdefault(s["event_id"], []).append(s)
    return out


def slot_key(slot):
    """Short id of a slot (and its times). Part of the event QR, so an old QR stops working when the slot changes."""
    if not slot:
        return ""
    raw = f"{slot['id']}|{(slot['start_dt'] or '')[:16]}|{(slot['end_dt'] or '')[:16]}"
    return hashlib.sha1(raw.encode()).hexdigest()[:6].upper()


def slot_name(slot, slots=None):
    """'Slot 2' (its own label, else its number among the event's slots)."""
    if slot["label"]:
        return slot["label"]
    slots = slots if slots is not None else slots_of(slot["event_id"])
    n = next((i + 1 for i, s in enumerate(slots) if s["id"] == slot["id"]), None)
    return f"Slot {n}" if n and len(slots) > 1 else "Your slot"


def _t(v):
    d = core.parse_dt(v)
    return d.strftime("%I:%M %p").lstrip("0") if d else ""


def slot_text(slot, slots=None, venue=None):
    """'Slot 2 · 11:00 AM–12:00 PM · Lab 1' (date added when it isn't today)."""
    s = core.parse_dt(slot["start_dt"])
    when = f"{_t(slot['start_dt'])}–{_t(slot['end_dt'])}"
    if s and s.date() != core.now().date():
        when = s.strftime("%a %d %b, ") + when
    room = slot["venue"] or venue
    return f"{slot_name(slot, slots)} · {when}" + (f" · {room}" if room else "")


def long_slot(slot):
    s, e = core.parse_dt(slot["start_dt"]), core.parse_dt(slot["end_dt"])
    return bool(s and e and e - s > LONG_SLOT)


def reg_slot(reg):
    return q("SELECT * FROM event_slots WHERE id=?", (reg["slot_id"],), one=True) if reg["slot_id"] else None


# ====================================================================== writing slots
def add_slot(event_id, start, end, venue=None, capacity=20, label=None):
    pos = (scalar("SELECT MAX(position) FROM event_slots WHERE event_id=?", (event_id,)) or 0) + 1
    return ex("INSERT INTO event_slots (event_id, label, start_dt, end_dt, venue, capacity, position) VALUES (?,?,?,?,?,?,?)",
              (event_id, (label or "").strip()[:40] or None, core.iso(core.parse_dt(start)), core.iso(core.parse_dt(end)),
               (venue or "").strip()[:80] or None, max(1, int(capacity)), pos))


def ensure_default_slot(event):
    """An event with no time slots runs in one slot: the whole event, as many members as it has seats."""
    if event["kind"] == "fest" or scalar("SELECT 1 FROM event_slots WHERE event_id=?", (event["id"],)):
        return None
    return add_slot(event["id"], event["start_dt"], event["end_dt"], None, max(1, event["capacity"] or 1))


def assign(regs, slot, locked=False):
    """Put registrations in a slot. Returns the ones whose slot actually changed."""
    if not slot:
        return []
    venue = slot["venue"] or scalar("SELECT venue FROM events WHERE id=?", (slot["event_id"],))
    changed = []
    for r in regs:
        moved = r["slot_id"] != slot["id"] or (r["slot_start"] or "")[:16] != (slot["start_dt"] or "")[:16]
        ex("""UPDATE registrations SET slot_id=?, slot_start=?, slot_end=?, slot_venue=?, slot_locked=?,
              slot_in_at=CASE WHEN slot_id=? THEN slot_in_at ELSE NULL END WHERE id=?""",
           (slot["id"], slot["start_dt"], slot["end_dt"], venue, 1 if locked else 0, slot["id"], r["id"]))
        if moved:
            changed.append(r)
    return changed


def unassign(reg_ids):
    for rid in reg_ids:
        ex("UPDATE registrations SET slot_id=NULL, slot_start=NULL, slot_end=NULL, slot_venue=NULL, slot_locked=0 WHERE id=?", (rid,))


def save_slot(slot, start, end, venue, capacity, label=None):
    """Edit a slot; the people in it keep it and get the new times. Returns the registrations to notify."""
    ex("UPDATE event_slots SET start_dt=?, end_dt=?, venue=?, capacity=?, label=? WHERE id=?",
       (core.iso(start), core.iso(end), (venue or "").strip()[:80] or None, max(1, int(capacity)),
        (label or "").strip()[:40] or None, slot["id"]))
    fresh = q("SELECT * FROM event_slots WHERE id=?", (slot["id"],), one=True)
    venue_ = fresh["venue"] or scalar("SELECT venue FROM events WHERE id=?", (fresh["event_id"],))
    regs = q("SELECT * FROM registrations WHERE slot_id=?", (slot["id"],))
    ex("UPDATE registrations SET slot_start=?, slot_end=?, slot_venue=? WHERE slot_id=?",
       (fresh["start_dt"], fresh["end_dt"], venue_, slot["id"]))
    changed = (slot["start_dt"][:16], slot["end_dt"][:16], slot["venue"]) != (fresh["start_dt"][:16], fresh["end_dt"][:16], fresh["venue"])
    return [r for r in regs if r["status"] == "confirmed"] if changed else []


def delete_slot(slot):
    """Remove a slot. Its people lose their slot (to be allocated again). Returns them."""
    regs = q("SELECT * FROM registrations WHERE slot_id=?", (slot["id"],))
    unassign([r["id"] for r in regs])
    ex("DELETE FROM event_slots WHERE id=?", (slot["id"],))
    return [r for r in regs if r["status"] == "confirmed"]


def generate(event_ids, start, minutes, gap, count, capacity, venue=None, replace=False):
    """Make `count` back-to-back slots of `minutes` (with `gap` between) for each event. With replace, an event's
    existing slots are removed first (their people are un-allocated). Returns (slots made, people un-allocated)."""
    made, freed = 0, []
    for eid in event_ids:
        if replace:
            for s in slots_of(eid):
                freed += delete_slot(s)
        t = start
        for i in range(count):
            add_slot(eid, t, t + timedelta(minutes=minutes), venue, capacity)
            t += timedelta(minutes=minutes + gap)
            made += 1
    return made, freed


def backfill():
    """Run at start-up: events from before time slots existed get their slot(s), and their people are put in them.
    Slots an older version of EventFlow gave people (time + room) become real slots. Safe to run again."""
    ex("""UPDATE events SET status='completed' WHERE status IN ('open','closed')
          AND parent_id IN (SELECT id FROM events WHERE status='completed')""")
    events = q("SELECT * FROM events WHERE kind!='fest' AND is_removed=0 AND id NOT IN (SELECT event_id FROM event_slots)")
    for e in events:
        legacy = q("""SELECT slot_start, slot_end, slot_venue, COUNT(*) n FROM registrations WHERE event_id=? AND slot_id IS NULL
                      AND slot_start IS NOT NULL AND slot_end IS NOT NULL AND status!='cancelled'
                      GROUP BY slot_start, slot_end, slot_venue ORDER BY slot_start, slot_venue""", (e["id"],))
        if legacy:
            for row in legacy:
                sid = add_slot(e["id"], row["slot_start"], row["slot_end"], row["slot_venue"], max(row["n"], 1))
                ex("""UPDATE registrations SET slot_id=? WHERE event_id=? AND slot_id IS NULL AND slot_start=? AND slot_end=?
                      AND COALESCE(slot_venue,'')=?""", (sid, e["id"], row["slot_start"], row["slot_end"], row["slot_venue"] or ""))
        ensure_default_slot(e)
    # events with a single slot: everyone confirmed is in it
    for row in q("""SELECT s.* FROM event_slots s WHERE (SELECT COUNT(*) FROM event_slots x WHERE x.event_id=s.event_id)=1
                    AND EXISTS (SELECT 1 FROM registrations r WHERE r.event_id=s.event_id AND r.status='confirmed' AND r.slot_id IS NULL)"""):
        assign(q("SELECT * FROM registrations WHERE event_id=? AND status='confirmed' AND slot_id IS NULL", (row["event_id"],)), row)


# ====================================================================== the flow (order of the event day)
def food_scope(scope_id):
    """Does this event day serve food at all (any event in it plans meals)?"""
    return bool(scalar("SELECT 1 FROM events WHERE (id=? OR parent_id=?) AND is_removed=0 AND meals_count>0", (scope_id, scope_id)))


def flow(scope_id, events=None):
    """Ordered items of the event day after entry: [{key, kind, event_id, id, title, start, end, venue}]."""
    events = events if events is not None else scope_events(scope_id)
    by_id = {e["id"]: e for e in events}
    rows = q("SELECT * FROM flow_items WHERE scope_id=? ORDER BY position, id", (scope_id,))
    out, seen = [], set()
    for r in rows:
        if r["kind"] == "event":
            ev = by_id.get(r["event_id"])
            if not ev or ev["id"] in seen:
                continue
            seen.add(ev["id"])
            out.append(_ev_item(ev, r["id"]))
        else:
            out.append({"key": f"food-{r['id']}", "kind": "food", "id": r["id"], "event_id": None,
                        "title": r["title"] or "Meal", "start": r["start_dt"], "end": r["end_dt"], "venue": r["venue"]})
    missing = [e for e in events if e["id"] not in seen]
    if not rows:
        out = [_ev_item(e, None) for e in missing]
        if food_scope(scope_id):
            out.insert(min(1, len(out)), {"key": "food", "kind": "food", "id": None, "event_id": None, "title": "Meal",
                                          "start": None, "end": None, "venue": None})
    else:
        out += [_ev_item(e, None) for e in missing]       # events added after the flow was saved go last
    return out


def _ev_item(ev, row_id):
    return {"key": f"ev-{ev['id']}", "kind": "event", "id": row_id, "event_id": ev["id"], "title": ev["title"],
            "start": ev["start_dt"], "end": ev["end_dt"], "venue": ev["venue"], "status": ev["status"]}


def save_flow(scope_id, items):
    """items: ordered [{"key": "ev-<id>" | "food-<id>" | "food" | "new-…", "title", "start", "end", "venue"}]. Meals keep
    their ids (so meals already served stay served); meals left out are removed."""
    events = {e["id"] for e in scope_events(scope_id)}
    existing_food = {r["id"]: r for r in q("SELECT * FROM flow_items WHERE scope_id=? AND kind='food'", (scope_id,))}
    keep_food, pos = set(), 0
    ex("DELETE FROM flow_items WHERE scope_id=? AND kind='event'", (scope_id,))
    for it in items:
        key = str(it.get("key") or "")
        if key.startswith("ev-") and key[3:].isdigit() and int(key[3:]) in events:
            ex("INSERT INTO flow_items (scope_id, kind, event_id, position) VALUES (?, 'event', ?, ?)", (scope_id, int(key[3:]), pos))
        elif key.startswith("food") or key.startswith("new"):
            title = (it.get("title") or "Meal").strip()[:60] or "Meal"
            s, e = core.parse_dt(it.get("start")), core.parse_dt(it.get("end"))
            if s and e and e <= s:
                e = None
            venue = (it.get("venue") or "").strip()[:80] or None
            fid = int(key[5:]) if key.startswith("food-") and key[5:].isdigit() else None
            if fid in existing_food:
                ex("UPDATE flow_items SET title=?, start_dt=?, end_dt=?, venue=?, position=? WHERE id=?",
                   (title, core.iso(s), core.iso(e), venue, pos, fid))
                keep_food.add(fid)
            else:
                fid = ex("INSERT INTO flow_items (scope_id, kind, title, start_dt, end_dt, venue, position) VALUES (?, 'food', ?, ?, ?, ?, ?)",
                         (scope_id, title, core.iso(s), core.iso(e), venue, pos))
                keep_food.add(fid)
                if key == "food":                       # the default meal becomes a saved one: keep who already ate
                    ex("UPDATE task_marks SET task_key=? WHERE scope_id=? AND task_key='food'", (f"food-{fid}", scope_id))
        else:
            continue
        pos += 1
    for fid in existing_food:
        if fid not in keep_food:
            ex("DELETE FROM flow_items WHERE id=?", (fid,))
            ex("DELETE FROM task_marks WHERE scope_id=? AND task_key=?", (scope_id, f"food-{fid}"))


def food_key(scope_id, key):
    return hashlib.sha1(f"{scope_id}|{key}".encode()).hexdigest()[:6].upper()


# ====================================================================== allocation
def _units(event_id):
    regs = q("""SELECT r.*, u.name FROM registrations r JOIN users u ON u.id=r.user_id
                WHERE r.event_id=? AND r.status='confirmed' ORDER BY r.created_at, r.id""", (event_id,))
    units = {}
    for r in regs:
        key = ("team", event_id, r["team_name"].strip().lower()) if r["team_name"] and r["team_name"].strip() else ("solo", event_id, r["id"])
        u = units.setdefault(key, {"key": key, "event_id": event_id, "regs": [], "created": r["created_at"],
                                   "label": f"Team {r['team_name'].strip()}" if key[0] == "team" else r["name"]})
        u["regs"].append(r)
    for u in units.values():
        u["users"] = sorted({r["user_id"] for r in u["regs"]})
        u["size"] = len(u["regs"])
        u["locked"] = any(r["slot_locked"] for r in u["regs"])
        cur = [r["slot_id"] for r in u["regs"] if r["slot_id"]]
        u["current"] = max(set(cur), key=cur.count) if cur else None   # where most of the team already is
        u["whole"] = bool(cur) and len(cur) == len(u["regs"]) and len(set(cur)) == 1
    return list(units.values())


def _overlap(a, b):
    return a[0] < b[1] and b[0] < a[1]


def plan(scope_id, mode="new", keep_locked=True, event_ids=None):
    """Work out slots without saving anything.
    mode "new": everyone who has a slot keeps it; only people without one are placed.
    mode "all": everyone is placed again from scratch (exceptions kept when keep_locked).
    event_ids: only (re)place people in these events; everyone else in the event day keeps their slot and is worked around."""
    events = scope_events(scope_id)
    targets = {e["id"] for e in events} if not event_ids else set(event_ids)
    ev_ids = [e["id"] for e in events]
    slots = slots_for(ev_ids)
    slot_by_id = {s["id"]: s for lst in slots.values() for s in lst}
    order = {it["event_id"]: i for i, it in enumerate(flow(scope_id)) if it["kind"] == "event"}
    times = {sid: (core.parse_dt(s["start_dt"]), core.parse_dt(s["end_dt"])) for sid, s in slot_by_id.items()}
    ev_by_id = {e["id"]: e for e in events}
    single = {eid: len(lst) == 1 for eid, lst in slots.items()}
    # a slot doesn't block other events when it's long (an all-day hackathon) or when it's the event's only slot and
    # covers the whole event (the event isn't split into time slots: people come any time it's on)
    is_long = {sid: long_slot(s) or (single.get(s["event_id"]) and (s["start_dt"] or "")[:16] <= (ev_by_id[s["event_id"]]["start_dt"] or "")[:16]
                                     and (s["end_dt"] or "")[:16] >= (ev_by_id[s["event_id"]]["end_dt"] or "")[:16])
               for sid, s in slot_by_id.items()}
    units = [u for e in events for u in _units(e["id"])]
    users = sorted({uid for u in units for uid in u["users"]})

    # what each person is busy with outside this plan: busy times, and their slots at other event days
    busy = {uid: [] for uid in users}
    if users:
        c, a = in_clause(users)
        for b in q(f"""SELECT user_id, start_dt, end_dt FROM busy_times WHERE user_id IN {c}
                       AND (event_id IS NULL OR event_id=? OR event_id IN (SELECT id FROM events WHERE parent_id=?))""",
                   a + [scope_id, scope_id]):
            s, e = core.parse_dt(b["start_dt"]), core.parse_dt(b["end_dt"])
            if s and e:
                busy[b["user_id"]].append((s, e))
        for r in q(f"""SELECT r.user_id, r.slot_start, r.slot_end FROM registrations r JOIN events e ON e.id=r.event_id
                       WHERE r.user_id IN {c} AND r.status='confirmed' AND r.slot_start IS NOT NULL
                       AND e.id!=? AND COALESCE(e.parent_id, 0)!=?""", a + [scope_id, scope_id]):
            s, e = core.parse_dt(r["slot_start"]), core.parse_dt(r["slot_end"])
            if s and e and e - s <= LONG_SLOT:
                busy[r["user_id"]].append((s, e))

    held = {}                      # user -> {event_id: slot_id}
    load = {sid: 0 for sid in slot_by_id}
    placed = {}                    # unit key -> slot id
    members = {sid: [] for sid in slot_by_id}

    def put(u, sid):
        placed[u["key"]] = sid
        load[sid] += u["size"]
        members[sid].append(u)
        for uid in u["users"]:
            held.setdefault(uid, {})[u["event_id"]] = sid

    def take(u):
        sid = placed.pop(u["key"])
        load[sid] -= u["size"]
        members[sid].remove(u)
        for uid in u["users"]:
            held.get(uid, {}).pop(u["event_id"], None)
        return sid

    def problem(u, sid, ignore=None):
        """Why unit u can't go in slot sid: None (it can), 'full', 'clash', 'busy' or 'order'."""
        if load[sid] - (ignore["size"] if ignore and placed.get(ignore["key"]) == sid else 0) + u["size"] > slot_by_id[sid]["capacity"]:
            return "full"
        if single.get(u["event_id"]):
            return None                          # one slot only: nothing to choose, the other events work around it
        s, e = times[sid]
        if not s or not e:
            return "clash"
        for uid in u["users"]:
            for oev, osid in held.get(uid, {}).items():
                if oev == u["event_id"] or (ignore and oev == ignore["event_id"] and uid in ignore["users"]):
                    continue
                os_, oe = times[osid]
                if not is_long[sid] and not is_long[osid] and _overlap((s, e), (os_, oe)):
                    return "clash"
                a_, b_ = order.get(oev, 999), order.get(u["event_id"], 999)
                if a_ < b_ and os_ > s:          # the other event comes first in the flow: it must not start later
                    return "order"
                if b_ < a_ and s > os_:
                    return "order"
            if not is_long[sid] and any(_overlap((s, e), bz) for bz in busy.get(uid, [])):
                return "busy"
        return None

    # 1. who keeps their slot
    fixed, todo = [], []
    for u in units:
        valid = u["current"] in slot_by_id and slot_by_id[u["current"]]["event_id"] == u["event_id"]
        if u["event_id"] not in targets:
            if valid:
                fixed.append(u)                  # another event of the day: kept, and worked around
        elif valid and ((mode == "new" and u["current"]) or (keep_locked and u["locked"])):
            fixed.append(u)
        else:
            todo.append(u)
    for u in fixed:
        put(u, u["current"])
    kept = {u["key"] for u in fixed}

    # 2. event by event in the event-day order; within an event, slot by slot in time order (so slot 1 fills first),
    #    giving each slot to the people who can't wait: the ones whose later events need an early slot (earliest
    #    deadline first), then the ones with the fewest possible slots, bigger teams, earlier registrations.
    user_events = {}
    for u in units:
        for uid in u["users"]:
            user_events.setdefault(uid, set()).add(u["event_id"])

    def look_ok(u, sid):
        """Placing u in sid still leaves each member a possible slot in their other, not yet placed, events."""
        s0, e0 = times[sid]
        ou = order.get(u["event_id"], 999)
        for uid in u["users"]:
            for other in user_events.get(uid, ()):
                if other == u["event_id"] or other in held.get(uid, {}) or not slots.get(other):
                    continue
                oo = order.get(other, 999)
                fine = False
                for o in slots[other]:
                    os_, oe = times[o["id"]]
                    if not os_ or not oe or (oo > ou and os_ < s0) or (oo < ou and os_ > s0):
                        continue
                    if not is_long[sid] and not is_long[o["id"]] and _overlap((s0, e0), (os_, oe)):
                        continue
                    fine = True
                    break
                if not fine:
                    return False
        return True

    unplaced = []
    pending = {}
    for u in todo:
        pending.setdefault(u["event_id"], []).append(u)
    # events with a single slot first (nothing to choose), then the rest in the event-day order
    for e in sorted([x for x in events if x["id"] in pending], key=lambda x: (not single.get(x["id"]), order.get(x["id"], 999), x["start_dt"], x["id"])):
        options = slots.get(e["id"], [])
        if not options:
            unplaced += [(u, "This event has no time slots yet.") for u in pending[e["id"]]]
            continue
        info = {}
        for u in pending[e["id"]]:
            allowed = [i for i, sl in enumerate(options) if problem(u, sl["id"]) in (None, "full") and look_ok(u, sl["id"])]
            info[u["key"]] = (max(allowed) if allowed else len(options), len(allowed))
        remaining = list(pending[e["id"]])
        for sl in options:
            cands = [u for u in remaining if not problem(u, sl["id"]) and look_ok(u, sl["id"])]
            cands.sort(key=lambda u: (info[u["key"]][0], info[u["key"]][1], -u["size"], str(u["created"])))
            for u in cands:
                if not problem(u, sl["id"]):            # the slot may have filled up meanwhile
                    put(u, sl["id"])
                    remaining.remove(u)
        for u in remaining:                              # no slot keeps every later event possible: take any that fits
            chosen, reasons = None, {}
            for sl in options:
                why = problem(u, sl["id"])
                if not why:
                    chosen = sl["id"]
                    break
                reasons[why] = reasons.get(why, 0) + 1
            if not chosen and reasons.get("full"):
                # make room: move someone who can just as well take another slot of this event
                for sl in options:
                    if problem(u, sl["id"]) != "full":
                        continue
                    for v in sorted(members[sl["id"]], key=lambda x: x["size"]):
                        if v["key"] in kept or v["size"] < u["size"] - (sl["capacity"] - load[sl["id"]]):
                            continue
                        alt = next((o["id"] for o in options if o["id"] != sl["id"] and not problem(v, o["id"], ignore=v)), None)
                        if not alt:
                            continue
                        take(v)
                        if problem(u, sl["id"]):
                            put(v, sl["id"])
                            continue
                        put(v, alt)
                        chosen = sl["id"]
                        break
                    if chosen:
                        break
            if chosen:
                put(u, chosen)
                continue
            if max(o["capacity"] for o in options) < u["size"]:
                why = f"The team has {u['size']} members but the biggest slot holds {max(o['capacity'] for o in options)}."
            elif reasons.get("full", 0) == len(options):
                why = "Every slot is full. Add a slot or raise the members per slot."
            elif reasons.get("busy"):
                why = "Busy (times they marked) during every slot with room."
            elif reasons.get("order"):
                why = "No slot with room keeps their event-day order. Change the order or add a slot."
            else:
                why = "Clashes with their other events in every slot with room."
            unplaced.append((u, why))

    by_event = []
    for e in [x for x in events if x["id"] in targets]:
        rows = []
        for s in slots.get(e["id"], []):
            rows.append({"slot": s, "units": sorted(members[s["id"]], key=lambda x: x["label"].lower()), "load": load[s["id"]],
                         "capacity": s["capacity"], "name": slot_name(s, slots[e["id"]])})
        by_event.append({"event": e, "slots": rows, "unplaced": [(u, w) for u, w in unplaced if u["event_id"] == e["id"]],
                         "people": sum(r["load"] for r in rows)})
    moves = [u for u in units if u["key"] in placed and u["key"] not in kept and
             any(r["slot_id"] != placed[u["key"]] for r in u["regs"])]
    mine = [u for u in units if u["event_id"] in targets]
    return {"scope_id": scope_id, "mode": mode, "events": by_event, "placed": placed, "units": mine, "slots": slot_by_id,
            "unplaced": unplaced, "moves": moves, "kept": len([u for u in mine if u["key"] in kept]),
            "people": sum(u["size"] for u in mine if u["key"] in placed),
            "total": sum(u["size"] for u in mine)}


def apply(result):
    """Save a plan. Returns [(registration, slot)] for people whose slot changed (to tell them)."""
    changed = []
    for u in result["units"]:
        sid = result["placed"].get(u["key"])
        if not sid:
            continue
        slot = result["slots"][sid]
        locked = u["locked"] and all(r["slot_id"] == sid for r in u["regs"])
        todo = [r for r in u["regs"] if r["slot_id"] != sid or bool(r["slot_locked"]) != locked
                or (r["slot_start"] or "")[:16] != (slot["start_dt"] or "")[:16]]
        for r in assign(todo, slot, locked=locked):
            changed.append((r, slot))
    return changed


def place_new(scope_id, notify=True, actor_id=None):
    """Give a slot to everyone in this event day who hasn't got one, without moving anybody. Used whenever tickets are
    confirmed. Returns the plan."""
    result = plan(scope_id, mode="new")
    changed = apply(result)
    if notify:
        tell(changed, actor_id)
    return result


def tell(changed, actor_id=None, why=None):
    """Notify people about their (new) slot. Events with a single slot (the whole event) don't need a message."""
    from flask import url_for
    counts = {}
    for r, slot in changed:
        if slot["event_id"] not in counts:
            counts[slot["event_id"]] = scalar("SELECT COUNT(*) FROM event_slots WHERE event_id=?", (slot["event_id"],))
        if counts[slot["event_id"]] < 2 and not why:
            continue
        ev = q("SELECT title, venue FROM events WHERE id=?", (slot["event_id"],), one=True)
        code = scalar("SELECT pass_code FROM registrations WHERE id=?", (r["id"],))
        text = (why + " " if why else "") + f"Your slot for {ev['title']}: {slot_text(slot, venue=ev['venue'])}."
        core.notify(r["user_id"], "slot", text, url_for("events.ticket", code=code), actor_id)


def board(scope_id, event_ids=None):
    """What the Studio shows: each event, its slots with the people in them, and who has no slot yet."""
    events = [e for e in scope_events(scope_id) if not event_ids or e["id"] in event_ids]
    ids = [e["id"] for e in events]
    slots = slots_for(ids)
    out, unplaced_total, people_total = [], 0, 0
    for e in events:
        units = _units(e["id"])
        rows = []
        for s in slots.get(e["id"], []):
            us = [u for u in units if u["current"] == s["id"]]
            rows.append({"slot": s, "units": sorted(us, key=lambda x: x["label"].lower()), "load": sum(len([r for r in u["regs"] if r["slot_id"] == s["id"]]) for u in us),
                         "name": slot_name(s, slots[e["id"]]), "key": slot_key(s),
                         "checked": sum(1 for u in us for r in u["regs"] if r["completed_at"])})
        loose = [u for u in units if not u["current"] or u["current"] not in {s["id"] for s in slots.get(e["id"], [])}]
        unplaced_total += sum(u["size"] for u in loose)
        people_total += sum(u["size"] for u in units)
        out.append({"event": e, "slots": rows, "loose": loose, "people": sum(u["size"] for u in units),
                    "capacity": sum(s["capacity"] for s in slots.get(e["id"], []))})
    return {"events": out, "unplaced": unplaced_total, "people": people_total,
            "slot_count": sum(len(v) for v in slots.values())}


def move_unit(event_id, reg, slot):
    """Organiser exception: put a person (and their whole team) in a slot, and lock it.
    Returns (registrations whose slot changed, everyone in the unit, warnings)."""
    if reg["team_name"] and reg["team_name"].strip():
        regs = q("SELECT * FROM registrations WHERE event_id=? AND status='confirmed' AND lower(team_name)=lower(?)",
                 (event_id, reg["team_name"].strip()))
    else:
        regs = [reg]
    warn = []
    load = scalar("SELECT COUNT(*) FROM registrations WHERE slot_id=? AND status='confirmed'", (slot["id"],)) or 0
    extra = len([r for r in regs if r["slot_id"] != slot["id"]])
    if load + extra > slot["capacity"]:
        warn.append(f"the slot now has {load + extra} members for {slot['capacity']} places")
    s, e = core.parse_dt(slot["start_dt"]), core.parse_dt(slot["end_dt"])
    if not long_slot(slot):
        users = [r["user_id"] for r in regs]
        c, a = in_clause(users)
        for o in q(f"""SELECT r.user_id, r.slot_start, r.slot_end, ev.title, u.name FROM registrations r
                       JOIN events ev ON ev.id=r.event_id JOIN users u ON u.id=r.user_id
                       WHERE r.user_id IN {c} AND r.status='confirmed' AND r.event_id!=? AND r.slot_start IS NOT NULL""", a + [event_id]):
            os_, oe = core.parse_dt(o["slot_start"]), core.parse_dt(o["slot_end"])
            if os_ and oe and oe - os_ <= LONG_SLOT and _overlap((s, e), (os_, oe)):
                warn.append(f"{o['name']} also has {o['title']} at {_t(o['slot_start'])}")
    moved = assign(regs, slot, locked=True)
    for r in regs:                                   # lock even those already there
        ex("UPDATE registrations SET slot_locked=1 WHERE id=?", (r["id"],))
    return moved, regs, warn
