"""College studio: the admin dashboard for a college's events (and for event leads it has invited)."""
import csv
import io
from datetime import datetime, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash, g, abort, jsonify, Response

import ai_agent
import core
import journey
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
    recent = q(f"""SELECT p.*, u.name, u.username, e.title FROM payments p JOIN users u ON u.id=p.user_id JOIN events e ON e.id=p.event_id
                   WHERE p.event_id IN {c} AND p.status IN ('paid','submitted') ORDER BY p.created_at DESC LIMIT 8""", a)
    return render_template("studio/overview.html", events=listed, kpis=kpis, charts=charts, recent=recent, active="overview")


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


@bp.route("/events/new", methods=["GET", "POST"])
def event_new():
    if g.user["role"] != "college":
        abort(403)
    if request.method == "POST":
        data, errors = _form()
        try:
            if not errors:
                _banner(data)
        except ValueError as err:
            errors.append(str(err))
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("studio/event_form.html", e=data, active="new")
        data["college_id"] = g.user["id"]
        is_fest = request.form.get("kind") == "fest"
        if is_fest:
            data["kind"] = "fest"
        cols = ",".join(data)
        eid = ex(f"INSERT INTO events ({cols}) VALUES ({','.join('?' * len(data))})", tuple(data.values()))
        if is_fest:
            if request.form.get("template"):
                festlib.apply_template(q("SELECT * FROM events WHERE id=?", (eid,), one=True))
                if not g.user["upi_id"]:
                    flash("Add your UPI ID in Settings → Payments so students can pay for the paid events.", "info")
            core.audit("fest.create", f"{data['title']} (#{eid})")
            flash("Fest created. Check the tracks and prices, then share a post about it.", "success")
            if data["status"] == "open":
                fol = [r[0] for r in q("SELECT follower_id FROM follows WHERE college_id=?", (g.user["id"],))]
                core.notify_many(fol, "event", f"{g.user['name']} opened registrations for {data['title']}.",
                                 url_for("events.detail", eid=eid), g.user["id"])
            return redirect(url_for("studio.event", eid=eid))
        if data["status"] == "open":
            fol = [r[0] for r in q("SELECT follower_id FROM follows WHERE college_id=?", (g.user["id"],))]
            core.notify_many(fol, "event", f"{g.user['name']} opened registrations for {data['title']}.",
                             url_for("events.detail", eid=eid), g.user["id"])
        core.audit("event.create", f"{data['title']} (#{eid})")
        flash("Event created. Add a schedule, then share a post about it.", "success")
        return redirect(url_for("studio.event", eid=eid))
    blank = {"category": "Technical", "status": "open", "capacity": 100, "fee": 0, "team_size": 1, "food_buffer_pct": 10,
             "meals_count": 1, "food_cost_per_head": 100, "city": g.user["city"]}
    return render_template("studio/event_form.html", e=blank, active="new")


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
            return render_template("studio/event_form.html", e=data, active=f"ev{eid}", **_sub_ctx(event))
        ex(f"UPDATE events SET {', '.join(k + '=?' for k in data)} WHERE id=?", tuple(data.values()) + (eid,))
        if event["kind"] == "fest":
            if data["status"] != event["status"]:  # sub-events that followed the fest follow it again
                ex("UPDATE events SET status=? WHERE parent_id=? AND status=?", (data["status"], eid, event["status"]))
            festlib.sync(eid)
        if event["parent_id"]:
            festlib.sync(event["parent_id"])
        if event["status"] != "open" and data["status"] == "open":
            fol = [r[0] for r in q("SELECT follower_id FROM follows WHERE college_id=?", (event["college_id"],))]
            core.notify_many(fol, "event", f"Registrations are open for {data['title']}.", url_for("events.detail", eid=eid))
        core.audit("event.edit", f"#{eid}")
        flash("Event saved.", "success")
        return redirect(url_for("studio.event", eid=eid))
    return render_template("studio/event_form.html", e=event, active=f"ev{eid}", **_sub_ctx(event))


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
    return render_template("studio/event.html", e=e, stats=stats, parent=parent, track=festlib.track_of(e), regs=regs, payments=payments, schedule=schedule,
                           coupons=coupons, news=news, faqs=faqs, rooms=rooms, staff=staff, role=role,
                           winners=winners, teams=teams, win_titles=core.WIN_TITLES, win_points=core.WIN_POINTS,
                           food=core.food_estimate(e, stats), default_start=core.iso(core.parse_dt(e["start_dt"])),
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
                           lead=role in ("owner", "dev", "lead"), active=f"ev{eid}", CATS=core.CATEGORIES)


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
    sid = festlib.add_sub(e, t["id"], title, category=f.get("category") or e["category"], fee=fee,
                          fee_type="team" if f.get("fee_type") == "team" and team > 1 else "person", team_size=team,
                          label=f.get("label"), capacity=cap)
    festlib.sync(eid)
    core.audit("fest.event", f"{e['title']}: {title} (#{sid})")
    flash(f"{title} added to {t['name']}.", "success")
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
                confirm(x["registration_id"], x["id"], g.user["id"])
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


@bp.route("/registrations/<int:rid>/slot", methods=["POST"])
def slot(rid):
    r = _reg(rid)
    need(r["event_id"], "lead")
    s, e = core.parse_dt(request.form.get("slot_start")), core.parse_dt(request.form.get("slot_end"))
    venue = (request.form.get("slot_venue") or "").strip()[:80] or None
    ex("UPDATE registrations SET slot_start=?, slot_end=?, slot_venue=? WHERE id=?", (core.iso(s), core.iso(e), venue, rid))
    if s and venue:
        title = scalar("SELECT title FROM events WHERE id=?", (r["event_id"],))
        core.notify(r["user_id"], "slot", f"Your slot for {title}: {s.strftime('%d %b, %I:%M %p')} in {venue}.",
                    url_for("events.ticket", code=r["pass_code"]))
    flash("Slot saved.", "success")
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
            flash("Registration restored.", "success")
    else:
        from blueprints.events import cancel_reg
        regs, refunds = cancel_reg(r, "Cancelled by organiser", g.user["id"])
        core.notify(r["user_id"], "event", f"Your registration for {title} was cancelled by the organiser."
                    + (" A refund will be sent to you." if refunds else ""), None, g.user["id"])
        flash("Registration cancelled." + (f" Also cancelled {len(regs) - 1} that depended on it." if len(regs) > 1 else "")
              + (f" Refund of ₹{refunds} marked as pending." if refunds else ""), "info")
    return back(r["event_id"], "registrations")


@bp.route("/events/<int:eid>/slots", methods=["POST"])
def slots(eid):
    need(eid, "lead")
    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True)
    start = core.parse_dt(request.form.get("slot_start"))
    try:
        duration = max(1, int(request.form.get("duration") or 10))
        gap = max(0, int(request.form.get("gap") or 0))
    except ValueError:
        duration, gap = 10, 0
    if not start:
        flash("Pick when the first slot starts.", "error")
        return back(eid, "slots")
    n, rooms, last_end = core.assign_slots(eid, start, duration, gap, (request.form.get("rooms") or "").split(","))
    if not n:
        flash("No confirmed participants to give slots to yet.", "error")
        return back(eid, "slots")
    for r in q("SELECT user_id, pass_code, slot_start, slot_venue FROM registrations WHERE event_id=? AND status='confirmed'", (eid,)):
        core.notify(r["user_id"], "slot", f"Your slot for {e['title']}: {core.parse_dt(r['slot_start']).strftime('%a %d %b, %I:%M %p')} "
                    f"in {r['slot_venue']}.", url_for("events.ticket", code=r["pass_code"]), e["college_id"])
    if request.form.get("announce"):
        body = (f"Personal slots are live for all {n} participants across {', '.join(rooms)}. "
                f"Slots run from {start.strftime('%a %d %b, %I:%M %p')} to {last_end.strftime('%I:%M %p')}, {duration} min each. "
                f"Open your ticket to see your exact time and room, and arrive 10 minutes early.")
        ex("INSERT INTO announcements (event_id, title, body, priority, kind, created_by) VALUES (?,?,?,?,?,?)",
           (eid, "Your slots are out", body, "important", "slot", g.user["id"]))
    flash(f"Gave {n} people a personal slot and notified each of them.", "success")
    return back(eid, "slots")


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


# ================================================================== check-in desk
@bp.route("/events/<int:eid>/checkin")
def checkin(eid):
    need(eid)
    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True) or abort(404)
    recent = q("""SELECT r.*, u.name, u.department, u.avatar, ev.title ev_title FROM registrations r JOIN users u ON u.id=r.user_id
                  JOIN events ev ON ev.id=r.event_id WHERE (ev.id=? OR ev.parent_id=?) AND r.attended=1
                  ORDER BY r.checkin_time DESC LIMIT 12""", (eid, eid))
    return render_template("studio/checkin.html", e=e, stats=core.event_stats(eid), recent=recent, active=f"ev{eid}",
                           stations=journey.STATIONS, station=request.args.get("station", ""))


@bp.route("/api/checkin", methods=["POST"])
def api_checkin():
    """One scan at a desk. The QR says which step it is (entry, completion, food); the order is enforced."""
    data = request.get_json(silent=True) or {}
    try:
        eid = int(data.get("event_id") or 0) or None
    except (TypeError, ValueError):
        eid = None
    res = journey.scan(data.get("code"), station=(data.get("station") or "").upper(), desk_event=eid,
                       can_staff=staff_role, actor_id=g.user["id"])
    if eid and staff_role(eid):
        s = core.event_stats(eid)
        res.update(attended=s["attended"], registered=s["registered"], completed=s["completed"], fed=s["fed"])
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
