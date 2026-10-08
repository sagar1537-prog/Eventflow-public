"""College studio: the admin dashboard for a college's events (and for event leads it has invited)."""
import csv
import io
import json
from datetime import datetime, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash, g, abort, jsonify, Response

import ai_agent
import core
import journey
import live
import scheduler
import fest as festlib
from db import q, ex, scalar, in_clause
from blueprints.events import confirm, promote_waitlist

bp = Blueprint("studio", __name__, url_prefix="/studio")


@bp.before_request
def guard():
    if not g.user:
        return redirect(url_for("auth.login", next=request.path))
    if not core.is_studio_user(g.user):
        abort(403)
    return None


def staff_role(eid):
    """owner | dev | lead | volunteer | None"""
    u = g.user
    if u["role"] == "dev":
        return "dev"
    if scalar("SELECT 1 FROM events WHERE id=? AND college_id=?", (eid, u["id"])):
        return "owner"
    role = scalar("SELECT role FROM event_staff WHERE event_id=? AND user_id=?", (eid, u["id"]))
    parent = scalar("SELECT parent_id FROM events WHERE id=?", (eid,))
    if parent:  # a lead or volunteer on the fest has the same role on every event in it
        prole = scalar("SELECT role FROM event_staff WHERE event_id=? AND user_id=?", (parent, u["id"]))
        if prole == "lead" or (prole and not role):
            role = prole
    return role


def need(eid, *roles):
    r = staff_role(eid)
    if not r or (roles and r not in roles and r not in ("owner", "dev")):
        abort(403)
    return r


def staff_any_lead():
    """College, dev, or a student who is a lead (not just a volunteer) somewhere."""
    if g.user["role"] in ("college", "dev"):
        return True
    return bool(scalar("SELECT 1 FROM event_staff WHERE user_id=? AND role='lead'", (g.user["id"],)))


def lead_event_ids():
    if g.user["role"] in ("college", "dev"):
        return core.managed_event_ids(g.user)
    return [r[0] for r in q("SELECT event_id FROM event_staff WHERE user_id=? AND role='lead'", (g.user["id"],))]


def college_only():
    if g.user["role"] not in ("college", "dev"):
        abort(403)


@bp.context_processor
def studio_ctx():
    ids = core.managed_event_ids(g.user) if g.get("user") else []
    c, a = in_clause(ids)
    side = q(f"""SELECT e.id, e.title, e.category, e.status, e.start_dt, f.title fest FROM events e LEFT JOIN events f ON f.id=e.parent_id
                 WHERE e.id IN {c} AND e.is_removed=0 ORDER BY COALESCE(f.start_dt, e.start_dt) DESC, COALESCE(e.parent_id, e.id), e.parent_id IS NOT NULL, e.position""", a)
    pending = scalar(f"SELECT COUNT(*) FROM payments WHERE status='submitted' AND event_id IN {c}", a) if ids else 0
    unanswered = 0
    if ids and g.get("user") and staff_any_lead():
        unanswered = scalar(f"SELECT COUNT(*) FROM chat_logs WHERE answered=0 AND resolved=0 AND event_id IN {c}", a)
    return {"side_events": side, "pending_payments": pending, "unanswered": unanswered,
            "can_assistant": bool(g.get("user") and staff_any_lead()),
            "is_owner": g.get("user") and g.user["role"] in ("college", "dev")}


def _events(ids):
    c, a = in_clause(ids)
    return q(f"""SELECT e.*,
        (SELECT COUNT(*) FROM registrations r WHERE r.event_id=e.id AND r.status='confirmed') reg_count,
        (SELECT COUNT(*) FROM registrations r WHERE r.event_id=e.id AND r.status='confirmed' AND r.attended=1) att_count,
        (SELECT COUNT(*) FROM payments p WHERE p.event_id=e.id AND p.status='submitted') review_count,
        (SELECT COALESCE(SUM(amount),0) FROM payments p WHERE p.event_id=e.id AND p.status='paid') revenue,
        (SELECT COALESCE(SUM(platform_fee),0) FROM payments p WHERE p.event_id=e.id AND p.status='paid') fees
        FROM events e WHERE e.id IN {c} AND e.is_removed=0
        ORDER BY CASE e.status WHEN 'completed' THEN 1 ELSE 0 END, e.start_dt""", a)


# ================================================================== overview
@bp.route("/")
def overview():
    me = g.user
    ids = core.managed_event_ids(me)
    events = _events(ids)
    c, a = in_clause(ids)
    days = [(datetime.now().date() - timedelta(days=i)) for i in range(13, -1, -1)]
    raw = {r[0]: r[1] for r in q(f"""SELECT date(created_at), COUNT(*) FROM registrations WHERE event_id IN {c}
                                    AND status='confirmed' AND date(created_at) >= ? GROUP BY 1""", a + [days[0].isoformat()])}
    revenue_raw = {r[0]: r[1] for r in q(f"""SELECT date(created_at), SUM(amount) FROM payments WHERE event_id IN {c}
                                           AND status='paid' AND date(created_at) >= ? GROUP BY 1""", a + [days[0].isoformat()])}
    gross = sum(e["revenue"] for e in events)
    fees = sum(e["fees"] for e in events)
    completed_reg = sum(e["reg_count"] for e in events if e["status"] == "completed")
    completed_att = sum(e["att_count"] for e in events if e["status"] == "completed")
    followers = scalar("SELECT COUNT(*) FROM follows WHERE college_id=?", (me["id"],)) if me["role"] == "college" else None
    new_followers = scalar("SELECT COUNT(*) FROM follows WHERE college_id=? AND created_at >= ?",
                           (me["id"], (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"))) if me["role"] == "college" else 0
    listed, by_id = [], {}
    for ev in events:  # fests show once, with their events' numbers added up
        d = dict(ev)
        d["sub_count"] = 0
        by_id[d["id"]] = d
        if not d["parent_id"]:
            listed.append(d)
    for ev in events:
        par = by_id.get(ev["parent_id"]) if ev["parent_id"] else None
        if par:
            for k in ("reg_count", "att_count", "review_count", "revenue"):
                par[k] += ev[k]
            par["sub_count"] += 1
    orphan_parents = {ev["parent_id"] for ev in events if ev["parent_id"] and ev["parent_id"] not in by_id}
    listed += [dict(ev, sub_count=0) for ev in events if ev["parent_id"] in orphan_parents]  # leads of single fest events
    kpis = {"events": sum(1 for e in events if e["kind"] != "fest"), "live": sum(1 for e in events if e["status"] == "open"),
            "registrations": sum(e["reg_count"] for e in events), "checked_in": sum(e["att_count"] for e in events),
            "gross": gross, "net": gross - fees, "fees": fees, "followers": followers, "new_followers": new_followers,
            "show_rate": (completed_att / completed_reg) if completed_reg else None,
            "review": sum(e["review_count"] for e in events)}
    charts = {"labels": [d.strftime("%d %b") for d in days], "regs": [raw.get(d.isoformat(), 0) for d in days],
              "revenue": [revenue_raw.get(d.isoformat(), 0) for d in days],
              "events": [e["title"] for e in events if e["status"] != "draft" and e["kind"] != "fest"][:8],
              "ev_reg": [e["reg_count"] for e in events if e["status"] != "draft" and e["kind"] != "fest"][:8],
              "ev_att": [e["att_count"] for e in events if e["status"] != "draft" and e["kind"] != "fest"][:8]}
    rows = q(f"""SELECT p.*, u.name, u.username, u.avatar, e.title FROM payments p JOIN users u ON u.id=p.user_id
                 JOIN events e ON e.id=p.event_id
                 WHERE p.event_id IN {c} AND p.status IN ('paid','submitted') AND p.method!='free'
                 ORDER BY p.created_at DESC, p.id DESC LIMIT 120""", a)
    recent = _orders(rows)[:8]
    return render_template("studio/overview.html", events=listed, kpis=kpis, charts=charts, recent=recent, active="overview")


def _orders(rows):
    """Payments grouped into orders: a fest order (several events paid together) is one line, not one per event."""
    orders = {}
    for p in rows:
        key = (p["bundle"] or p["receipt_no"], p["status"])
        o = orders.setdefault(key, {"head": p, "events": [], "total": 0, "status": p["status"]})
        if p["title"] not in o["events"]:
            o["events"].insert(0, p["title"])         # rows come newest first; list the order's events in order
        o["total"] += p["amount"]
    return list(orders.values())


# ================================================================== events CRUD
def _form(owner=None, existing=None):
    f = request.form
    data = {
        "title": (f.get("title") or "").strip()[:120],
        "category": f.get("category") if f.get("category") in core.CATEGORIES else "Other",
        "tagline": (f.get("tagline") or "").strip()[:160],
        "description": (f.get("description") or "").strip()[:5000],
        "venue": (f.get("venue") or "").strip()[:120],
        "venue_details": (f.get("venue_details") or "").strip()[:300],
        "city": (f.get("city") or "").strip()[:60],
        "map_url": (f.get("map_url") or "").strip()[:500],
        "start_dt": f.get("start_dt") or "", "end_dt": f.get("end_dt") or "",
        "reg_deadline": f.get("reg_deadline") or None,
        "status": f.get("status") if f.get("status") in core.EVENT_STATUSES else "draft",
    }
    errors = []
    data.update(capacity=1, fee=0, team_size=1, food_cost_per_head=0.0, meals_count=0, food_buffer_pct=0.0)
    try:
        data["capacity"] = max(1, int(f.get("capacity") or 1))
        data["fee"] = max(0, int(float(f.get("fee") or 0)))
        data["team_size"] = min(10, max(1, int(f.get("team_size") or 1)))
        data["food_cost_per_head"] = max(0.0, float(f.get("food_cost_per_head") or 0))
        data["meals_count"] = max(0, int(f.get("meals_count") or 0))
        data["food_buffer_pct"] = max(0.0, float(f.get("food_buffer_pct") or 0))
    except ValueError:
        errors.append("Seats, fee and food numbers must be numbers.")
    if not data["title"]:
        errors.append("Give the event a name.")
    if not data["venue"]:
        errors.append("Add a venue.")
    if data["map_url"] and not data["map_url"].startswith(("http://", "https://")):
        errors.append("The map link must start with https://")
    s, e = core.parse_dt(data["start_dt"]), core.parse_dt(data["end_dt"])
    if not s or not e:
        errors.append("Add start and end date and time.")
    elif e <= s:
        errors.append("The event must end after it starts.")
    d = core.parse_dt(data["reg_deadline"])
    if d and s and d > e:
        errors.append("Registration should close before the event ends.")
    if data["status"] == "open" and core.setting("require_verified_to_publish") == "1" \
            and g.user["role"] == "college" and g.user["verification"] != "verified":
        errors.append("Your college must be verified before events can open for registration. Save it as a draft for now.")
    owner = owner or g.user
    if existing is not None and existing["parent_id"]:  # an event inside a fest
        tracks = {t["id"] for t in festlib.tracks(existing["parent_id"])}
        try:
            tid = int(f.get("track_id") or 0)
        except ValueError:
            tid = 0
        data["track_id"] = tid if tid in tracks else existing["track_id"]
        data["fee_type"] = "team" if f.get("fee_type") == "team" else "person"
        data["label"] = (f.get("label") or "").strip()[:40] or None
    if existing is not None and existing["kind"] == "fest":
        data.update(fee=existing["fee"], capacity=existing["capacity"], team_size=1)
    elif existing is None and f.get("kind") == "fest":
        data.update(fee=0, capacity=100, team_size=1)
    if data["fee"] > 0 and owner["role"] == "college" and not owner["upi_id"]:
        errors.append("The college needs a UPI ID (Settings → Payments) before this event can be paid."
                      if owner["id"] != g.user["id"] else "Add your UPI ID in Settings → Payments before creating a paid event.")
    return data, errors


def _banner(data, old=None):
    file = request.files.get("banner")
    if file and file.filename:
        up = core.save_upload(file, allowed=("image",), max_mb=10)
        if old:
            core.delete_upload(old)
        data["banner"] = up["path"]
    elif request.form.get("remove_banner") and old:
        core.delete_upload(old)
        data["banner"] = None


# ---------------------------------------------------------------- the create / edit wizard
def _json_field(name, default):
    raw = request.form.get(name) or ""
    try:
        val = json.loads(raw) if raw.strip() else default
    except ValueError:
        return default
    return val if isinstance(val, type(default)) else default


def _int(v, lo, hi, default):
    try:
        return min(hi, max(lo, int(float(v))))
    except (TypeError, ValueError):
        return default


def _fest_plan(errors):
    """The events inside a new fest, from the wizard: [{name, emoji, pricing, pass_fee, events: [...]}]."""
    data = _json_field("fest_json", {})
    tracks, keys, titles = [], set(), set()
    for t in (data.get("tracks") or [])[:12]:
        if not isinstance(t, dict):
            continue
        evs = []
        for ev in (t.get("events") or [])[:100]:
            if not isinstance(ev, dict):
                continue
            title = str(ev.get("title") or "").strip()[:120]
            if not title:
                continue                                    # empty rows are simply ignored
            if title.lower() in titles:
                errors.append(f"Two events are called “{title}”. Give each event its own name.")
                continue
            titles.add(title.lower())
            key = str(ev.get("key") or "")[:24]
            if not key or key in keys or key == "self":
                key = f"k{len(keys) + 1}"
            keys.add(key)
            team = _int(ev.get("team_size"), 1, 15, 1)
            evs.append({"key": key, "title": title,
                        "category": ev.get("category") if ev.get("category") in core.CATEGORIES else "Other",
                        "fee": _int(ev.get("fee"), 0, 10 ** 6, 0),
                        "fee_type": "team" if ev.get("fee_type") == "team" and team > 1 else "person",
                        "team_size": team, "capacity": _int(ev.get("capacity"), 1, 100000, 100),
                        "label": str(ev.get("label") or "").strip()[:40] or None,
                        "venue": str(ev.get("venue") or "").strip()[:120] or None})
        name = str(t.get("name") or "").strip()[:60]
        if not evs and not name:
            continue
        tracks.append({"name": name or "Events", "emoji": str(t.get("emoji") or "").strip()[:4] or "✨",
                       "blurb": str(t.get("blurb") or "").strip()[:200],
                       "pricing": "pass" if t.get("pricing") == "pass" else "event",
                       "pass_fee": _int(t.get("pass_fee"), 0, 10 ** 6, 0), "events": evs})
    if not any(t["events"] for t in tracks):
        errors.append("Add at least one event inside the fest.")
    return [t for t in tracks if t["events"] or t["name"]]


def _slot_plan(errors, event_keys):
    """Time slots from the wizard: rounds (shared times) and which events run in which, with members per slot."""
    data = _json_field("slots_json", {})
    rounds, seen = [], set()
    for i, r in enumerate((data.get("rounds") or [])[:80]):
        if not isinstance(r, dict):
            continue
        s, e = core.parse_dt(r.get("start")), core.parse_dt(r.get("end"))
        name = str(r.get("label") or "").strip()[:40] or f"Slot {i + 1}"
        if not s or not e:
            errors.append(f"{name} needs a start and an end time.")
            continue
        if e <= s:
            errors.append(f"{name} must end after it starts.")
            continue
        key = str(r.get("key") or "")[:24] or f"r{i}"
        if key in seen:
            key = f"r{i}"
        seen.add(key)
        rounds.append({"key": key, "label": str(r.get("label") or "").strip()[:40] or None, "start": s, "end": e,
                       "venue": str(r.get("venue") or "").strip()[:80] or None})
    raw = data.get("use") if isinstance(data.get("use"), dict) else {}
    use = {}
    for k in event_keys:
        u = raw.get(k) if isinstance(raw.get(k), dict) else {}
        picked = set(map(str, u.get("rounds") or []))
        use[k] = {"rounds": [r for r in rounds if r["key"] in picked], "capacity": _int(u.get("capacity"), 1, 100000, 20)}
    return rounds, use


def _flow_plan(errors):
    out = []
    for it in _json_field("flow_json", [])[:150]:
        if not isinstance(it, dict):
            continue
        if it.get("type") == "event":
            out.append({"type": "event", "ref": str(it.get("ref") or "")})
        elif it.get("type") == "food":
            title = str(it.get("title") or "").strip()[:60] or "Meal"
            s, e = core.parse_dt(it.get("start")), core.parse_dt(it.get("end"))
            if s and e and e <= s:
                errors.append(f"{title} must end after it starts.")
            out.append({"type": "food", "title": title, "start": core.iso(s), "end": core.iso(e),
                        "venue": str(it.get("venue") or "").strip()[:80] or None})
    return out


def _wizard(e, mode, **ctx):
    wiz = {"kind": request.form.get("kind") or ("fest" if e.get("kind") == "fest" else "event"),
           "fest_json": request.form.get("fest_json") or "", "slots_json": request.form.get("slots_json") or "",
           "flow_json": request.form.get("flow_json") or ""}
    return render_template("studio/event_form.html", e=e, mode=mode, wiz=wiz, active="new" if mode == "create" else ctx.pop("active", "new"),
                           **ctx)


@bp.route("/events/new", methods=["GET", "POST"])
def event_new():
    if g.user["role"] != "college":
        abort(403)
    if request.method == "GET":
        blank = {"category": "Technical", "status": "open", "capacity": 100, "fee": 0, "team_size": 1, "food_buffer_pct": 10,
                 "meals_count": 0, "food_cost_per_head": 100, "city": g.user["city"],
                 "kind": "fest" if request.args.get("kind") == "fest" else "event"}
        return _wizard(blank, "create")
    data, errors = _form()
    is_fest = request.form.get("kind") == "fest"
    tracks = _fest_plan(errors) if is_fest else []
    keys = [ev["key"] for t in tracks for ev in t["events"]] if is_fest else ["self"]
    _rounds, use = _slot_plan(errors, keys)
    flow_items = _flow_plan(errors)
    paid = any((t["pricing"] == "pass" and t["pass_fee"]) or (t["pricing"] == "event" and any(ev["fee"] for ev in t["events"]))
               for t in tracks)
    if is_fest and paid and not g.user["upi_id"]:
        errors.append("Add your UPI ID in Settings → Payments before adding paid events.")
    try:
        if not errors:
            _banner(data)
    except ValueError as err:
        errors.append(str(err))
    if errors:
        for msg in dict.fromkeys(errors):
            flash(msg, "error")
        data["kind"] = "fest" if is_fest else "event"
        return _wizard(data, "create")
    data["college_id"] = g.user["id"]
    if is_fest:
        data["kind"] = "fest"
    cols = ",".join(data)
    eid = ex(f"INSERT INTO events ({cols}) VALUES ({','.join('?' * len(data))})", tuple(data.values()))
    ids = {}
    if is_fest:
        fest_row = q("SELECT * FROM events WHERE id=?", (eid,), one=True)
        for pos, t in enumerate(tracks):
            tid = ex("INSERT INTO fest_tracks (fest_id, name, emoji, blurb, pricing, pass_fee, position) VALUES (?,?,?,?,?,?,?)",
                     (eid, t["name"], t["emoji"], t["blurb"], t["pricing"], t["pass_fee"], pos))
            for i, ev in enumerate(t["events"]):
                rs = use[ev["key"]]["rounds"]
                start = min(r["start"] for r in rs) if rs else core.parse_dt(data["start_dt"])
                end = max(r["end"] for r in rs) if rs else core.parse_dt(data["end_dt"])
                sid = festlib.add_sub(fest_row, tid, ev["title"], category=ev["category"],
                                      fee=ev["fee"] if t["pricing"] == "event" else 0, fee_type=ev["fee_type"],
                                      team_size=ev["team_size"], label=ev["label"], position=pos * 1000 + i,
                                      capacity=ev["capacity"], venue=ev["venue"], start_dt=core.iso(start), end_dt=core.iso(end))
                ids[ev["key"]] = sid
                for r in rs:
                    scheduler.add_slot(sid, r["start"], r["end"], r["venue"], use[ev["key"]]["capacity"], r["label"])
                scheduler.ensure_default_slot(q("SELECT * FROM events WHERE id=?", (sid,), one=True))
        festlib.sync(eid)
    else:
        ids["self"] = eid
        for r in use["self"]["rounds"]:
            scheduler.add_slot(eid, r["start"], r["end"], r["venue"], use["self"]["capacity"], r["label"])
        scheduler.ensure_default_slot(q("SELECT * FROM events WHERE id=?", (eid,), one=True))
    if flow_items:
        items = []
        for it in flow_items:
            if it["type"] == "event" and it["ref"] in ids:
                items.append({"key": f"ev-{ids[it['ref']]}"})
            elif it["type"] == "food":
                items.append(dict(it, key="new"))
        scheduler.save_flow(eid, items)
    if data["status"] == "open":
        fol = [r[0] for r in q("SELECT follower_id FROM follows WHERE college_id=?", (g.user["id"],))]
        core.notify_many(fol, "event", f"{g.user['name']} opened registrations for {data['title']}.",
                         url_for("events.detail", eid=eid), g.user["id"])
    core.audit("fest.create" if is_fest else "event.create", f"{data['title']} (#{eid})")
    if is_fest and not g.user["upi_id"]:
        flash("Add your UPI ID in Settings → Payments if you later add paid events.", "info")
    flash(f"{'Fest' if is_fest else 'Event'} created with {len(ids) if is_fest else 1} event{'s' if is_fest and len(ids) != 1 else ''}. "
          "Everything can be changed from here.", "success")
    return redirect(url_for("studio.event", eid=eid))


@bp.route("/events/<int:eid>/edit", methods=["GET", "POST"])
def event_edit(eid):
    need(eid, "lead")
    event = q("SELECT * FROM events WHERE id=?", (eid,), one=True) or abort(404)
    if request.method == "POST":
        data, errors = _form(q("SELECT * FROM users WHERE id=?", (event["college_id"],), one=True), event)
        try:
            if not errors:
                _banner(data, event["banner"])
        except ValueError as err:
            errors.append(str(err))
        if errors:
            for e in errors:
                flash(e, "error")
            data.update(id=eid, banner=event["banner"], kind=event["kind"], parent_id=event["parent_id"],
                        track_id=data.get("track_id", event["track_id"]), fee_type=data.get("fee_type", event["fee_type"]),
                        label=data.get("label", event["label"]))
            return _wizard(data, "edit", active=f"ev{eid}", **_sub_ctx(event))
        ex(f"UPDATE events SET {', '.join(k + '=?' for k in data)} WHERE id=?", tuple(data.values()) + (eid,))
        if event["kind"] != "fest":
            # the default slot (the whole event) follows the event's new times
            slots = scheduler.slots_of(eid)
            if len(slots) == 1 and slots[0]["start_dt"][:16] == (event["start_dt"] or "")[:16] \
                    and slots[0]["end_dt"][:16] == (event["end_dt"] or "")[:16] \
                    and (data["start_dt"][:16], data["end_dt"][:16]) != (event["start_dt"][:16], event["end_dt"][:16]):
                s = slots[0]
                moved = scheduler.save_slot(s, core.parse_dt(data["start_dt"]), core.parse_dt(data["end_dt"]), s["venue"],
                                            max(s["capacity"], data["capacity"]), s["label"])
                fresh = q("SELECT * FROM event_slots WHERE id=?", (s["id"],), one=True)
                scheduler.tell([(r, fresh) for r in moved], g.user["id"], why="The time changed.")
        if event["kind"] == "fest":
            if data["status"] != event["status"]:  # sub-events that followed the fest follow it again
                ex("UPDATE events SET status=? WHERE parent_id=? AND status=?", (data["status"], eid, event["status"]))
            festlib.sync(eid)
        if event["parent_id"]:
            festlib.sync(event["parent_id"])
        if data["status"] == "completed" and event["status"] != "completed" and not event["parent_id"]:
            live.finish_scope(eid)
        if event["status"] != "open" and data["status"] == "open":
            fol = [r[0] for r in q("SELECT follower_id FROM follows WHERE college_id=?", (event["college_id"],))]
            core.notify_many(fol, "event", f"Registrations are open for {data['title']}.", url_for("events.detail", eid=eid))
        core.audit("event.edit", f"#{eid}")
        flash("Saved.", "success")
        return redirect(url_for("studio.event", eid=eid))
    return _wizard(dict(event), "edit", active=f"ev{eid}", **_sub_ctx(event))


def _sub_ctx(event):
    if not event["parent_id"]:
        return {}
    return {"tracks": festlib.tracks(event["parent_id"]), "parent_title": scalar("SELECT title FROM events WHERE id=?", (event["parent_id"],))}


@bp.route("/events/<int:eid>/delete", methods=["POST"])
def event_delete(eid):
    need(eid, "owner")
    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True) or abort(404)
    paid = scalar("""SELECT COUNT(*) FROM payments WHERE status='paid' AND amount>0
                     AND event_id IN (SELECT id FROM events WHERE id=? OR parent_id=?)""", (eid, eid))
    if e["parent_id"]:
        need(e["parent_id"], "lead")
    if paid:
        flash(f"{paid} people have paid for this event. Mark it closed or completed instead of deleting it.", "error")
        return redirect(url_for("studio.event_edit", eid=eid))
    core.delete_upload(e["banner"])
    ex("DELETE FROM events WHERE parent_id=?", (eid,))
    ex("DELETE FROM events WHERE id=?", (eid,))
    core.audit("event.delete", e["title"])
    flash("Event deleted.", "info")
    if e["parent_id"]:
        festlib.sync(e["parent_id"])
        return redirect(url_for("studio.event", eid=e["parent_id"]) + "#events")
    return redirect(url_for("studio.overview"))


# ================================================================== control room
@bp.route("/events/<int:eid>")
def event(eid):
    role = need(eid)
    if role == "volunteer":
        return redirect(url_for("studio.checkin", eid=eid))
    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True) or abort(404)
    if e["kind"] == "fest":
        return _fest_room(e, role)
    stats = core.event_stats(eid)
    regs = q("""SELECT r.*, u.name, u.username, u.email, u.phone, u.department, u.year, u.college_name, u.avatar,
                       (SELECT p.status FROM payments p WHERE p.registration_id=r.id ORDER BY p.id DESC LIMIT 1) pay_status
                FROM registrations r JOIN users u ON u.id=r.user_id WHERE r.event_id=?
                ORDER BY CASE r.status WHEN 'confirmed' THEN 0 WHEN 'payment_review' THEN 1 WHEN 'waitlisted' THEN 2
                         WHEN 'pending_payment' THEN 3 ELSE 4 END, COALESCE(r.slot_start,'9999'), r.created_at""", (eid,))
    payments = q("""SELECT p.*, u.name, u.username, u.phone, r.pass_code FROM payments p JOIN users u ON u.id=p.user_id
                    JOIN registrations r ON r.id=p.registration_id WHERE p.event_id=? AND p.method!='free'
                    ORDER BY CASE p.status WHEN 'submitted' THEN 0 WHEN 'refunded' THEN 1 ELSE 2 END, p.created_at DESC""", (eid,))
    schedule = q("SELECT * FROM schedule_items WHERE event_id=? ORDER BY start_dt", (eid,))
    coupons = q("""SELECT c.*, (SELECT COUNT(*) FROM registrations r WHERE r.event_id=c.event_id AND r.coupon_code=c.code COLLATE NOCASE
                           AND r.status IN ('confirmed','payment_review','pending_payment')) used_now
                    FROM coupons c WHERE c.event_id=? ORDER BY c.id DESC""", (eid,))
    news = q("""SELECT a.*, u.name author FROM announcements a LEFT JOIN users u ON u.id=a.created_by
                WHERE a.event_id=? ORDER BY a.created_at DESC""", (eid,))
    faqs = q("SELECT * FROM faqs WHERE event_id=?", (eid,))
    rooms = q("""SELECT slot_venue, COUNT(*) c, MIN(slot_start) first, MAX(slot_end) last FROM registrations
                 WHERE event_id=? AND status='confirmed' AND slot_venue IS NOT NULL GROUP BY slot_venue""", (eid,))
    staff = q("""SELECT u.name, u.username, u.avatar, s.role FROM event_staff s JOIN users u ON u.id=s.user_id WHERE s.event_id=?""", (eid,))
    winners = core.event_winners(eid)
    teams = sorted({r["team_name"] for r in regs if r["status"] == "confirmed" and r["team_name"]}, key=str.lower)
    parent = q("SELECT id, title FROM events WHERE id=?", (e["parent_id"],), one=True) if e["parent_id"] else None
    scope = e["parent_id"] or eid
    return render_template("studio/event.html", e=e, stats=stats, parent=parent, track=festlib.track_of(e), regs=regs, payments=payments, schedule=schedule,
                           coupons=coupons, news=news, faqs=faqs, rooms=rooms, staff=staff, role=role,
                           winners=winners, teams=teams, win_titles=core.WIN_TITLES, win_points=core.WIN_POINTS,
                           food=core.food_estimate(e, stats), default_start=core.iso(core.parse_dt(e["start_dt"])),
                           default_finish=core.iso(core.parse_dt(e["end_dt"])), scope_id=scope,
                           bd=scheduler.board(scope, [eid]), flow=None if e["parent_id"] else scheduler.flow(eid),
                           busy_count=scalar("""SELECT COUNT(DISTINCT b.user_id) FROM busy_times b JOIN registrations r
                                                ON r.user_id=b.user_id AND r.event_id=? AND r.status='confirmed'
                                                WHERE b.event_id IS NULL OR b.event_id=?""", (eid, eid)),
                           active=f"ev{eid}")


def _fest_room(e, role):
    eid = e["id"]
    groups = festlib.structure(eid, None, include_drafts=True)
    subs = festlib.sub_events(eid, include_drafts=True)
    sub_ids = [x["id"] for x in subs]
    c, a = in_clause(sub_ids)
    kpis = {
        "participants": scalar(f"SELECT COUNT(DISTINCT user_id) FROM registrations WHERE event_id IN {c} AND status='confirmed'", a) if sub_ids else 0,
        "registrations": sum(x["reg_count"] for x in subs), "checked_in": sum(x["att_count"] for x in subs),
        "revenue": sum(x["revenue"] for x in subs), "review": sum(x["review_count"] for x in subs), "events": len(subs),
    }
    pending = []
    if sub_ids:
        rows = q(f"""SELECT p.*, u.name, u.username, u.phone, e.title FROM payments p JOIN users u ON u.id=p.user_id JOIN events e ON e.id=p.event_id
                     WHERE p.event_id IN {c} AND p.status IN ('submitted','paid','refunded') AND p.method!='free'
                     ORDER BY CASE p.status WHEN 'submitted' THEN 0 WHEN 'refunded' THEN 1 ELSE 2 END, p.created_at DESC, p.id""", a)
        orders = {}
        for p in rows:
            key = (p["bundle"] or p["receipt_no"], p["status"])
            o = orders.setdefault(key, {"head": p, "events": [], "total": 0, "status": p["status"]})
            o["events"].append(p["title"])
            o["total"] += p["amount"]
        pending = list(orders.values())[:200]
    news = q("""SELECT a.*, u.name author FROM announcements a LEFT JOIN users u ON u.id=a.created_by
                WHERE a.event_id=? ORDER BY a.created_at DESC""", (eid,))
    return render_template("studio/fest.html", e=e, groups=groups, subs=subs, kpis=kpis, orders=pending, news=news, role=role,
                           lead=role in ("owner", "dev", "lead"), active=f"ev{eid}", CATS=core.CATEGORIES, scope_id=eid,
                           bd=scheduler.board(eid), flow=scheduler.flow(eid), default_start=core.iso(core.parse_dt(e["start_dt"])))


@bp.route("/events/<int:eid>/tracks", methods=["POST"])
def track_save(eid):
    need(eid, "lead")
    e = q("SELECT * FROM events WHERE id=? AND kind='fest'", (eid,), one=True) or abort(404)
    f = request.form
    name = (f.get("name") or "").strip()[:60]
    if not name:
        flash("Give the track a name.", "error")
        return back(eid, "events")
    pricing = "pass" if f.get("pricing") == "pass" else "event"
    try:
        pass_fee = max(0, int(float(f.get("pass_fee") or 0)))
    except ValueError:
        pass_fee = 0
    emoji = (f.get("emoji") or "").strip()[:4] or "✨"
    blurb = (f.get("blurb") or "").strip()[:200]
    tid = f.get("track_id")
    if tid:
        t = q("SELECT * FROM fest_tracks WHERE id=? AND fest_id=?", (tid, eid), one=True) or abort(404)
        if t["pricing"] != pricing and scalar(f"""SELECT 1 FROM registrations r JOIN events s ON s.id=r.event_id
                                                 WHERE s.track_id=? AND r.status IN {festlib.HELD}""", (t["id"],)):
            flash("People have already registered in this track, so its pricing type can't change now. You can still edit the fee.", "error")
            pricing = t["pricing"]
        ex("UPDATE fest_tracks SET name=?, emoji=?, blurb=?, pricing=?, pass_fee=? WHERE id=?", (name, emoji, blurb, pricing, pass_fee, t["id"]))
        flash(f"{name} saved.", "success")
    else:
        pos = (scalar("SELECT MAX(position) FROM fest_tracks WHERE fest_id=?", (eid,)) or 0) + 1
        ex("INSERT INTO fest_tracks (fest_id, name, emoji, blurb, pricing, pass_fee, position) VALUES (?,?,?,?,?,?,?)",
           (eid, name, emoji, blurb, pricing, pass_fee, pos))
        flash(f"Track “{name}” added. Now add its events.", "success")
    festlib.sync(eid)
    return back(eid, "events")


@bp.route("/tracks/<int:tid>/delete", methods=["POST"])
def track_delete(tid):
    t = q("SELECT * FROM fest_tracks WHERE id=?", (tid,), one=True) or abort(404)
    need(t["fest_id"], "owner")
    if scalar("SELECT 1 FROM events WHERE track_id=? AND is_removed=0", (tid,)):
        flash("Move or delete the events in this track first.", "error")
    else:
        ex("DELETE FROM fest_tracks WHERE id=?", (tid,))
        flash("Track removed.", "info")
    return back(t["fest_id"], "events")


@bp.route("/events/<int:eid>/subs", methods=["POST"])
def sub_add(eid):
    need(eid, "lead")
    e = q("SELECT * FROM events WHERE id=? AND kind='fest'", (eid,), one=True) or abort(404)
    f = request.form
    t = q("SELECT * FROM fest_tracks WHERE id=? AND fest_id=?", (f.get("track_id"), eid), one=True)
    title = (f.get("title") or "").strip()
    if not t or not title:
        flash("Pick a track and give the event a name.", "error")
        return back(eid, "events")
    try:
        fee = max(0, int(float(f.get("fee") or 0))) if t["pricing"] == "event" else 0
        team = min(15, max(1, int(f.get("team_size") or 1)))
        cap = max(1, int(f.get("capacity") or 100))
    except ValueError:
        flash("Fee, team size and seats must be numbers.", "error")
        return back(eid, "events")
    owner = q("SELECT * FROM users WHERE id=?", (e["college_id"],), one=True)
    if fee and not owner["upi_id"]:
        flash("Add the college's UPI ID in Settings → Payments before adding paid events.", "error")
        return back(eid, "events")
    if scalar("SELECT 1 FROM events WHERE parent_id=? AND lower(title)=lower(?) AND is_removed=0", (eid, title.strip())):
        flash(f"There's already an event called “{title.strip()}” in this fest.", "error")
        return back(eid, "events")
    start = core.parse_dt(f.get("start_dt")) or core.parse_dt(e["start_dt"])
    end = core.parse_dt(f.get("end_dt")) or core.parse_dt(e["end_dt"])
    if end <= start:
        flash("The event must end after it starts.", "error")
        return back(eid, "events")
    sid = festlib.add_sub(e, t["id"], title, category=f.get("category") or e["category"], fee=fee,
                          fee_type="team" if f.get("fee_type") == "team" and team > 1 else "person", team_size=team,
                          label=f.get("label"), capacity=cap, venue=(f.get("venue") or "").strip()[:120] or None,
                          start_dt=core.iso(start), end_dt=core.iso(end))
    scheduler.ensure_default_slot(q("SELECT * FROM events WHERE id=?", (sid,), one=True))
    festlib.sync(eid)
    core.audit("fest.event", f"{e['title']}: {title} (#{sid})")
    flash(f"{title} added to {t['name']}. It runs as one slot for now; split it into time slots under Slots & flow.", "success")
    return back(eid, "events")


def _remove_sub(e):
    """Take an event out of its fest. Refused once people have paid for it (close it instead)."""
    paid = scalar("""SELECT COUNT(*) FROM payments WHERE event_id=? AND amount>0 AND status IN ('paid','submitted')""", (e["id"],))
    if paid:
        return False, f"{e['title']}: {paid} {'person has' if paid == 1 else 'people have'} paid for it. Close it instead, or cancel and refund them first."
    people = [r[0] for r in q("""SELECT user_id FROM registrations WHERE event_id=? AND status IN
                                 ('confirmed','payment_review','pending_payment','waitlisted')""", (e["id"],))]
    fest_title = scalar("SELECT title FROM events WHERE id=?", (e["parent_id"],))
    core.notify_many(people, "event", f"{e['title']} was removed from {fest_title} by the organisers. Your other events are unchanged.",
                     url_for("events.tickets"), e["college_id"])
    core.delete_upload(e["banner"])
    ex("DELETE FROM events WHERE id=?", (e["id"],))
    core.audit("fest.event.remove", f"{fest_title}: {e['title']} (#{e['id']})")
    return True, f"{e['title']} removed" + (f" ({len(people)} registrations cancelled and told)" if people else "") + "."


@bp.route("/events/<int:eid>/remove", methods=["POST"])
def sub_remove(eid):
    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True) or abort(404)
    if not e["parent_id"]:
        abort(400)
    need(e["parent_id"], "lead")
    ok, msg = _remove_sub(e)
    flash(msg, "success" if ok else "error")
    festlib.sync(e["parent_id"])
    return back(e["parent_id"], "events")


@bp.route("/events/<int:eid>/subs/remove", methods=["POST"])
def subs_remove(eid):
    """Remove several of a fest's events at once (the tick boxes)."""
    need(eid, "lead")
    picked = {int(x) for x in request.form.getlist("sub") if x.isdigit()}
    subs = [s for s in q("SELECT * FROM events WHERE parent_id=? AND is_removed=0", (eid,)) if s["id"] in picked]
    if not subs:
        flash("Tick the events you want to remove first.", "error")
        return back(eid, "events")
    done, refused = 0, []
    for s_ in subs:
        ok, msg = _remove_sub(s_)
        if ok:
            done += 1
        else:
            refused.append(msg)
    festlib.sync(eid)
    if done:
        flash(f"Removed {done} event{'s' if done != 1 else ''}.", "success")
    for msg in refused[:4]:
        flash(msg, "error")
    return back(eid, "events")


def back(eid, tab):
    return redirect(url_for("studio.event", eid=eid) + "#" + tab)


@bp.route("/events/<int:eid>/payments")
def payments(eid):
    need(eid)
    return back(eid, "payments")


@bp.route("/payments/<int:pid>/<action>", methods=["POST"])
def payment_action(pid, action):
    p = q("""SELECT p.*, e.title, r.status reg_status FROM payments p JOIN events e ON e.id=p.event_id
             JOIN registrations r ON r.id=p.registration_id WHERE p.id=?""", (pid,), one=True) or abort(404)
    need(p["event_id"], "lead")
    group = [x for x in festlib.bundle_payments(p) if x["status"] == "submitted"] if p["status"] == "submitted" else []
    for x in group:  # a fest order is approved or rejected as a whole
        if x["id"] != p["id"] and not core.can_manage(g.user, x["event_id"]):
            abort(403)
    if action == "approve" and group:
        cancelled = {x["id"] for x in group if scalar("SELECT status FROM registrations WHERE id=?", (x["registration_id"],)) != "payment_review"}
        for x in group:
            if x["id"] in cancelled:
                ex("UPDATE payments SET status='rejected', note='Registration was cancelled' WHERE id=?", (x["id"],))
            else:
                confirm(x["registration_id"], x["id"], g.user["id"], place=False)
        for ev_id in {x["event_id"] for x in group if x["id"] not in cancelled}:
            ev = q("SELECT id, parent_id FROM events WHERE id=?", (ev_id,), one=True)
            if ev:
                scheduler.place_new(ev["parent_id"] or ev["id"], actor_id=g.user["id"])
        if cancelled and len(cancelled) == len(group):
            flash("This registration was cancelled, so the payment can't be approved. Refund it if money arrived.", "error")
        else:
            flash("Payment approved. " + (f"All {len(group) - len(cancelled)} tickets in this order are" if len(group) > 1 else "The ticket is")
                  + " now active.", "success")
    elif action == "reject" and group:
        note = (request.form.get("note") or "").strip()[:200] or "We couldn't find this UTR in our account."
        for x in group:
            ex("UPDATE payments SET status='rejected', note=?, reviewed_by=?, reviewed_at=? WHERE id=?",
               (note, g.user["id"], datetime.now().strftime("%Y-%m-%d %H:%M:%S"), x["id"]))
            ex("UPDATE registrations SET status='pending_payment' WHERE id=? AND status='payment_review'", (x["registration_id"],))
        core.notify(p["user_id"], "payment", f"Payment for {p['title']}{' and more' if len(group) > 1 else ''} wasn't verified: {note}",
                    url_for("events.checkout", receipt=p["receipt_no"]), g.user["id"])
        flash("Payment rejected. The participant was asked to resubmit.", "info")
    elif action == "refunded" and p["status"] == "refunded":
        ex("UPDATE payments SET note=? WHERE id=?", ("Refund sent by college on " + datetime.now().strftime("%d %b %Y"), pid))
        core.notify(p["user_id"], "payment", f"Your refund of ₹{p['amount']} for {p['title']} has been sent.", None, g.user["id"])
        flash("Marked as refunded. The participant has been told.", "success")
    nxt = request.form.get("next") or ""
    if nxt.startswith("/studio/") and "//" not in nxt:
        return redirect(nxt)
    return back(p["event_id"], "payments")


def _reg(rid):
    r = q("SELECT * FROM registrations WHERE id=?", (rid,), one=True) or abort(404)
    need(r["event_id"])
    return r


@bp.route("/registrations/<int:rid>/attendance", methods=["POST"])
def attendance(rid):
    r = _reg(rid)
    if r["status"] != "confirmed":
        flash("Only confirmed registrations can be checked in.", "error")
    elif r["attended"]:
        ex("UPDATE registrations SET attended=0, checkin_time=NULL WHERE id=?", (rid,))
    else:
        ex("UPDATE registrations SET attended=1, checkin_time=? WHERE id=?", (core.now_iso(), rid))
        title = scalar("SELECT title FROM events WHERE id=?", (r["event_id"],))
        core.notify(r["user_id"], "event", f"Checked in at {title}. +{core.POINTS_ATTEND} points and your certificate is unlocked!",
                    url_for("events.certificate", code=r["pass_code"]))
    return back(r["event_id"], "registrations")


@bp.route("/registrations/<int:rid>/status", methods=["POST"])
def reg_status(rid):
    r = _reg(rid)
    need(r["event_id"], "lead")
    title = scalar("SELECT title FROM events WHERE id=?", (r["event_id"],))
    if r["status"] == "cancelled":
        e = q("SELECT * FROM events WHERE id=?", (r["event_id"],), one=True)
        last = q("SELECT * FROM payments WHERE registration_id=? ORDER BY id DESC LIMIT 1", (rid,), one=True)
        paid = bool(last and last["status"] == "paid")
        if core.seats_taken(r["event_id"]) >= e["capacity"]:
            flash("No seats left, so this registration can't be restored.", "error")
        elif r["amount"] and not paid:
            flash("This ticket wasn't paid for (or was refunded). Ask them to register again.", "error")
        else:
            ex("UPDATE registrations SET status='confirmed' WHERE id=?", (rid,))
            scheduler.place_new(e["parent_id"] or e["id"], actor_id=g.user["id"])
            flash("Registration restored.", "success")
    else:
        from blueprints.events import cancel_reg
        regs, refunds = cancel_reg(r, "Cancelled by organiser", g.user["id"])
        core.notify(r["user_id"], "event", f"Your registration for {title} was cancelled by the organiser."
                    + (" A refund will be sent to you." if refunds else ""), None, g.user["id"])
        flash("Registration cancelled." + (f" Also cancelled {len(regs) - 1} that depended on it." if len(regs) > 1 else "")
              + (f" Refund of ₹{refunds} marked as pending." if refunds else ""), "info")
    return back(r["event_id"], "registrations")


# ================================================================== time slots, allocation and the event-day flow
def _scope_target(eid):
    """For slot pages: (scope id, [event ids] or None). A fest works on all its events; one of its events on itself."""
    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True) or abort(404)
    return e, (e["parent_id"] or e["id"]), ([e["id"]] if e["parent_id"] else None)


def _back_slots(eid):
    nxt = request.form.get("next") or ""
    if nxt.startswith("/studio/") and "//" not in nxt:
        return redirect(nxt)
    return back(eid, "slots")


def _slot_form(prefix=""):
    f = request.form
    s, e = core.parse_dt(f.get(prefix + "start")), core.parse_dt(f.get(prefix + "end"))
    return s, e, (f.get(prefix + "venue") or "").strip()[:80], _int(f.get(prefix + "capacity"), 1, 100000, 20), \
        (f.get(prefix + "label") or "").strip()[:40]


@bp.route("/events/<int:eid>/slots/generate", methods=["POST"])
def slots_generate(eid):
    """Make back-to-back slots for one or more events (e.g. 5 slots of 60 minutes, 20 members each)."""
    need(eid, "lead")
    e, scope, only = _scope_target(eid)
    allowed = [x["id"] for x in scheduler.scope_events(scope)] if not only else only
    if e["kind"] == "fest":
        picked = [int(x) for x in request.form.getlist("event_ids") if x.isdigit() and int(x) in allowed]
    else:
        picked = [e["id"]]
    f = request.form
    start = core.parse_dt(f.get("start"))
    minutes, gap, count = _int(f.get("minutes"), 5, 1440, 60), _int(f.get("gap"), 0, 600, 0), _int(f.get("count"), 1, 48, 3)
    capacity = _int(f.get("capacity"), 1, 100000, 20)
    if not picked:
        flash("Tick at least one event.", "error")
        return _back_slots(eid)
    if not start:
        flash("Pick when the first slot starts.", "error")
        return _back_slots(eid)
    made, freed = scheduler.generate(picked, start, minutes, gap, count, capacity, f.get("venue"), replace=bool(f.get("replace")))
    for ev_id in picked:                                   # an event always has at least one slot
        scheduler.ensure_default_slot(q("SELECT * FROM events WHERE id=?", (ev_id,), one=True))
    res = scheduler.place_new(scope, actor_id=g.user["id"])
    core.audit("slots.generate", f"#{scope}: {made} slots for {len(picked)} events")
    msg = f"Made {made} slot{'s' if made != 1 else ''} for {len(picked)} event{'s' if len(picked) != 1 else ''}."
    if freed:
        msg += f" {len(freed)} people from the old slots were re-allocated."
    if res["unplaced"]:
        msg += f" {sum(u['size'] for u, _ in res['unplaced'])} people still need a slot: see the list below."
    flash(msg, "success" if not res["unplaced"] else "info")
    return _back_slots(eid)


@bp.route("/events/<int:eid>/slots/add", methods=["POST"])
def slot_add(eid):
    need(eid, "lead")
    e = q("SELECT * FROM events WHERE id=? AND kind!='fest'", (eid,), one=True) or abort(404)
    s, end, venue, cap, label = _slot_form()
    if not s or not end or end <= s:
        flash("A slot needs a start and an end (after the start).", "error")
        return _back_slots(e["parent_id"] or eid)
    scheduler.add_slot(eid, s, end, venue, cap, label)
    res = scheduler.place_new(e["parent_id"] or eid, actor_id=g.user["id"])
    flash(f"Slot added to {e['title']}." + (" People who had no slot were placed." if res["moves"] else ""), "success")
    return _back_slots(e["parent_id"] or eid)


@bp.route("/slots/<int:sid>/save", methods=["POST"])
def slot_save(sid):
    sl = q("SELECT * FROM event_slots WHERE id=?", (sid,), one=True) or abort(404)
    need(sl["event_id"], "lead")
    e = q("SELECT * FROM events WHERE id=?", (sl["event_id"],), one=True)
    s, end, venue, cap, label = _slot_form()
    if not s or not end or end <= s:
        flash("A slot needs a start and an end (after the start).", "error")
        return _back_slots(e["parent_id"] or e["id"])
    moved = scheduler.save_slot(sl, s, end, venue, cap, label)
    fresh = q("SELECT * FROM event_slots WHERE id=?", (sid,), one=True)
    scheduler.tell([(r, fresh) for r in moved], g.user["id"], why="Your slot was changed by the organisers.")
    load = scalar("SELECT COUNT(*) FROM registrations WHERE slot_id=? AND status='confirmed'", (sid,)) or 0
    flash("Slot saved." + (f" {len(moved)} people in it were told the new time." if moved else "")
          + (f" It now has {load} members for {cap} places: move some, or re-allocate." if load > cap else ""),
          "success" if load <= cap else "info")
    return _back_slots(e["parent_id"] or e["id"])


@bp.route("/slots/<int:sid>/delete", methods=["POST"])
def slot_delete(sid):
    sl = q("SELECT * FROM event_slots WHERE id=?", (sid,), one=True) or abort(404)
    need(sl["event_id"], "lead")
    e = q("SELECT * FROM events WHERE id=?", (sl["event_id"],), one=True)
    if (scalar("SELECT COUNT(*) FROM event_slots WHERE event_id=?", (e["id"],)) or 0) <= 1:
        flash("An event needs at least one slot. Edit this one instead.", "error")
        return _back_slots(e["parent_id"] or e["id"])
    freed = scheduler.delete_slot(sl)
    res = scheduler.place_new(e["parent_id"] or e["id"], actor_id=g.user["id"])
    left = sum(u["size"] for u, _ in res["unplaced"] if u["event_id"] == e["id"])
    flash("Slot removed." + (f" {len(freed) - left} of its {len(freed)} people were moved to other slots." if freed else "")
          + (f" {left} still need a slot." if left else ""), "success" if not left else "info")
    return _back_slots(e["parent_id"] or e["id"])


@bp.route("/events/<int:eid>/allocate", methods=["POST"])
def allocate(eid):
    """Slot allocator: Preview shows the plan without saving; Allocate saves it and tells everyone whose slot changed."""
    need(eid, "lead")
    e, scope, only = _scope_target(eid)
    mode = "all" if request.form.get("mode") == "all" else "new"
    keep = bool(request.form.get("keep_locked")) or mode == "new"
    result = scheduler.plan(scope, mode=mode, keep_locked=keep, event_ids=only)
    if not result["total"]:
        flash("No confirmed participants to allocate yet.", "info")
        return _back_slots(eid)
    if request.form.get("action") != "apply":
        return render_template("studio/slot_plan.html", e=e, r=result, mode=mode, keep=keep, announce=request.form.get("announce"),
                               active=f"ev{eid}")
    changed = scheduler.apply(result)
    scheduler.tell(changed, g.user["id"])
    if request.form.get("announce") and changed:
        body = (f"Time slots are allocated for {result['people']} participants. Open your ticket: your task list shows each "
                "event's slot, and each event's QR only works in your own slot. Please be there 10 minutes early.")
        ex("INSERT INTO announcements (event_id, title, body, priority, kind, created_by) VALUES (?,?,?,?,?,?)",
           (eid, "Your slots are out", body, "important", "slot", g.user["id"]))
    core.audit("slots.allocate", f"#{eid} {mode}: {result['people']}/{result['total']} placed, {len(changed)} changed")
    left = sum(u["size"] for u, _ in result["unplaced"])
    flash(f"Allocated: {result['people']} of {result['total']} people have a slot; {len(changed)} were told about a new slot."
          + (f" {left} couldn't be placed (reasons are listed): add a slot or place them by hand." if left else ""),
          "success" if not left else "info")
    return _back_slots(eid)


@bp.route("/events/<int:eid>/slots/move", methods=["POST"])
def slot_move(eid):
    """Exception: put a person, or their whole team, in a chosen slot. It's locked, so re-allocating keeps it."""
    r = q("SELECT * FROM registrations WHERE id=?", (request.form.get("rid", type=int),), one=True) or abort(404)
    need(r["event_id"], "lead")
    sl = q("SELECT * FROM event_slots WHERE id=? AND event_id=?", (request.form.get("slot_id", type=int), r["event_id"]), one=True)
    if not sl or r["status"] != "confirmed":
        flash("Pick one of this event's slots for a confirmed participant.", "error")
        return _back_slots(eid)
    ev = q("SELECT * FROM events WHERE id=?", (r["event_id"],), one=True)
    moved, regs, warn = scheduler.move_unit(r["event_id"], r, sl)
    text = scheduler.slot_text(sl, venue=ev["venue"])
    for m in moved:
        core.notify(m["user_id"], "slot", f"Your slot for {ev['title']} was set by the organisers: {text}. "
                    "Use the new QR on your ticket.", url_for("events.ticket", code=m["pass_code"]), g.user["id"])
    who = f"Team {r['team_name']}" if r["team_name"] else scalar("SELECT name FROM users WHERE id=?", (r["user_id"],))
    core.audit("slots.move", f"{ev['title']}: {who} -> slot #{sl['id']}")
    flash(f"{who} is now in {text} and locked there{', and was notified' if moved else ''}."
          + (" Heads up: " + "; ".join(warn) + "." if warn else ""), "success" if not warn else "info")
    return _back_slots(eid)


@bp.route("/events/<int:eid>/slots/unlock", methods=["POST"])
def slot_unlock(eid):
    r = q("SELECT * FROM registrations WHERE id=?", (request.form.get("rid", type=int),), one=True) or abort(404)
    need(r["event_id"], "lead")
    if r["team_name"]:
        ex("UPDATE registrations SET slot_locked=0 WHERE event_id=? AND lower(team_name)=lower(?)", (r["event_id"], r["team_name"]))
    else:
        ex("UPDATE registrations SET slot_locked=0 WHERE id=?", (r["id"],))
    flash("Unlocked. Re-allocating everyone may now move them.", "info")
    return _back_slots(eid)


@bp.route("/events/<int:eid>/flow", methods=["POST"])
def flow_save(eid):
    """The event-day order the organisers dragged into place: entry, then events and meals."""
    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True) or abort(404)
    if e["parent_id"]:
        abort(400)
    need(eid, "lead")
    try:
        items = json.loads(request.form.get("flow") or "[]")
    except ValueError:
        items = None
    if not isinstance(items, list):
        flash("Couldn't read the new order. Try again.", "error")
        return _back_slots(eid)
    clean, errors = [], []
    for it in items[:150]:
        if not isinstance(it, dict):
            continue
        key = str(it.get("key") or "")
        if key.startswith("food") or key.startswith("new"):
            s, en = core.parse_dt(it.get("start")), core.parse_dt(it.get("end"))
            if s and en and en <= s:
                errors.append(f"{(it.get('title') or 'A meal').strip()} must end after it starts.")
                continue
        clean.append({"key": key, "title": it.get("title"), "start": it.get("start"), "end": it.get("end"), "venue": it.get("venue")})
    if errors:
        for m in errors:
            flash(m, "error")
        return _back_slots(eid)
    scheduler.save_flow(eid, clean)
    meals = scalar("SELECT COUNT(*) FROM flow_items WHERE scope_id=? AND kind='food'", (eid,)) or 0
    ex("UPDATE events SET meals_count=? WHERE id=?", (meals, eid))
    core.audit("flow.save", f"#{eid}: {len(clean)} items")
    flash("Event-day order saved. Every participant's task list follows it now.", "success")
    return _back_slots(eid)


@bp.route("/events/<int:eid>/schedule", methods=["POST"])
def schedule_add(eid):
    need(eid, "lead")
    title = (request.form.get("title") or "").strip()[:120]
    s, e = core.parse_dt(request.form.get("start_dt")), core.parse_dt(request.form.get("end_dt"))
    if not title or not s:
        flash("A session needs a name and a start time.", "error")
    elif e and e <= s:
        flash("A session must end after it starts.", "error")
    else:
        ex("INSERT INTO schedule_items (event_id, title, start_dt, end_dt, venue, description) VALUES (?,?,?,?,?,?)",
           (eid, title, core.iso(s), core.iso(e), (request.form.get("venue") or "").strip()[:100] or None,
            (request.form.get("description") or "").strip()[:300] or None))
        flash("Session added.", "success")
    return back(eid, "schedule")


@bp.route("/schedule/<int:sid>/delete", methods=["POST"])
def schedule_delete(sid):
    eid = scalar("SELECT event_id FROM schedule_items WHERE id=?", (sid,)) or abort(404)
    need(eid, "lead")
    ex("DELETE FROM schedule_items WHERE id=?", (sid,))
    return back(eid, "schedule")


@bp.route("/events/<int:eid>/food", methods=["POST"])
def food(eid):
    need(eid, "lead")
    try:
        vals = (max(0.0, float(request.form.get("food_cost_per_head") or 0)), max(0, int(request.form.get("meals_count") or 0)),
                max(0.0, float(request.form.get("food_buffer_pct") or 0)), eid)
    except ValueError:
        flash("Enter numbers only.", "error")
        return back(eid, "food")
    ex("UPDATE events SET food_cost_per_head=?, meals_count=?, food_buffer_pct=? WHERE id=?", vals)
    flash("Food plan saved.", "success")
    return back(eid, "food")


@bp.route("/events/<int:eid>/coupons", methods=["POST"])
def coupon_add(eid):
    need(eid, "lead")
    code = "".join(ch for ch in (request.form.get("code") or "").upper() if ch.isalnum())[:20]
    try:
        pct = int(request.form.get("percent_off") or 0)
        uses = max(1, int(request.form.get("max_uses") or 1))
    except ValueError:
        pct, uses = 0, 1
    if len(code) < 3 or not 1 <= pct <= 100:
        flash("Codes need 3+ letters or digits and a discount between 1% and 100%.", "error")
    elif scalar("SELECT 1 FROM coupons WHERE event_id=? AND code=?", (eid, code)):
        flash("That code already exists for this event.", "error")
    else:
        ex("INSERT INTO coupons (event_id, code, percent_off, max_uses) VALUES (?,?,?,?)", (eid, code, pct, uses))
        flash(f"Code {code} created.", "success")
    return back(eid, "coupons")


@bp.route("/coupons/<int:cid>/toggle", methods=["POST"])
def coupon_toggle(cid):
    c = q("SELECT * FROM coupons WHERE id=?", (cid,), one=True) or abort(404)
    need(c["event_id"], "lead")
    ex("UPDATE coupons SET active=1-active WHERE id=?", (cid,))
    return back(c["event_id"], "coupons")


@bp.route("/events/<int:eid>/announce", methods=["POST"])
def announce(eid):
    need(eid, "lead")
    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True)
    title = (request.form.get("title") or "").strip()[:120]
    text = (request.form.get("body") or "").strip()[:2000]
    if not title or not text:
        flash("Write a headline and a message.", "error")
        return back(eid, "updates")
    priority = "important" if request.form.get("important") else "normal"
    kind = "slot" if request.form.get("kind") == "slot" else "general"
    ex("INSERT INTO announcements (event_id, title, body, priority, kind, created_by) VALUES (?,?,?,?,?,?)",
       (eid, title, text, priority, kind, g.user["id"]))
    people = [r[0] for r in q("""SELECT DISTINCT user_id FROM registrations WHERE status IN ('confirmed','payment_review','waitlisted')
                                AND event_id IN (SELECT id FROM events WHERE id=? OR parent_id=?)""", (eid, eid))]
    core.notify_many(people, "announcement", f"{'Important: ' if priority == 'important' else ''}{e['title']}: {title}",
                     url_for("events.detail", eid=eid) + "#updates", e["college_id"])
    flash(f"Published and sent to {len(people)} participants.", "success")
    return back(eid, "updates")


# ------------------------------------------------------------------ winners
@bp.route("/events/<int:eid>/winners", methods=["POST"])
def winner_add(eid):
    need(eid, "lead")
    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True) or abort(404)
    try:
        position = int(request.form.get("position", 1))
    except ValueError:
        position = 1
    position = position if position in core.WIN_TITLES else 1
    title = (request.form.get("title") or "").strip()[:60] or core.WIN_TITLES[position]
    who = request.form.get("who") or ""
    if who.startswith("t:"):
        team = who[2:]
        people = q("""SELECT id, user_id, team_name FROM registrations WHERE event_id=? AND status='confirmed'
                      AND team_name=? COLLATE NOCASE""", (eid, team))
    elif who.startswith("r:") and who[2:].isdigit():
        people = q("SELECT id, user_id, team_name FROM registrations WHERE id=? AND event_id=? AND status='confirmed'",
                   (int(who[2:]), eid))
    else:
        people = []
    if not people:
        flash("Pick a confirmed participant or team.", "error")
        return back(eid, "winners")
    pts = core.WIN_POINTS[position]
    added = 0
    for r in people:
        existed = scalar("SELECT 1 FROM event_winners WHERE event_id=? AND user_id=?", (eid, r["user_id"]))
        ex("""INSERT INTO event_winners (event_id, user_id, registration_id, position, title, team_name, points, created_by)
              VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(event_id, user_id) DO UPDATE SET position=excluded.position,
              title=excluded.title, team_name=excluded.team_name, points=excluded.points""",
           (eid, r["user_id"], r["id"], position, title, r["team_name"], pts, g.user["id"]))
        code = scalar("SELECT pass_code FROM registrations WHERE id=?", (r["id"],))
        core.notify(r["user_id"], "event", f"🏆 {title} at {e['title']}! +{pts} points. Your certificate of achievement is ready.",
                    url_for("events.certificate", code=code), e["college_id"])
        added += 0 if existed else 1
    names = ", ".join(scalar("SELECT name FROM users WHERE id=?", (r["user_id"],)) for r in people[:4])
    flash(f"{title}: {names}{' and others' if len(people) > 4 else ''}. They've been notified.", "success")
    core.audit("event.winner", f"{e['title']}: {title} → {names}")
    return back(eid, "winners")


@bp.route("/winners/<int:wid>/delete", methods=["POST"])
def winner_delete(wid):
    w = q("SELECT * FROM event_winners WHERE id=?", (wid,), one=True) or abort(404)
    need(w["event_id"], "lead")
    ex("DELETE FROM event_winners WHERE id=?", (wid,))
    flash("Removed from the winners list.", "info")
    return back(w["event_id"], "winners")


@bp.route("/events/<int:eid>/winners/announce", methods=["POST"])
def winners_announce(eid):
    need(eid, "lead")
    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True) or abort(404)
    ws = core.event_winners(eid)
    if not ws:
        flash("Add at least one winner first.", "error")
        return back(eid, "winners")
    lines, seen = [], set()
    for w in ws:
        key = (w["title"], w["team_name"] or w["name"])
        if key in seen:
            continue
        seen.add(key)
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(w["position"], "🏅")
        who = f"Team {w['team_name']}" if w["team_name"] else w["name"]
        lines.append(f"{medal} **{w['title']}**: {who}")
    body = "Congratulations to everyone who took part!\n\n" + "\n".join(lines)
    ex("INSERT INTO announcements (event_id, title, body, priority, kind, created_by) VALUES (?,?,?,?,?,?)",
       (eid, "🏆 Winners announced", body, "important", "general", g.user["id"]))
    people = [r[0] for r in q("SELECT user_id FROM registrations WHERE event_id=? AND status='confirmed'", (eid,))]
    core.notify_many(people, "announcement", f"🏆 Winners of {e['title']} are out!", url_for("events.detail", eid=eid) + "#winners",
                     e["college_id"])
    flash(f"Winners announced to {len(people)} participants.", "success")
    return back(eid, "winners")


@bp.route("/announcements/<int:aid>/delete", methods=["POST"])
def announce_delete(aid):
    eid = scalar("SELECT event_id FROM announcements WHERE id=?", (aid,)) or abort(404)
    need(eid, "lead")
    ex("DELETE FROM announcements WHERE id=?", (aid,))
    return back(eid, "updates")


@bp.route("/events/<int:eid>/faq", methods=["POST"])
def faq_add(eid):
    need(eid, "lead")
    qn, an = (request.form.get("question") or "").strip()[:300], (request.form.get("answer") or "").strip()[:1000]
    if qn and an:
        ex("INSERT INTO faqs (event_id, question, answer) VALUES (?,?,?)", (eid, qn, an))
        log_id = request.form.get("log_id", type=int)
        if log_id and _own_log(log_id):
            ex("UPDATE chat_logs SET resolved=1 WHERE id=?", (log_id,))
        flash("Saved. The assistant now answers this for everyone.", "success")
    else:
        flash("Write both the question and the answer.", "error")
    return redirect(request.referrer or back(eid, "updates").location)


@bp.route("/faq/<int:fid>/delete", methods=["POST"])
def faq_delete(fid):
    eid = scalar("SELECT event_id FROM faqs WHERE id=?", (fid,)) or abort(404)
    need(eid, "lead")
    ex("DELETE FROM faqs WHERE id=?", (fid,))
    return back(eid, "updates")


@bp.route("/events/<int:eid>/export.csv")
def export(eid):
    need(eid, "lead")
    title = scalar("SELECT title FROM events WHERE id=?", (eid,)) or abort(404)
    rows = q("""SELECT u.name, u.username, u.email, u.phone, u.college_name, u.department, u.year, r.team_name, r.food_pref,
                       r.status, r.amount, r.coupon_code, r.pass_code, r.slot_start, r.slot_end, r.slot_venue, r.attended,
                       r.checkin_time, r.created_at
                FROM registrations r JOIN users u ON u.id=r.user_id WHERE r.event_id=? ORDER BY u.name""", (eid,))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Name", "Username", "Email", "Phone", "College", "Department", "Year", "Team", "Food", "Status", "Paid (INR)",
                "Coupon", "Pass code", "Slot start", "Slot end", "Slot room", "Attended", "Check-in", "Registered at"])
    for r in rows:
        w.writerow(list(r))
    fname = "".join(ch if ch.isalnum() else "_" for ch in title) + "_participants.csv"
    return Response("﻿" + buf.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename={fname}"})


# ================================================================== check-in desks
STATION_INFO = {
    "entry": ("IN", "Entry gate", "🚪", "Everyone starts here. Marks people as arrived.", "#2F80ED"),
    "event": ("EV", "Event desk", "🎯", "At an event, in one time slot. Only people booked into that slot are accepted. "
              "Unlocks their certificate.", "#9B51E0"),
    "food": ("FOOD", "Food counter", "🍽️", "Serve a meal once, when it's next on each person's list.", "#27AE60"),
}


@bp.route("/scan")
def scan_pick():
    """Studio-wide 'Scan QR': pick the event day, then the desk."""
    ids = core.managed_event_ids(g.user)
    if not ids:
        abort(403)
    c, a = in_clause(ids)
    events = q(f"""SELECT e.*, f.title fest FROM events e LEFT JOIN events f ON f.id=e.parent_id WHERE e.id IN {c} AND e.is_removed=0
                   AND (e.parent_id IS NULL OR e.parent_id NOT IN {c})
                   ORDER BY CASE WHEN e.end_dt >= ? THEN 0 ELSE 1 END, e.start_dt""", a + a + [core.now_iso()])
    if len(events) == 1:
        return redirect(url_for("studio.checkin", eid=events[0]["id"]))
    return render_template("studio/scan_pick.html", events=events, active="scan")


@bp.route("/events/<int:eid>/checkin")
@bp.route("/events/<int:eid>/scan")
def checkin(eid):
    """Scan hub: one scanner per kind of desk, so a QR can never be marked at the wrong desk."""
    need(eid)
    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True) or abort(404)
    scope = e["parent_id"] or e["id"]
    return render_template("studio/scan_hub.html", e=e, n=journey.counts(scope), info=STATION_INFO,
                           meals=journey.desk_meals(scope), active=f"ev{eid}")


def _auto_slot(slots, chosen):
    if chosen:
        hit = next((s for s in slots if str(s["id"]) == str(chosen)), None)
        if hit:
            return hit
    now = core.now()
    running = [s for s in slots if core.parse_dt(s["start_dt"]) <= now < core.parse_dt(s["end_dt"])]
    if running:
        return running[0]
    upcoming = [s for s in slots if core.parse_dt(s["start_dt"]) > now]
    return upcoming[0] if upcoming else (slots[0] if slots else None)


@bp.route("/events/<int:eid>/scan/<station>")
def scan_station(eid, station):
    need(eid)
    if station not in STATION_INFO:
        abort(404)
    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True) or abort(404)
    scope = e["parent_id"] or e["id"]
    kind, title, emoji, blurb, color = STATION_INFO[station]
    desk = q("SELECT * FROM events WHERE id=?", (scope,), one=True)
    events, slots, slot, expected, meals, meal = [], [], None, [], [], None
    if station == "event":
        events = [x for x in scheduler.scope_events(scope) if staff_role(x["id"])]
        pick = request.args.get("ev", type=int) or (e["id"] if not e["kind"] == "fest" else None)
        desk = next((x for x in events if x["id"] == pick), None)
        if desk:
            slots = scheduler.slots_of(desk["id"])
            slot = _auto_slot(slots, request.args.get("slot"))
            if slot:
                expected = q("""SELECT r.id, r.pass_code, r.team_name, r.completed_at, u.name, u.avatar FROM registrations r
                                JOIN users u ON u.id=r.user_id WHERE r.slot_id=? AND r.status='confirmed'
                                ORDER BY lower(COALESCE(r.team_name, u.name)), u.name""", (slot["id"],))
    elif station == "food":
        meals = journey.desk_meals(scope)
        want = request.args.get("meal")
        meal = next((m for m in meals if m["key"] == want), None)
        if not meal and meals:
            now = core.now()
            meal = next((m for m in meals if m["start"] and m["end"] and core.parse_dt(m["start"]) <= now < core.parse_dt(m["end"])), meals[0])
    if station == "entry":
        recent = q("""SELECT u.name, u.department, u.avatar, MIN(r.checkin_time) at FROM registrations r JOIN users u ON u.id=r.user_id
                      JOIN events ev ON ev.id=r.event_id WHERE (ev.id=? OR ev.parent_id=?) AND r.checkin_time IS NOT NULL
                      GROUP BY r.user_id, u.name, u.department, u.avatar ORDER BY at DESC LIMIT 12""", (scope, scope))
    elif station == "event":
        recent = q("""SELECT u.name, u.department, u.avatar, r.completed_at at, ev.title ev_title FROM registrations r
                      JOIN users u ON u.id=r.user_id JOIN events ev ON ev.id=r.event_id
                      WHERE r.event_id=? AND r.completed_at IS NOT NULL ORDER BY r.completed_at DESC LIMIT 12""",
                   (desk["id"] if desk else 0,))
    else:
        recent = q("""SELECT u.name, u.department, u.avatar, m.created_at at FROM task_marks m JOIN users u ON u.id=m.user_id
                      WHERE m.scope_id=? AND m.task_key=? AND m.state='done' ORDER BY m.created_at DESC LIMIT 12""",
                   (scope, meal["key"] if meal else ""))
    return render_template("studio/checkin.html", e=e, scope=scope, desk=desk, n=journey.counts(scope), recent=recent,
                           active=f"ev{eid}", station=kind, slug=station, title=title, emoji=emoji, blurb=blurb, color=color,
                           events=events, slots=slots, slot=slot, expected=expected, meals=meals, meal=meal,
                           slot_name=lambda s_: scheduler.slot_name(s_, slots), slot_text=scheduler.slot_text)


@bp.route("/api/checkin", methods=["POST"])
def api_checkin():
    """One scan at a desk. The desk says what it's for (entry, an event in one slot, a meal); the QR must match."""
    data = request.get_json(silent=True) or {}
    try:
        eid = int(data.get("event_id") or 0) or None
    except (TypeError, ValueError):
        eid = None
    if not eid or not staff_role(eid):
        return jsonify(ok=False, level="error", message="You don't run a desk for this event.")
    res = journey.scan(data.get("code"), station=(data.get("station") or "").upper(), desk_event=eid, can_staff=staff_role,
                       actor_id=g.user["id"], slot_id=data.get("slot"), meal=data.get("meal") or "", skip=bool(data.get("skip")))
    e = q("SELECT id, parent_id FROM events WHERE id=?", (eid,), one=True)
    res["n"] = journey.counts(e["parent_id"] or e["id"])
    if data.get("slot"):
        res["slot_done"] = scalar("SELECT COUNT(*) FROM registrations WHERE slot_id=? AND status='confirmed' AND completed_at IS NOT NULL",
                                  (data.get("slot"),))
    return jsonify(res)


@bp.route("/events/<int:eid>/complete-all", methods=["POST"])
def complete_all(eid):
    """End of the event: mark everyone who checked in as completed (unlocks their certificates)."""
    need(eid)
    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True) or abort(404)
    rows = q("""SELECT r.id, r.user_id, r.pass_code, ev.title FROM registrations r JOIN events ev ON ev.id=r.event_id
                WHERE (ev.id=? OR ev.parent_id=?) AND r.status='confirmed' AND r.attended=1 AND r.completed_at IS NULL""",
             (eid, eid))
    now = core.now_iso()
    for r in rows:
        ex("UPDATE registrations SET completed_at=? WHERE id=?", (now, r["id"]))
        core.notify(r["user_id"], "event", f"You completed {r['title']}. Your certificate is ready. +{core.POINTS_COMPLETE} points.",
                    url_for("events.certificate", code=r["pass_code"]), g.user["id"])
    core.audit("event.complete_all", f"{e['title']}: {len(rows)}")
    flash(f"Marked {len(rows)} checked-in participant{'s' if len(rows) != 1 else ''} as completed. Their certificates are unlocked."
          if rows else "Everyone who checked in is already marked complete.", "success" if rows else "info")
    return redirect(request.referrer or url_for("studio.event", eid=eid))


@bp.route("/api/draft", methods=["POST"])
def api_draft():
    data = request.get_json(silent=True) or {}
    e = None
    try:
        draft_eid = int(data.get("event_id") or 0)
    except (TypeError, ValueError):
        draft_eid = 0
    if draft_eid:
        if not staff_role(draft_eid):
            return jsonify(error="No access to that event."), 403
        e = q("SELECT * FROM events WHERE id=?", (draft_eid,), one=True)
    return jsonify(body=ai_agent.draft_announcement(data.get("title"), data.get("notes"), e), source=ai_agent.mode())


# ================================================================== team (hierarchy)
@bp.route("/team", methods=["GET", "POST"])
def team():
    college_only()
    me = g.user
    my_events = q("SELECT id, title, category FROM events WHERE college_id=? AND is_removed=0 ORDER BY start_dt DESC", (me["id"],))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lstrip("@")
        user = q("SELECT * FROM users WHERE username=? AND role='student' AND status='active'", (username,), one=True)
        role = "volunteer" if request.form.get("role") == "volunteer" else "lead"
        chosen = [int(x) for x in request.form.getlist("event_ids") if x.isdigit()]
        valid = {e["id"] for e in my_events}
        chosen = [x for x in chosen if x in valid]
        if not user:
            flash(f"No student account called @{username}. Ask them to sign up first.", "error")
        elif not chosen:
            flash("Tick at least one event they should manage.", "error")
        else:
            for eid in chosen:
                ex("INSERT INTO event_staff (event_id, user_id, role) VALUES (?,?,?) ON CONFLICT(event_id, user_id) DO UPDATE SET role=excluded.role",
                   (eid, user["id"], role))
            core.notify(user["id"], "team", f"{me['name']} added you as {'an event lead' if role == 'lead' else 'a volunteer'}.",
                        url_for("studio.overview"), me["id"])
            core.audit("team.add", f"@{username} as {role} on {chosen}")
            flash(f"@{username} is now {'an event lead' if role == 'lead' else 'a check-in volunteer'} for {len(chosen)} event(s).", "success")
        return redirect(url_for("studio.team"))
    c, a = in_clause([e["id"] for e in my_events])
    rows = q(f"""SELECT s.*, u.name, u.username, u.avatar, u.department, e.title FROM event_staff s JOIN users u ON u.id=s.user_id
                 JOIN events e ON e.id=s.event_id WHERE s.event_id IN {c} ORDER BY u.name""", a)
    members = {}
    for r in rows:
        m = members.setdefault(r["user_id"], {"user": r, "events": []})
        m["events"].append(r)
    return render_template("studio/team.html", members=list(members.values()), my_events=my_events, active="team")


@bp.route("/team/<int:uid>/remove", methods=["POST"])
def team_remove(uid):
    college_only()
    ex("DELETE FROM event_staff WHERE user_id=? AND event_id IN (SELECT id FROM events WHERE college_id=?)", (uid, g.user["id"]))
    core.audit("team.remove", f"user #{uid}")
    flash("Access removed.", "info")
    return redirect(url_for("studio.team"))


# ================================================================== posts & assistant
@bp.route("/posts")
def posts():
    college_only()
    rows = q("""SELECT p.*, e.title event_title,
                       (SELECT COUNT(*) FROM post_likes l WHERE l.post_id=p.id) likes,
                       (SELECT COUNT(*) FROM comments c WHERE c.post_id=p.id) comments,
                       (SELECT COUNT(*) FROM post_saves s WHERE s.post_id=p.id) saves,
                       (SELECT path FROM post_media m WHERE m.post_id=p.id AND m.kind='image' ORDER BY position LIMIT 1) thumb,
                       (SELECT COUNT(*) FROM post_media m WHERE m.post_id=p.id) media_count
                FROM posts p LEFT JOIN events e ON e.id=p.event_id WHERE p.author_id=? ORDER BY p.created_at DESC""", (g.user["id"],))
    return render_template("studio/posts.html", rows=rows, active="posts")


@bp.route("/assistant")
def assistant():
    if not staff_any_lead():
        abort(403)
    ids = lead_event_ids()
    c, a = in_clause(ids)
    my_events = q(f"SELECT id, title FROM events WHERE id IN {c} ORDER BY start_dt DESC", a)
    if g.user["role"] == "dev":
        unanswered = q("""SELECT c.*, u.name, e.title event_title FROM chat_logs c LEFT JOIN users u ON u.id=c.user_id
                          LEFT JOIN events e ON e.id=c.event_id WHERE c.answered=0 AND c.resolved=0 ORDER BY c.created_at DESC LIMIT 50""")
    else:
        unanswered = q(f"""SELECT c.*, u.name, e.title event_title FROM chat_logs c LEFT JOIN users u ON u.id=c.user_id
                           JOIN events e ON e.id=c.event_id WHERE c.answered=0 AND c.resolved=0 AND c.event_id IN {c}
                           ORDER BY c.created_at DESC LIMIT 50""", a)
    faqs = q(f"SELECT f.*, e.title event_title FROM faqs f JOIN events e ON e.id=f.event_id WHERE f.event_id IN {c}", a)
    total = scalar("SELECT COUNT(*) FROM chat_logs") or 0
    answered = scalar("SELECT COUNT(*) FROM chat_logs WHERE answered=1") or 0
    return render_template("studio/assistant.html", my_events=my_events, unanswered=unanswered, faqs=faqs,
                           stats={"total": total, "rate": (answered / total) if total else 1}, mode=ai_agent.mode(),
                           model=ai_agent.MODEL, active="assistant")


def _own_log(lid):
    log = q("SELECT * FROM chat_logs WHERE id=?", (lid,), one=True)
    if not log:
        return None
    if g.user["role"] == "dev" or (log["event_id"] and log["event_id"] in lead_event_ids()):
        return log
    return None


@bp.route("/assistant/<int:lid>/dismiss", methods=["POST"])
def dismiss(lid):
    if not _own_log(lid):
        abort(403)
    ex("UPDATE chat_logs SET resolved=1 WHERE id=?", (lid,))
    return redirect(url_for("studio.assistant"))
