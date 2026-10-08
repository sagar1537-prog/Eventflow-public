"""Event data editor: full control over every row that belongs to an organiser's own event (and nobody else's).

The college that owns an event, its event leads and the developer can view, edit, add, delete and export:
the event itself (and a fest's own events), registrations, payments, schedule, coupons, FAQs, updates, winners and
participants' busy times. Every field is validated against its type and allowed values, every row is checked to
belong to this event before it is touched, and every change goes into the audit log.
"""
import csv
import io

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, Response

import core
import scheduler
from db import q, ex, scalar, in_clause
from blueprints.studio import need

bp = Blueprint("data", __name__, url_prefix="/studio/events/<int:eid>/data")

# field: (column, label, type, options)   types: ro, text, area, int, float, choice, bool, dt
CHOICES = {
    "status_ev": ["draft", "open", "closed", "completed"],
    "status_reg": ["pending_payment", "payment_review", "confirmed", "waitlisted", "cancelled"],
    "food": ["veg", "nonveg", "none"],
    "method": ["upi", "demo", "free"],
    "status_pay": ["created", "submitted", "paid", "rejected", "refunded"],
    "priority": ["normal", "important"],
    "kind_ann": ["general", "slot"],
    "fee_type": ["person", "team"],
}

TABLES = {
    "events": {
        "label": "Event details", "icon": "🎪", "scope": "id", "add": False, "delete": False,
        "list": ["title", "status", "start_dt", "capacity", "fee"],
        "fields": [("title", "Title", "text", 120), ("label", "Short label", "text", 40),
                   ("category", "Category", "choice", list(core.CATEGORIES)), ("status", "Status", "choice", CHOICES["status_ev"]),
                   ("tagline", "Tagline", "text", 160), ("description", "Description", "area", 5000),
                   ("venue", "Venue", "text", 120), ("venue_details", "Venue details", "text", 200), ("city", "City", "text", 60),
                   ("map_url", "Map link", "text", 300), ("start_dt", "Starts", "dt", True), ("end_dt", "Ends", "dt", True),
                   ("reg_deadline", "Registration deadline", "dt", False), ("capacity", "Capacity", "int", (1, 100000)),
                   ("fee", "Fee (₹)", "int", (0, 1000000)), ("fee_type", "Fee is per", "choice", CHOICES["fee_type"]),
                   ("team_size", "Team size", "int", (1, 50)), ("meals_count", "Meals", "int", (0, 20)),
                   ("food_cost_per_head", "Food cost per head (₹)", "float", (0, 100000)),
                   ("food_buffer_pct", "Food buffer %", "float", (0, 100))]},
    "registrations": {
        "label": "Registrations", "icon": "🎟️", "scope": "event_id", "add": True, "delete": True, "user": True,
        "list": ["_user", "status", "team_name", "slot_start", "attended", "completed_at"],
        "fields": [("pass_code", "Ticket code", "ro", None), ("status", "Status", "choice", CHOICES["status_reg"]),
                   ("team_name", "Team", "text", 60), ("food_pref", "Food", "choice", CHOICES["food"]),
                   ("amount", "Amount (₹)", "int", (0, 1000000)), ("coupon_code", "Coupon", "text", 30),
                   ("slot_start", "Slot starts (change it under Slots)", "ro", None), ("slot_end", "Slot ends", "ro", None),
                   ("slot_venue", "Slot room", "ro", None), ("slot_locked", "Slot locked (re-allocating keeps it)", "bool", None),
                   ("attended", "Entered (checked in)", "bool", None), ("checkin_time", "Entry time", "dt", False),
                   ("slot_in_at", "Slot check-in time", "dt", False), ("completed_at", "Completed at (certificate)", "dt", False),
                   ("food_at", "Meal served at", "dt", False), ("created_at", "Registered at", "ro", None)]},
    "payments": {
        "label": "Payments", "icon": "💳", "scope": "event_id", "add": False, "delete": True, "user": True,
        "list": ["receipt_no", "_user", "amount", "status", "utr"],
        "fields": [("receipt_no", "Receipt", "ro", None), ("base_amount", "Price (₹)", "int", (0, 1000000)),
                   ("discount", "Discount (₹)", "int", (0, 1000000)), ("points_used", "Points used", "int", (0, 1000000)),
                   ("amount", "Paid (₹)", "int", (0, 1000000)), ("platform_fee", "Platform fee (₹)", "int", (0, 1000000)),
                   ("method", "Method", "choice", CHOICES["method"]), ("status", "Status", "choice", CHOICES["status_pay"]),
                   ("utr", "UPI reference (UTR)", "text", 40), ("note", "Note", "text", 300), ("created_at", "Created", "ro", None)]},
    "schedule_items": {
        "label": "Schedule", "icon": "🗓️", "scope": "event_id", "add": True, "delete": True,
        "list": ["title", "start_dt", "venue"],
        "fields": [("title", "Title", "text", 120), ("start_dt", "Starts", "dt", True), ("end_dt", "Ends", "dt", False),
                   ("venue", "Venue", "text", 120), ("description", "Description", "area", 2000)]},
    "coupons": {
        "label": "Coupons", "icon": "🏷️", "scope": "event_id", "add": True, "delete": True,
        "list": ["code", "percent_off", "used", "max_uses", "active"],
        "fields": [("code", "Code", "text", 30), ("percent_off", "% off", "int", (1, 100)), ("max_uses", "Max uses", "int", (1, 100000)),
                   ("used", "Used", "int", (0, 100000)), ("active", "Active", "bool", None)]},
    "faqs": {
        "label": "FAQs", "icon": "❓", "scope": "event_id", "add": True, "delete": True,
        "list": ["question", "answer"],
        "fields": [("question", "Question", "text", 300), ("answer", "Answer", "area", 2000)]},
    "announcements": {
        "label": "Updates", "icon": "📣", "scope": "event_id", "add": True, "delete": True,
        "list": ["title", "priority", "created_at"],
        "fields": [("title", "Title", "text", 120), ("body", "Message", "area", 3000),
                   ("priority", "Priority", "choice", CHOICES["priority"]), ("kind", "Kind", "choice", CHOICES["kind_ann"]),
                   ("created_at", "Posted", "ro", None)]},
    "event_winners": {
        "label": "Winners", "icon": "🏆", "scope": "event_id", "add": False, "delete": True, "user": True,
        "list": ["_user", "title", "position", "points"],
        "fields": [("position", "Position (1-3, 0 = special)", "int", (0, 3)), ("title", "Award", "text", 60),
                   ("team_name", "Team", "text", 60), ("points", "Points", "int", (0, 10000))]},
    "event_slots": {
        "label": "Time slots", "icon": "⏱️", "scope": "event_id", "add": True, "delete": True,
        "list": ["label", "start_dt", "end_dt", "capacity", "venue"],
        "fields": [("label", "Name (optional)", "text", 40), ("start_dt", "Starts", "dt", True), ("end_dt", "Ends", "dt", True),
                   ("venue", "Room (optional)", "text", 80), ("capacity", "Members per slot", "int", (1, 100000)),
                   ("position", "Order", "int", (0, 10000))]},
    "busy_times": {
        "label": "Busy times", "icon": "⏰", "scope": "event_id", "add": False, "delete": True, "user": True,
        "list": ["_user", "start_dt", "end_dt", "note"],
        "fields": [("start_dt", "From", "dt", True), ("end_dt", "Until", "dt", True), ("note", "Note", "text", 80)]},
}
REQUIRED_TEXT = {("schedule_items", "title"), ("coupons", "code"), ("faqs", "question"), ("faqs", "answer"),
                 ("announcements", "title"), ("announcements", "body"), ("events", "title"), ("events", "venue"),
                 ("event_winners", "title")}


def scope(eid):
    """This event plus, for a fest, its own events."""
    return [eid] + [r[0] for r in q("SELECT id FROM events WHERE parent_id=?", (eid,))]


def _spec(table):
    spec = TABLES.get(table)
    if not spec:
        abort(404)
    return spec


def _rows(table, ids):
    spec = _spec(table)
    c, a = in_clause(ids)
    if table == "events":
        return q(f"SELECT t.*, t.title AS _event FROM events t WHERE t.id IN {c} ORDER BY t.parent_id IS NOT NULL, t.position, t.id", a)
    user = ", u.name AS _user, u.username AS _username" if spec.get("user") else ""
    join = " LEFT JOIN users u ON u.id=t.user_id" if spec.get("user") else ""
    return q(f"""SELECT t.*, ev.title AS _event{user} FROM {table} t JOIN events ev ON ev.id=t.event_id{join}
                 WHERE t.event_id IN {c} ORDER BY t.id DESC LIMIT 2000""", a)


def _row(table, rid, ids):
    c, a = in_clause(ids)
    col = "id" if table == "events" else "event_id"
    row = q(f"SELECT * FROM {table} WHERE id=? AND {col} IN {c}", [rid] + a, one=True)
    if not row:
        abort(404)                              # not this event's row: never touched
    return row


def _clean(table, form, partial_existing=None):
    """Validate the posted fields of a table. Returns (values dict, list of errors)."""
    out, errors = {}, []
    for col, label, kind, opt in _spec(table)["fields"]:
        if kind == "ro":
            continue
        raw = form.get(col)
        if kind == "bool":
            out[col] = 1 if raw else 0
            continue
        raw = (raw or "").strip()
        if kind in ("text", "area"):
            if not raw and (table, col) in REQUIRED_TEXT:
                errors.append(f"{label} can't be empty.")
            out[col] = raw[:opt] or None
            if (table, col) in REQUIRED_TEXT and out[col] is None:
                out[col] = ""
        elif kind in ("int", "float"):
            if raw == "":
                out[col] = 0
                continue
            try:
                v = int(raw) if kind == "int" else float(raw)
            except ValueError:
                errors.append(f"{label} must be a number.")
                continue
            lo, hi = opt
            if not lo <= v <= hi:
                errors.append(f"{label} must be between {lo} and {hi}.")
            out[col] = v
        elif kind == "choice":
            if raw not in opt:
                errors.append(f"{label}: choose one of {', '.join(opt)}.")
            out[col] = raw
        elif kind == "dt":
            if not raw:
                if opt:
                    errors.append(f"{label} is required.")
                out[col] = None
            else:
                d = core.parse_dt(raw)
                if not d:
                    errors.append(f"{label} isn't a valid date and time.")
                out[col] = core.iso(d) if d else None
    for a_, b_ in (("start_dt", "end_dt"), ("slot_start", "slot_end")):
        if out.get(a_) and out.get(b_) and out[b_] < out[a_]:
            errors.append("The end time must be after the start time.")
    return out, errors


@bp.route("", strict_slashes=False)
@bp.route("/<table>")
def index(eid, table="registrations"):
    need(eid, "lead")
    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True) or abort(404)
    _spec(table)
    ids = scope(eid)
    counts = {t: (len(ids) if t == "events" else scalar(f"SELECT COUNT(*) FROM {t} WHERE event_id IN {in_clause(ids)[0]}", ids))
              for t in TABLES}
    events = q(f"SELECT id, title FROM events WHERE id IN {in_clause(ids)[0]} ORDER BY parent_id IS NOT NULL, title", ids)
    return render_template("studio/data.html", e=e, table=table, spec=TABLES[table], tables=TABLES, rows=_rows(table, ids),
                           counts=counts, events=events, active=f"ev{eid}")


@bp.route("/<table>/<int:rid>", methods=["POST"])
def update(eid, table, rid):
    need(eid, "lead")
    ids = scope(eid)
    _row(table, rid, ids)
    values, errors = _clean(table, request.form)
    if errors:
        flash(" ".join(errors), "error")
        return redirect(url_for("data.index", eid=eid, table=table) + f"#row-{rid}")
    try:
        ex(f"UPDATE {table} SET {', '.join(k + '=?' for k in values)} WHERE id=?", tuple(values.values()) + (rid,))
    except Exception as err:                    # unique codes, foreign keys and the like
        flash(f"Couldn't save: {str(err).splitlines()[0][:160]}", "error")
        return redirect(url_for("data.index", eid=eid, table=table) + f"#row-{rid}")
    if table == "event_slots":                  # people in the slot get its new times
        ex("""UPDATE registrations SET slot_start=?, slot_end=?, slot_venue=COALESCE(?, (SELECT venue FROM events WHERE id=registrations.event_id))
              WHERE slot_id=?""", (values["start_dt"], values["end_dt"], values["venue"], rid))
    core.audit("data.update", f"{table}#{rid}: " + ", ".join(f"{k}={v}" for k, v in values.items())[:400])
    flash(f"Saved {TABLES[table]['label'].lower()} row #{rid}.", "success")
    return redirect(url_for("data.index", eid=eid, table=table) + f"#row-{rid}")


@bp.route("/<table>/<int:rid>/delete", methods=["POST"])
def delete(eid, table, rid):
    need(eid, "lead")
    spec = _spec(table)
    if not spec["delete"]:
        abort(403)
    row = _row(table, rid, scope(eid))
    if table == "event_slots":
        if (scalar("SELECT COUNT(*) FROM event_slots WHERE event_id=?", (row["event_id"],)) or 0) <= 1:
            flash("An event needs at least one time slot. Edit this one instead.", "error")
            return redirect(url_for("data.index", eid=eid, table=table))
        scheduler.delete_slot(row)
    else:
        ex(f"DELETE FROM {table} WHERE id=?", (rid,))
    core.audit("data.delete", f"{table}#{rid}")
    flash(f"Deleted row #{rid}.", "success")
    return redirect(url_for("data.index", eid=eid, table=table))


@bp.route("/<table>/new", methods=["POST"])
def add(eid, table):
    need(eid, "lead")
    spec = _spec(table)
    if not spec["add"]:
        abort(403)
    ids = scope(eid)
    target = request.form.get("event_id", type=int) or eid
    if target not in ids:
        abort(403)
    if table == "registrations":
        uname = (request.form.get("username") or "").strip().lstrip("@")
        user = q("SELECT id, name FROM users WHERE username=? AND role='student' AND status='active'", (uname,), one=True)
        if not user:
            flash(f"No student called @{uname}.", "error")
            return redirect(url_for("data.index", eid=eid, table=table))
        if scalar("SELECT 1 FROM registrations WHERE event_id=? AND user_id=?", (target, user["id"])):
            flash(f"{user['name']} is already registered for that event. Edit their row instead.", "error")
            return redirect(url_for("data.index", eid=eid, table=table))
        status = request.form.get("status") if request.form.get("status") in CHOICES["status_reg"] else "confirmed"
        food = request.form.get("food_pref") if request.form.get("food_pref") in CHOICES["food"] else "veg"
        rid = ex("""INSERT INTO registrations (event_id, user_id, pass_code, team_name, food_pref, status, amount)
                    VALUES (?,?,?,?,?,?,0)""", (target, user["id"], core.gen_code("EVF-", "registrations", "pass_code"),
                                                (request.form.get("team_name") or "").strip()[:60] or None, food, status))
        core.notify(user["id"], "event", f"The organisers added you to {scalar('SELECT title FROM events WHERE id=?', (target,))}.",
                    url_for("events.tickets"))
        if status == "confirmed":
            ev = q("SELECT id, parent_id FROM events WHERE id=?", (target,), one=True)
            scheduler.place_new(ev["parent_id"] or ev["id"])
    else:
        values, errors = _clean(table, request.form)
        if errors:
            flash(" ".join(errors), "error")
            return redirect(url_for("data.index", eid=eid, table=table))
        values["event_id"] = target
        try:
            rid = ex(f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})", tuple(values.values()))
        except Exception as err:
            flash(f"Couldn't add: {str(err).splitlines()[0][:160]}", "error")
            return redirect(url_for("data.index", eid=eid, table=table))
    core.audit("data.add", f"{table}#{rid}")
    flash(f"Added a row to {spec['label'].lower()}.", "success")
    return redirect(url_for("data.index", eid=eid, table=table) + f"#row-{rid}")


@bp.route("/<table>.csv")
def export(eid, table):
    need(eid, "lead")
    rows = _rows(table, scope(eid))
    buf = io.StringIO()
    if rows:
        w = csv.writer(buf)
        w.writerow(rows[0].keys())
        for r in rows:
            w.writerow(list(r))
    resp = Response(buf.getvalue(), mimetype="text/csv")
    resp.headers["Content-Disposition"] = f'attachment; filename="event-{eid}-{table}.csv"'
    return resp
