"""Event pages, registration + payment, tickets, calendar, receipts, certificates."""
import calendar as cal
import re
from datetime import datetime, date, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash, g, abort, session, Response, jsonify

import core
import documents
import journey
import fest as festlib
from db import q, ex, scalar, in_clause
from blueprints.social import hydrate_posts, POST_SELECT, friends_going

bp = Blueprint("events", __name__)


# ================================================================== pricing
def coupon_uses(event_id, code, exclude_user=None):
    """Seats already claimed with a code: confirmed, under review or awaiting payment."""
    return scalar("""SELECT COUNT(*) FROM registrations WHERE event_id=? AND coupon_code=? COLLATE NOCASE
                     AND status IN ('confirmed','payment_review','pending_payment') AND user_id != ?""",
                  (event_id, code, exclude_user or 0))


def price_for(event, code=None):
    base = int(event["fee"] or 0)
    code = (code or "").strip().upper()
    res = {"base": base, "discount": 0, "total": base, "code": None, "valid": None, "message": ""}
    if not code:
        return res
    c = q("SELECT * FROM coupons WHERE event_id=? AND code=? AND active=1", (event["id"], code), one=True)
    if not c:
        res.update(valid=False, message="That code isn't valid for this event.")
    elif coupon_uses(event["id"], c["code"], g.user["id"] if g.get("user") else None) >= c["max_uses"]:
        res.update(valid=False, message="That code has been fully used.")
    else:
        disc = int(round(base * c["percent_off"] / 100))
        res.update(discount=disc, total=max(0, base - disc), code=c["code"], valid=True,
                   message=f"{c['percent_off']}% off applied.")
    return res


def load_event(eid, allow_draft=False):
    e = q("""SELECT e.*, u.name college, u.username college_username, u.avatar college_avatar, u.verification,
                    u.upi_id, u.city college_city
             FROM events e JOIN users u ON u.id=e.college_id WHERE e.id=?""", (eid,), one=True)
    if not e or (e["is_removed"] and not core.can_manage(g.user, eid)):
        abort(404)
    if e["status"] == "draft" and not (allow_draft or core.can_manage(g.user, eid)):
        abort(404)
    if e["parent_id"]:  # a fest's event is only public while the fest is
        parent = q("SELECT status, is_removed FROM events WHERE id=?", (e["parent_id"],), one=True)
        if (not parent or parent["is_removed"] or parent["status"] == "draft") and not core.can_manage(g.user, eid):
            abort(404)
    return e


def confirm(reg_id, payment_id=None, reviewer=None):
    """Mark a registration confirmed (and its payment paid). Notifies everyone involved."""
    reg = q("""SELECT r.*, e.title, e.college_id, e.fee FROM registrations r JOIN events e ON e.id=r.event_id WHERE r.id=?""",
            (reg_id,), one=True)
    if not reg:
        return
    ex("UPDATE registrations SET status='confirmed' WHERE id=?", (reg_id,))
    if payment_id:
        ex("UPDATE payments SET status='paid', reviewed_by=?, reviewed_at=? WHERE id=?",
           (reviewer, datetime.now().strftime("%Y-%m-%d %H:%M:%S") if reviewer else None, payment_id))
    pts = core.reg_points(q("SELECT * FROM registrations WHERE id=?", (reg_id,), one=True))
    core.notify(reg["user_id"], "event", f"You're in! Your ticket for {reg['title']} is ready. +{pts} points earned.",
                url_for("events.ticket", code=reg["pass_code"]), reg["college_id"])


def promote_waitlist(event_id):
    event = q("SELECT * FROM events WHERE id=?", (event_id,), one=True)
    if not event or core.seats_taken(event_id) >= event["capacity"]:
        return
    nxt = q("SELECT * FROM registrations WHERE event_id=? AND status='waitlisted' ORDER BY created_at, id LIMIT 1",
            (event_id,), one=True)
    if not nxt:
        return
    if event["fee"] == 0:
        ex("UPDATE registrations SET status='confirmed' WHERE id=?", (nxt["id"],))
        core.notify(nxt["user_id"], "event", f"A seat opened up. You're now confirmed for {event['title']}! +{core.POINTS_FREE_REG} points.",
                    url_for("events.ticket", code=nxt["pass_code"]), event["college_id"])
    else:
        ex("UPDATE registrations SET status='pending_payment', amount=?, coupon_code=NULL WHERE id=?", (event["fee"], nxt["id"]))
        receipt = core.gen_code("RCPT-", "payments", "receipt_no", 8)
        ex("""INSERT INTO payments (registration_id, user_id, event_id, receipt_no, base_amount, discount, amount, platform_fee, method, status)
              VALUES (?,?,?,?,?,0,?,?,'upi','created')""",
           (nxt["id"], nxt["user_id"], event_id, receipt, event["fee"], event["fee"], core.platform_fee(event["fee"])))
        link = url_for("events.checkout", receipt=receipt)
        core.notify(nxt["user_id"], "event", f"A seat opened up for {event['title']}. Complete payment to confirm it.",
                    link, event["college_id"])


def cancel_reg(reg, note, actor_id=None):
    """Cancel a registration plus anything that only existed because of it (a track pass or a team fee).
    Returns (cancelled registrations, total to refund)."""
    regs = [reg] + list(festlib.dependents(reg))
    refunds = 0
    for r in regs:
        ex("UPDATE registrations SET status='cancelled', slot_start=NULL, slot_end=NULL, slot_venue=NULL WHERE id=?", (r["id"],))
        pay = q("SELECT * FROM payments WHERE registration_id=? ORDER BY id DESC LIMIT 1", (r["id"],), one=True)
        if pay and pay["status"] == "paid" and pay["amount"] > 0:
            ex("UPDATE payments SET status='refunded', note=? WHERE id=?", (note, pay["id"]))
            refunds += pay["amount"]
        elif pay and pay["status"] in ("created", "submitted"):
            ex("UPDATE payments SET status='rejected', note=? WHERE id=?", (note, pay["id"]))
        if r["id"] != reg["id"]:
            title = scalar("SELECT title FROM events WHERE id=?", (r["event_id"],))
            core.notify(r["user_id"], "event", f"Your registration for {title} was cancelled because the "
                        f"{'pass' if reg['covers'] == 'pass' else 'team fee'} it relied on was cancelled.", "/tickets", actor_id)
    for ev in {r["event_id"] for r in regs}:
        promote_waitlist(ev)
    return regs, refunds


# ================================================================== event page
@bp.route("/e/<int:eid>")
def detail(eid):
    e = load_event(eid)
    seen = session.get("seen_events", [])
    if eid not in seen:
        ex("UPDATE events SET views=views+1 WHERE id=?", (eid,))
        session["seen_events"] = (seen + [eid])[-200:]
    taken = core.seats_taken(eid)
    is_open, reason = core.reg_state(e, taken)
    my_reg = None
    my_payment = None
    saved = False
    if g.user:
        my_reg = q("SELECT * FROM registrations WHERE event_id=? AND user_id=? AND status!='cancelled'", (eid, g.user["id"]), one=True)
        if my_reg:
            my_payment = q("SELECT * FROM payments WHERE registration_id=? ORDER BY id DESC LIMIT 1", (my_reg["id"],), one=True)
        saved = bool(scalar("SELECT 1 FROM event_saves WHERE event_id=? AND user_id=?", (eid, g.user["id"])))
    schedule = q("SELECT * FROM schedule_items WHERE event_id=? ORDER BY start_dt", (eid,))
    news = q("SELECT * FROM announcements WHERE event_id IN (?, ?) ORDER BY created_at DESC LIMIT 10", (eid, e["parent_id"] or 0))
    faqs = q("SELECT * FROM faqs WHERE event_id=?", (eid,))
    posts = hydrate_posts(q(f"{POST_SELECT} AND p.event_id=? ORDER BY p.created_at DESC LIMIT 9", (eid,)), g.user)
    going = friends_going(g.user, [eid]).get(eid, [])
    attendees = q("""SELECT u.name, u.username, u.avatar FROM registrations r JOIN users u ON u.id=r.user_id
                     WHERE r.event_id=? AND r.status='confirmed' AND u.show_events='everyone' ORDER BY r.created_at DESC LIMIT 8""", (eid,))
    more = q("""SELECT e.*, (SELECT COUNT(*) FROM registrations r WHERE r.event_id=e.id AND r.status IN ('confirmed','payment_review')) taken
                FROM events e WHERE e.college_id=? AND e.id!=? AND e.status IN ('open','closed') AND e.is_removed=0
                AND e.end_dt >= ? ORDER BY e.start_dt LIMIT 3""", (e["college_id"], eid, core.now_iso()))
    friends_list = []
    if g.user and g.user["role"] == "student":
        fids = core.friend_ids(g.user["id"])
        if fids:
            from db import in_clause
            c, a = in_clause(fids)
            friends_list = q(f"SELECT id, name, username, avatar FROM users WHERE id IN {c} AND status='active' ORDER BY name", a)
    waitlisted = scalar("SELECT COUNT(*) FROM registrations WHERE event_id=? AND status='waitlisted'", (eid,))
    if e["kind"] == "fest":
        return _fest_page(e, news=news, schedule=schedule, faqs=faqs, posts=posts, saved=saved, friends_list=friends_list,
                          following=core.is_following(g.user, e["college_id"]))
    parent, track = (festlib.get_fest(e), festlib.track_of(e)) if e["parent_id"] else (None, None)
    sub_quote = None
    if parent and g.user and g.user["role"] == "student" and not my_reg:
        held = festlib.holds_pass(g.user["id"], track["id"]) if track and track["pricing"] == "pass" else None
        sub_quote = {"pass": track and track["pricing"] == "pass", "held": held, "track": track,
                     "price": 0 if held else (track["pass_fee"] if track and track["pricing"] == "pass" else e["fee"])}
    return render_template("events/detail.html", parent=parent, track=track, sub_quote=sub_quote, e=e, taken=taken, is_open=is_open, reason=reason, my_reg=my_reg,
                           my_payment=my_payment, saved=saved, schedule=schedule, news=news, faqs=faqs, posts=posts,
                           going=going, attendees=attendees, more=more, can_manage=core.can_manage(g.user, eid),
                           friends_list=friends_list, waitlisted=waitlisted, winners=core.winner_groups(eid),
                           pts=core.points_summary(g.user["id"]) if g.user and g.user["role"] == "student" else None,
                           reg_pts=core.POINTS_PAID_REG if e["fee"] else core.POINTS_FREE_REG, max_pct=core.POINTS_MAX_PCT,
                           following=core.is_following(g.user, e["college_id"]))


def _fest_page(e, **ctx):
    me = g.user
    groups = festlib.structure(e["id"], me if me and me["role"] == "student" else None)
    subs = [it["e"] for gr in groups for it in gr["items"]]
    participants = scalar("""SELECT COUNT(DISTINCT r.user_id) FROM registrations r JOIN events s ON s.id=r.event_id
                             WHERE s.parent_id=? AND r.status IN ('confirmed','payment_review')""", (e["id"],))
    results = []
    for s in subs:
        w = core.winner_groups(s["id"])
        if w:
            results.append({"e": s, "winners": w})
    my_regs = [it for gr in groups for it in gr["items"] if it["mine"]]
    sel = {int(x) for x in (request.args.get("sel") or "").split(",") if x.isdigit()}
    going = friends_going(me, [s["id"] for s in subs])
    friends_at = {}
    for lst in going.values():
        for f in lst:
            friends_at[f["id"]] = f
    return render_template("events/fest.html", e=e, groups=groups, subs=subs, participants=participants, results=results,
                           my_regs=my_regs, sel=sel, going=list(friends_at.values()), can_manage=core.can_manage(me, e["id"]),
                           is_open=e["status"] == "open", pts=core.points_summary(me["id"]) if me and me["role"] == "student" else None,
                           **ctx)


@bp.route("/e/<int:eid>/calendar.ics")
def ics(eid):
    e = load_event(eid)
    return Response(core.ics_for(e, e["college"]), mimetype="text/calendar",
                    headers={"Content-Disposition": f"attachment; filename=event-{eid}.ics"})


# ================================================================== registration
@bp.route("/e/<int:eid>/register", methods=["POST"])
@core.login_required
def register(eid):
    e = load_event(eid)
    if e["kind"] == "fest" or e["parent_id"]:
        return join(e["parent_id"] or eid)
    me = g.user
    if me["role"] != "student":
        flash("Only student accounts can register for events.", "info")
        return redirect(url_for("events.detail", eid=eid))
    existing = q("SELECT * FROM registrations WHERE event_id=? AND user_id=?", (eid, me["id"]), one=True)
    if existing and existing["status"] in ("confirmed", "payment_review"):
        flash("You're already registered.", "info")
        return redirect(url_for("events.ticket", code=existing["pass_code"]))
    if existing and existing["status"] == "pending_payment":
        pay = q("SELECT receipt_no FROM payments WHERE registration_id=? ORDER BY id DESC LIMIT 1", (existing["id"],), one=True)
        if pay:
            return redirect(url_for("events.checkout", receipt=pay["receipt_no"]))
    food = request.form.get("food_pref") if request.form.get("food_pref") in ("veg", "nonveg", "none") else "veg"
    team = (request.form.get("team_name") or "").strip()[:60] or None
    if e["team_size"] > 1 and not team:
        flash("This is a team event. Enter your team name (teammates use the same one).", "error")
        return redirect(url_for("events.detail", eid=eid) + "#register")
    if team and e["team_size"] > 1:
        count = scalar("""SELECT COUNT(*) FROM registrations WHERE event_id=? AND team_name=? COLLATE NOCASE
                          AND status IN ('confirmed','payment_review') AND user_id!=?""", (eid, team, me["id"]))
        if count >= e["team_size"]:
            flash(f"Team “{team}” already has {e['team_size']} members.", "error")
            return redirect(url_for("events.detail", eid=eid) + "#register")
    is_open, reason = core.reg_state(e)
    join_waitlist = False
    if not is_open:
        if reason == "All seats are taken." and request.form.get("waitlist"):
            join_waitlist = True
        else:
            flash(reason, "error")
            return redirect(url_for("events.detail", eid=eid))
    price = price_for(e, request.form.get("coupon"))
    if price["valid"] is False:
        flash(price["message"], "error")
        return redirect(url_for("events.detail", eid=eid) + "#register")
    status = "waitlisted" if join_waitlist else ("confirmed" if price["total"] == 0 else "pending_payment")
    if existing:
        ex("""UPDATE registrations SET status=?, food_pref=?, team_name=?, amount=?, coupon_code=?, slot_start=NULL,
              slot_end=NULL, slot_venue=NULL, attended=0, checkin_time=NULL, created_at=datetime('now','localtime') WHERE id=?""",
           (status, food, team, price["total"], price["code"], existing["id"]))
        reg_id, code = existing["id"], existing["pass_code"]
    else:
        code = core.gen_code("EVF-", "registrations", "pass_code")
        reg_id = ex("""INSERT INTO registrations (event_id, user_id, pass_code, team_name, food_pref, status, amount, coupon_code)
                       VALUES (?,?,?,?,?,?,?,?)""", (eid, me["id"], code, team, food, status, price["total"], price["code"]))
    if join_waitlist:
        pos = scalar("SELECT COUNT(*) FROM registrations WHERE event_id=? AND status='waitlisted' AND id<=?", (eid, reg_id))
        flash(f"You're #{pos} on the waitlist. We'll notify you the moment a seat opens.", "success")
        return redirect(url_for("events.tickets", tab="waitlist"))
    receipt = core.gen_code("RCPT-", "payments", "receipt_no", 8)
    method = "free" if price["total"] == 0 else "upi"
    pay_id = ex("""INSERT INTO payments (registration_id, user_id, event_id, receipt_no, base_amount, discount, amount,
                                         platform_fee, method, status) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (reg_id, me["id"], eid, receipt, price["base"], price["discount"], price["total"],
                 core.platform_fee(price["total"]), method, "paid" if method == "free" else "created"))
    if status == "confirmed":
        confirm(reg_id)
        return redirect(url_for("events.success", code=code))
    return redirect(url_for("events.checkout", receipt=receipt))


PAY_SELECT = """SELECT p.*, r.pass_code, r.status reg_status, r.team_name, r.food_pref, r.covers, e.title, e.start_dt, e.venue,
                       e.banner, e.category, e.college_id, e.parent_id, e.capacity, u.name college, u.upi_id,
                       u.username college_username, f.title fest_title, f.banner fest_banner
                FROM payments p JOIN registrations r ON r.id=p.registration_id JOIN events e ON e.id=p.event_id
                JOIN users u ON u.id=e.college_id LEFT JOIN events f ON f.id=e.parent_id"""


# ================================================================== fest registration (several events, one payment)
@bp.route("/e/<int:eid>/join", methods=["POST"])
@core.login_required
def join(eid):
    f = load_event(eid)
    back = url_for("events.detail", eid=eid)
    if f["kind"] != "fest":
        abort(404)
    me = g.user
    if me["role"] != "student":
        flash("Only student accounts can register for events.", "info")
        return redirect(back)
    if f["status"] != "open":
        flash("Registration for this fest isn't open.", "error")
        return redirect(back)
    ids = []
    for v in request.form.getlist("pick"):
        try:
            ids.append(int(v))
        except ValueError:
            pass
    ids = list(dict.fromkeys(ids))[:30]
    if not ids:
        flash("Pick at least one event.", "error")
        return redirect(back + "#pick")
    c, a = in_clause(ids)
    subs = {s["id"]: s for s in q(f"SELECT * FROM events WHERE id IN {c} AND parent_id=? AND is_removed=0 AND status!='draft'", a + [eid])}
    picks = [(subs[i], (request.form.get(f"team_{i}") or "").strip()[:60]) for i in ids if i in subs]
    items, errors = festlib.quote(f, picks, me)
    sel = "?sel=" + ",".join(str(i) for i in ids)
    if errors:
        for msg in errors[:3]:
            flash(msg, "error")
        return redirect(back + sel + "#pick")
    if not items:
        flash("Pick at least one event.", "error")
        return redirect(back + "#pick")
    food = request.form.get("food_pref") if request.form.get("food_pref") in ("veg", "nonveg", "none") else "veg"
    total = sum(i["amount"] for i in items)
    status = "confirmed" if total == 0 else "pending_payment"
    bundle = core.gen_code("ORD-", "payments", "bundle", 8)
    first = None
    for it in items:
        ev = it["event"]
        existing = q("SELECT * FROM registrations WHERE event_id=? AND user_id=?", (ev["id"], me["id"]), one=True)
        if existing:
            ex("""UPDATE registrations SET status=?, food_pref=?, team_name=?, amount=?, coupon_code=NULL, covers=?, slot_start=NULL,
                  slot_end=NULL, slot_venue=NULL, attended=0, checkin_time=NULL, created_at=datetime('now','localtime') WHERE id=?""",
               (status, food, it["team"], it["amount"], it["covers"], existing["id"]))
            rid, code = existing["id"], existing["pass_code"]
        else:
            code = core.gen_code("EVF-", "registrations", "pass_code")
            rid = ex("""INSERT INTO registrations (event_id, user_id, pass_code, team_name, food_pref, status, amount, covers)
                        VALUES (?,?,?,?,?,?,?,?)""", (ev["id"], me["id"], code, it["team"], food, status, it["amount"], it["covers"]))
        receipt = core.gen_code("RCPT-", "payments", "receipt_no", 8)
        pid = ex("""INSERT INTO payments (registration_id, user_id, event_id, receipt_no, base_amount, discount, amount, platform_fee,
                                          method, status, note, bundle) VALUES (?,?,?,?,?,0,?,?,?,?,?,?)""",
                 (rid, me["id"], ev["id"], receipt, it["amount"], it["amount"], core.platform_fee(it["amount"]),
                  "free" if total == 0 else "upi", "paid" if total == 0 else "created", None, bundle))
        if status == "confirmed":
            confirm(rid)
        first = first or (receipt, code)
    if status == "confirmed":
        return redirect(url_for("events.success", code=first[1]))
    return redirect(url_for("events.checkout", receipt=first[0]))


def _payment(receipt):
    p = q(PAY_SELECT + " WHERE p.receipt_no=?", (receipt,), one=True)
    if not p:
        abort(404)
    if not g.user or (p["user_id"] != g.user["id"] and not core.can_manage(g.user, p["event_id"])):
        abort(403)
    return p


def _items(p):
    """Every payment row paid together with p (a fest order), oldest first."""
    if not p["bundle"]:
        return [p]
    return q(PAY_SELECT + " WHERE p.bundle=? AND p.user_id=? ORDER BY p.id", (p["bundle"], p["user_id"]))


def _live(items):
    return [i for i in items if i["status"] not in ("paid", "refunded") and i["reg_status"] in ("pending_payment", "payment_review")]


def _payable(items):
    return [i for i in items if i["status"] in ("created", "rejected") and i["reg_status"] == "pending_payment"]


def _points_room(items):
    """(points currently applied, most points this order may use)."""
    applied = sum(i["points_used"] for i in items)
    ids = [i["id"] for i in items]
    earned = core.points_summary(items[0]["user_id"])["earned"]
    spent_elsewhere = scalar(f"""SELECT COALESCE(SUM(points_used),0) FROM payments WHERE user_id=? AND status IN ('created','submitted','paid')
                                 AND id NOT IN ({",".join("?" * len(ids))})""", [items[0]["user_id"]] + ids) or 0
    cap = sum(core.max_points_for(i["base_amount"] - i["discount"], 10 ** 9) for i in items)
    return applied, max(0, min(earned - spent_elsewhere, cap))


def _set_points(p, points):
    amount = max(0, p["base_amount"] - p["discount"] - points)
    ex("UPDATE payments SET points_used=?, amount=?, platform_fee=? WHERE id=?", (points, amount, core.platform_fee(amount), p["id"]))
    ex("UPDATE registrations SET amount=? WHERE id=?", (amount, p["registration_id"]))


def _spread_points(items, points):
    """Share a points discount across an order, at most half of each item."""
    for i in items:
        give = min(points, core.max_points_for(i["base_amount"] - i["discount"], 10 ** 9))
        _set_points(i, give)
        points -= give


def _points_still_valid(items):
    """A resubmitted order may carry points that were spent elsewhere meanwhile: drop them and ask to re-check."""
    applied, room = _points_room(items)
    if applied <= room:
        return True
    _spread_points(items, 0)
    flash("Your points balance changed, so the points discount was removed. Check the new total and pay again.", "info")
    return False


def _order_title(items):
    if len(items) > 1 and items[0]["fest_title"]:
        return f"{items[0]['fest_title']} ({len(items)} events)"
    return items[0]["title"]


@bp.route("/checkout/<receipt>")
@core.login_required
def checkout(receipt):
    p = _payment(receipt)
    items = _items(p)
    live = _live(items)
    if not live:
        if p["reg_status"] == "cancelled":
            flash("This registration was cancelled. Register again to get a new ticket.", "info")
        return redirect(url_for("events.ticket", code=p["pass_code"]))
    pts = core.points_summary(g.user["id"]) if p["user_id"] == g.user["id"] else None
    applied, usable = (0, 0)
    if pts:
        applied, usable = _points_room(live)
        if applied > usable:  # points were spent elsewhere since: drop them from this order
            _spread_points(live, 0)
            return redirect(url_for("events.checkout", receipt=receipt))
    total = sum(i["amount"] for i in live)
    head = live[0]
    status = "submitted" if any(i["status"] == "submitted" for i in live) else ("rejected" if any(i["status"] == "rejected" for i in live) else "created")
    order = {"title": _order_title(live), "total": total, "base": sum(i["base_amount"] for i in live),
             "discount": sum(i["discount"] for i in live), "points": applied, "status": status, "receipt": head["receipt_no"],
             "code": head["bundle"] or head["receipt_no"], "utr": head["utr"], "note": next((i["note"] for i in live if i["note"]), None),
             "banner": head["fest_banner"] or head["banner"], "college": head["college"], "upi_id": head["upi_id"],
             "start_dt": head["start_dt"], "count": len(live)}
    upi = None
    if head["upi_id"] and total:
        upi = core.upi_link(head["upi_id"], head["college"], total, f"{order['title'][:40]} {order['code']}")
    return render_template("events/checkout.html", p=head, order=order, items=live, upi=upi, demo=core.setting("demo_payments") == "1",
                           pts=pts, usable=0 if applied else usable, max_pct=core.POINTS_MAX_PCT, paid_pts=core.POINTS_PAID_REG)


@bp.route("/checkout/<receipt>/points", methods=["POST"])
@core.login_required
def use_points(receipt):
    p = _payment(receipt)
    items = _payable(_items(p))
    if p["user_id"] != g.user["id"] or not items:
        flash("Points can't be changed on this order now.", "info")
        return redirect(url_for("events.checkout", receipt=receipt))
    if request.form.get("remove"):
        _spread_points(items, 0)
        flash("Points removed. They're back in your balance.", "info")
    else:
        _, room = _points_room(items)
        if room <= 0:
            flash("You don't have points to use on this order yet.", "info")
        else:
            _spread_points(items, room)
            flash(f"{room} points applied. You save ₹{room * core.POINT_VALUE}.", "success")
    return redirect(url_for("events.checkout", receipt=receipt))


def _seats_ok(items):
    for i in items:
        if core.seats_taken(i["event_id"]) >= i["capacity"]:
            flash(f"Sorry, the last seat in {i['title']} was just taken. Remove it and try again.", "error")
            return False
    return True


@bp.route("/checkout/<receipt>/upi", methods=["POST"])
@core.login_required
def pay_upi(receipt):
    p = _payment(receipt)
    items = _payable(_items(p))
    if p["user_id"] != g.user["id"] or not items:
        flash("This payment can't be changed now.", "info")
        return redirect(url_for("events.ticket", code=p["pass_code"]))
    if not _seats_ok(items):
        return redirect(url_for("events.detail", eid=items[0]["parent_id"] or items[0]["event_id"]))
    if not _points_still_valid(items):
        return redirect(url_for("events.checkout", receipt=receipt))
    utr = re.sub(r"\s", "", request.form.get("utr") or "")
    if not re.fullmatch(r"\d{12}", utr):
        flash("The UTR / UPI reference is the 12-digit number in your payment app.", "error")
        return redirect(url_for("events.checkout", receipt=receipt))
    ids = [i["id"] for i in items]
    if scalar(f"SELECT 1 FROM payments WHERE utr=? AND id NOT IN ({','.join('?' * len(ids))})", [utr] + ids):
        flash("That UTR was already used for another payment.", "error")
        return redirect(url_for("events.checkout", receipt=receipt))
    shot = None
    f = request.files.get("screenshot")
    if f and f.filename:
        try:
            shot = core.save_upload(f, allowed=("image",), max_mb=10)["path"]
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("events.checkout", receipt=receipt))
    for i in items:
        ex("UPDATE payments SET status='submitted', method='upi', utr=?, screenshot=COALESCE(?, screenshot), note=NULL WHERE id=?",
           (utr, shot, i["id"]))
        ex("UPDATE registrations SET status='payment_review' WHERE id=?", (i["registration_id"],))
    total = sum(i["amount"] for i in items)
    staff = {items[0]["college_id"]} | {r[0] for i in items for r in q("SELECT user_id FROM event_staff WHERE event_id IN (?, ?)",
                                                                       (i["event_id"], i["parent_id"] or 0))}
    core.notify_many(list(staff), "payment", f"{g.user['name']} paid ₹{total} for {_order_title(items)}. Verify the UTR.",
                     url_for("studio.payments", eid=items[0]["event_id"]), g.user["id"])
    flash("Payment submitted. Your seats are held while the college verifies it, usually within a few hours.", "success")
    return redirect(url_for("events.tickets", tab="pending") if len(items) > 1 else url_for("events.ticket", code=p["pass_code"]))


@bp.route("/checkout/<receipt>/demo", methods=["POST"])
@core.login_required
def pay_demo(receipt):
    p = _payment(receipt)
    if core.setting("demo_payments") != "1":
        abort(404)
    items = _payable(_items(p))
    if p["user_id"] != g.user["id"] or not items:
        return redirect(url_for("events.ticket", code=p["pass_code"]))
    if not _seats_ok(items):
        return redirect(url_for("events.detail", eid=items[0]["parent_id"] or items[0]["event_id"]))
    if not _points_still_valid(items):
        return redirect(url_for("events.checkout", receipt=receipt))
    ref = "DEMO" + datetime.now().strftime("%H%M%S%f")[:8]
    for i in items:
        ex("UPDATE payments SET method='demo', utr=? WHERE id=?", (ref, i["id"]))
        confirm(i["registration_id"], i["id"])
    return redirect(url_for("events.success", code=items[0]["pass_code"]))


@bp.route("/success/<code>")
@core.login_required
def success(code):
    reg = _reg(code)
    pay = q("SELECT * FROM payments WHERE registration_id=? ORDER BY id DESC LIMIT 1", (reg["id"],), one=True)
    group = [reg]
    if pay and pay["bundle"]:
        codes = [r[0] for r in q("""SELECT r.pass_code FROM payments p JOIN registrations r ON r.id=p.registration_id
                                    WHERE p.bundle=? AND p.user_id=? AND r.status='confirmed' ORDER BY p.id""", (pay["bundle"], reg["user_id"]))]
        group = [_reg(c) for c in codes] or [reg]
    earned = sum(core.reg_points(r) for r in group if r["status"] == "confirmed")
    fest_row = q("SELECT id, title FROM events WHERE id=?", (reg["parent_id"],), one=True) if reg["parent_id"] else None
    return render_template("events/success.html", reg=reg, group=group, fest=fest_row, earned=earned,
                           summary=core.points_summary(reg["user_id"]))


# ================================================================== tickets
def _reg(code):
    r = q("""SELECT r.*, e.title, e.category, e.venue, e.venue_details, e.start_dt, e.end_dt, e.status event_status,
                    e.banner, e.college_id, e.map_url, e.fee, e.parent_id, e.label, f.title fest_title,
                    u.name, u.username, u.department, u.year, u.college_name, u.avatar,
                    c.name college, c.username college_username, c.avatar college_avatar, c.cert_logo, c.cert_signature,
                    c.cert_signatory, c.cert_signatory_title
             FROM registrations r JOIN events e ON e.id=r.event_id JOIN users u ON u.id=r.user_id JOIN users c ON c.id=e.college_id
             LEFT JOIN events f ON f.id=e.parent_id
             WHERE r.pass_code=?""", (code,), one=True)
    if not r:
        abort(404)
    if not g.user or (r["user_id"] != g.user["id"] and not core.can_manage(g.user, r["event_id"])):
        abort(403)
    return r


@bp.route("/tickets")
@core.login_required
def tickets():
    me = g.user
    view = request.args.get("view", "list")
    tab = request.args.get("tab", "upcoming")
    rows = q("""SELECT r.*, e.title, e.category, e.venue, e.start_dt, e.end_dt, e.status event_status, e.banner,
                       c.name college, c.username college_username, (SELECT f.title FROM events f WHERE f.id=e.parent_id) fest_title,
                       (SELECT receipt_no FROM payments p WHERE p.registration_id=r.id ORDER BY p.id DESC LIMIT 1) receipt_no,
                       (SELECT status FROM payments p WHERE p.registration_id=r.id ORDER BY p.id DESC LIMIT 1) pay_status
                FROM registrations r JOIN events e ON e.id=r.event_id JOIN users c ON c.id=e.college_id
                WHERE r.user_id=? AND r.status!='cancelled' ORDER BY e.start_dt""", (me["id"],))
    nowi = core.now_iso()
    groups = {
        "upcoming": [r for r in rows if r["status"] == "confirmed" and r["end_dt"] >= nowi and r["event_status"] != "completed"],
        "pending": [r for r in rows if r["status"] in ("pending_payment", "payment_review")],
        "waitlist": [r for r in rows if r["status"] == "waitlisted"],
        "past": [r for r in rows if r["status"] == "confirmed" and (r["end_dt"] < nowi or r["event_status"] == "completed")],
    }
    groups["past"].reverse()
    # month calendar
    month = request.args.get("month")
    try:
        first = datetime.strptime(month, "%Y-%m").date() if month else date.today().replace(day=1)
    except ValueError:
        first = date.today().replace(day=1)
    weeks = cal.Calendar(firstweekday=0).monthdatescalendar(first.year, first.month)
    by_day = {}
    for r in rows:
        if r["status"] in ("confirmed", "payment_review", "pending_payment"):
            by_day.setdefault(r["start_dt"][:10], []).append(r)
    prev_m = (first - timedelta(days=1)).replace(day=1).strftime("%Y-%m")
    next_m = (first + timedelta(days=32)).replace(day=1).strftime("%Y-%m")
    return render_template("events/tickets.html", groups=groups, tab=tab, view=view, weeks=weeks, by_day=by_day,
                           first=first, prev_m=prev_m, next_m=next_m, today=date.today())


@bp.route("/ticket/<code>")
@core.login_required
def ticket(code):
    reg = _reg(code)
    pay = q("SELECT * FROM payments WHERE registration_id=? ORDER BY id DESC LIMIT 1", (reg["id"],), one=True)
    news = q("SELECT * FROM announcements WHERE event_id=? ORDER BY created_at DESC LIMIT 3", (reg["event_id"],))
    waitpos = None
    if reg["status"] == "waitlisted":
        waitpos = scalar("SELECT COUNT(*) FROM registrations WHERE event_id=? AND status='waitlisted' AND id<=?",
                         (reg["event_id"], reg["id"]))
    win = q("SELECT * FROM event_winners WHERE event_id=? AND user_id=?", (reg["event_id"], reg["user_id"]), one=True)
    steps = journey.steps(reg["user_id"], journey.scope_id(reg)) if reg["status"] == "confirmed" else []
    current = next((s for s in steps if s["state"] == "current"), None)
    busy = q("""SELECT * FROM busy_times WHERE user_id=? AND (event_id IS NULL OR event_id=?) ORDER BY start_dt""",
             (reg["user_id"], reg["event_id"]))
    return render_template("events/ticket.html", reg=reg, pay=pay, news=news, waitpos=waitpos, win=win, steps=steps,
                           current=current, journey_sig=journey.signature(steps), busy=busy)


@bp.route("/ticket/<code>/busy", methods=["POST"])
@core.login_required
def busy_add(code):
    """A student marks a time they can't make; the slot planner works around it."""
    reg = _reg(code)
    if reg["user_id"] != g.user["id"]:
        abort(403)
    s, e = core.parse_dt(request.form.get("start")), core.parse_dt(request.form.get("end"))
    if not s or not e or e <= s:
        flash("Pick a start and an end time (the end after the start).", "error")
    elif (e - s).days > 3:
        flash("Busy times can be at most 3 days long.", "error")
    elif (scalar("SELECT COUNT(*) FROM busy_times WHERE user_id=?", (g.user["id"],)) or 0) >= 30:
        flash("You already have 30 busy times. Remove some first.", "error")
    else:
        ex("INSERT INTO busy_times (user_id, event_id, start_dt, end_dt, note) VALUES (?,?,?,?,?)",
           (g.user["id"], reg["event_id"], core.iso(s), core.iso(e), (request.form.get("note") or "").strip()[:80] or None))
        flash("Saved. When the organisers plan slots, you won't get one in that time.", "success")
    return redirect(url_for("events.ticket", code=code) + "#busy")


@bp.route("/ticket/<code>/busy/<int:bid>/delete", methods=["POST"])
@core.login_required
def busy_delete(code, bid):
    reg = _reg(code)
    if reg["user_id"] != g.user["id"]:
        abort(403)
    ex("DELETE FROM busy_times WHERE id=? AND user_id=?", (bid, g.user["id"]))
    return redirect(url_for("events.ticket", code=code) + "#busy")


@bp.route("/api/journey/<code>")
@core.login_required
def journey_state(code):
    """Polled by the ticket page: when a volunteer scans a step, the page refreshes to show the next QR."""
    reg = _reg(code)
    steps = journey.steps(reg["user_id"], journey.scope_id(reg)) if reg["status"] == "confirmed" else []
    return jsonify(sig=journey.signature(steps), status=reg["status"])


@bp.route("/ticket/<code>/cancel", methods=["POST"])
@core.login_required
def cancel(code):
    reg = _reg(code)
    if reg["user_id"] != g.user["id"]:
        abort(403)
    if reg["attended"] or reg["event_status"] == "completed":
        flash("You can't cancel after attending.", "error")
        return redirect(url_for("events.ticket", code=code))
    regs, refunds = cancel_reg(reg, "Cancelled by participant")
    msg = f"Registration for {reg['title']} cancelled."
    if len(regs) > 1:
        msg = f"Cancelled {len(regs)} registrations ({reg['title']} and the events that depended on it)."
    if refunds:
        msg += f" A refund of ₹{refunds} has been requested from {reg['college']}."
        core.notify(reg["college_id"], "payment", f"{g.user['name']} cancelled {reg['title']}. Refund ₹{refunds} via UPI.",
                    url_for("studio.payments", eid=reg["event_id"]), g.user["id"])
    flash(msg, "info")
    return redirect(url_for("events.tickets"))


@bp.route("/receipt/<receipt>")
@core.login_required
def receipt(receipt):
    p = _payment(receipt)
    user = q("SELECT * FROM users WHERE id=?", (p["user_id"],), one=True)
    return render_template("events/receipt.html", p=p, user=user, items=_items(p))


@bp.route("/receipt/<receipt>/receipt.pdf")
@core.login_required
def receipt_pdf(receipt):
    p = _payment(receipt)
    user = q("SELECT * FROM users WHERE id=?", (p["user_id"],), one=True)
    college = q("SELECT name, upi_id, avatar, cert_logo FROM users WHERE id=?", (p["college_id"],), one=True)
    items = _items(p)

    def when(v):
        d = core.parse_dt(v)
        return d.strftime("%d %b %Y, %I:%M %p") if d else ""
    rows = []
    for i in items:
        sub = (f"{i['fest_title']} · " if i["fest_title"] else "") + f"Ticket {i['pass_code']}" + \
            (f" · Team {i['team_name']}" if i["team_name"] else "") + (" · " + when(i["start_dt"]) if i["start_dt"] else "")
        rows.append((i["title"], sub, i["base_amount"], i["discount"], i["points_used"], i["amount"]))
    data = {"receipt_no": p["receipt_no"], "status": p["status"], "created_at": when(p["created_at"]),
            "paid_at": when(p["reviewed_at"]) if p["status"] == "paid" and p["reviewed_at"] else "",
            "method": p["method"], "utr": p["utr"], "college": college["name"], "college_upi": college["upi_id"],
            "college_logo": core.media_bytes(college["cert_logo"] or college["avatar"]),
            "buyer": user["name"], "buyer_username": user["username"], "buyer_email": user["email"], "items": rows,
            "total": sum(r[5] for r in rows), "base_total": sum(r[2] for r in rows),
            "discount_total": sum(r[3] or 0 for r in rows), "points_total": sum(r[4] or 0 for r in rows),
            "note": p["note"], "verify_url": url_for("events.verify", code=p["pass_code"], _external=True),
            "ticket_codes": [i["pass_code"] for i in items]}
    resp = Response(documents.receipt_pdf(data), mimetype="application/pdf")
    resp.headers["Content-Disposition"] = f'attachment; filename="EventFlow-Receipt-{p["receipt_no"]}.pdf"'
    return resp


@bp.route("/certificate/<code>")
@core.login_required
def certificate(code):
    reg = _reg(code)
    reg, win = _cert_reg(code)
    if not reg:
        flash("Your certificate unlocks once you complete the event (the completion QR on your ticket is scanned).", "info")
        return redirect(url_for("events.ticket", code=code))
    return render_template("events/certificate.html", reg=reg, win=win,
                           verify_url=url_for("events.verify", code=code, _external=True))


def _cert_reg(code):
    reg = _reg(code)
    win = q("SELECT * FROM event_winners WHERE event_id=? AND user_id=?", (reg["event_id"], reg["user_id"]), one=True)
    if reg["status"] != "confirmed" or not (reg["completed_at"] or win):  # winners get one even without a scan
        return None, None
    return reg, win


def _cert_png(reg, win):
    def nice_date(v, fmt):
        d = core.parse_dt(v)
        return d.strftime(fmt).lstrip("0") if d else ""
    data = {
        "name": reg["name"], "kind": "achievement" if win else "participation", "college": reg["college"],
        "college_name": reg["college_name"] or "", "department": reg["department"] or "", "event": reg["title"],
        "category": reg["category"], "fest": reg["fest_title"] or "", "date": nice_date(reg["start_dt"], "%d %B %Y"),
        "venue": reg["venue"], "team": reg["team_name"] or "", "award": win["title"] if win else "",
        "position": win["position"] if win else 0, "code": reg["pass_code"],
        "issued": nice_date((win["created_at"] if win else reg["completed_at"]) or reg["start_dt"], "%d %b %Y"),
        "verify_url": url_for("events.verify", code=reg["pass_code"], _external=True),
        "logo": core.media_bytes(reg["cert_logo"] or reg["college_avatar"]), "signature": core.media_bytes(reg["cert_signature"]),
        "signatory": reg["cert_signatory"] or "", "signatory_title": reg["cert_signatory_title"] or "",
    }
    return documents.certificate_png(data)


@bp.route("/certificate/<code>/certificate.png")
@core.login_required
def certificate_png(code):
    reg, win = _cert_reg(code)
    if not reg:
        abort(404)
    png = _cert_png(reg, win)
    if request.args.get("preview"):
        resp = Response(documents.png_preview(png), mimetype="image/jpeg")
        resp.headers["Cache-Control"] = "private, max-age=300"
        return resp
    resp = Response(png, mimetype="image/png")
    resp.headers["Content-Disposition"] = f'attachment; filename="EventFlow-Certificate-{reg["pass_code"]}.png"'
    return resp


@bp.route("/certificate/<code>/certificate.pdf")
@core.login_required
def certificate_pdf(code):
    reg, win = _cert_reg(code)
    if not reg:
        abort(404)
    pdf = documents.certificate_pdf(_cert_png(reg, win), f"Certificate · {reg['title']} · {reg['name']}")
    resp = Response(pdf, mimetype="application/pdf")
    resp.headers["Content-Disposition"] = f'attachment; filename="EventFlow-Certificate-{reg["pass_code"]}.pdf"'
    return resp


@bp.route("/verify/<code>")
def verify(code):
    reg = q("""SELECT r.pass_code, r.attended, r.status, r.checkin_time, r.completed_at, e.title, e.start_dt, u.name, u.college_name,
                      c.name college, w.title win_title, w.position win_position
               FROM registrations r JOIN events e ON e.id=r.event_id JOIN users u ON u.id=r.user_id
               JOIN users c ON c.id=e.college_id LEFT JOIN event_winners w ON w.event_id=r.event_id AND w.user_id=r.user_id
               WHERE r.pass_code=?""", (code,), one=True)
    return render_template("events/verify.html", reg=reg, code=code,
                           valid=bool(reg and (reg["completed_at"] or reg["win_title"]) and reg["status"] == "confirmed"))
