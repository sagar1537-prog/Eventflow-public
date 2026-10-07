"""Developer console: the platform owner's controls over every account, event and setting."""
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash, g, abort, session, current_app
from werkzeug.security import generate_password_hash

import core
import db
from db import q, ex, scalar

bp = Blueprint("dev", __name__, url_prefix="/dev")


@bp.before_request
def guard():
    if not g.user:
        return redirect(url_for("auth.login", next=request.path))
    if g.user["role"] != "dev":
        abort(403)
    return None


@bp.context_processor
def ctx():
    return {"open_reports": scalar("SELECT COUNT(*) FROM reports WHERE status='open'"),
            "pending_colleges": scalar("SELECT COUNT(*) FROM users WHERE role='college' AND verification='pending'")}


@bp.route("/")
def overview():
    today = datetime.now().strftime("%Y-%m-%d")
    days = [(datetime.now().date() - timedelta(days=i)) for i in range(29, -1, -1)]
    start = days[0].isoformat()

    def series(sql):
        raw = {r[0]: r[1] for r in q(sql, (start,))}
        return [raw.get(d.isoformat(), 0) for d in days]

    k = {
        "students": scalar("SELECT COUNT(*) FROM users WHERE role='student'"),
        "colleges": scalar("SELECT COUNT(*) FROM users WHERE role='college'"),
        "verified": scalar("SELECT COUNT(*) FROM users WHERE role='college' AND verification='verified'"),
        "events": scalar("SELECT COUNT(*) FROM events WHERE is_removed=0"),
        "live": scalar("SELECT COUNT(*) FROM events WHERE status='open' AND is_removed=0"),
        "registrations": scalar("SELECT COUNT(*) FROM registrations WHERE status='confirmed'"),
        "gmv": scalar("SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='paid'"),
        "fees": scalar("SELECT COALESCE(SUM(platform_fee),0) FROM payments WHERE status='paid'"),
        "active_today": scalar("SELECT COUNT(*) FROM users WHERE last_seen >= ?", (today,)),
        "posts": scalar("SELECT COUNT(*) FROM posts WHERE is_removed=0"),
        "messages": scalar("SELECT COUNT(*) FROM messages"),
        "friendships": scalar("SELECT COUNT(*) FROM friendships WHERE status='accepted'"),
    }
    charts = {"labels": [d.strftime("%d %b") for d in days],
              "signups": series("SELECT date(created_at), COUNT(*) FROM users WHERE date(created_at)>=? GROUP BY 1"),
              "regs": series("SELECT date(created_at), COUNT(*) FROM registrations WHERE status='confirmed' AND date(created_at)>=? GROUP BY 1"),
              "gmv": series("SELECT date(created_at), SUM(amount) FROM payments WHERE status='paid' AND date(created_at)>=? GROUP BY 1")}
    top = q("""SELECT u.id, u.name, u.username, u.avatar, u.verification,
                      (SELECT COUNT(*) FROM follows f WHERE f.college_id=u.id) followers,
                      (SELECT COUNT(*) FROM events e WHERE e.college_id=u.id AND e.is_removed=0) events,
                      (SELECT COUNT(*) FROM registrations r JOIN events e ON e.id=r.event_id WHERE e.college_id=u.id AND r.status='confirmed') regs,
                      (SELECT COALESCE(SUM(p.amount),0) FROM payments p JOIN events e ON e.id=p.event_id WHERE e.college_id=u.id AND p.status='paid') gmv
               FROM users u WHERE role='college' ORDER BY regs DESC LIMIT 8""")
    log = q("""SELECT a.*, u.name FROM audit_log a LEFT JOIN users u ON u.id=a.actor_id ORDER BY a.id DESC LIMIT 10""")
    return render_template("dev/overview.html", k=k, charts=charts, top=top, log=log, active="overview")


@bp.route("/colleges")
def colleges():
    tab = request.args.get("tab", "pending")
    where = {"pending": "AND verification='pending'", "verified": "AND verification='verified'",
             "unverified": "AND verification IN ('none','rejected')"}.get(tab, "")
    rows = q(f"""SELECT u.*, (SELECT COUNT(*) FROM follows f WHERE f.college_id=u.id) followers,
                        (SELECT COUNT(*) FROM events e WHERE e.college_id=u.id AND e.is_removed=0) events
                 FROM users u WHERE role='college' {where} ORDER BY u.created_at DESC""")
    return render_template("dev/colleges.html", rows=rows, tab=tab, active="colleges")


@bp.route("/colleges/<int:uid>/<action>", methods=["POST"])
def college_action(uid, action):
    c = q("SELECT * FROM users WHERE id=? AND role='college'", (uid,), one=True) or abort(404)
    note = (request.form.get("note") or "").strip()[:300]
    if action == "verify":
        ex("UPDATE users SET verification='verified', verification_note=NULL WHERE id=?", (uid,))
        core.notify(uid, "verify", "Your college is verified on EventFlow. The blue tick is live!",
                    url_for("social.profile", username=c["username"]))
        flash(f"{c['name']} is verified.", "success")
    elif action == "reject":
        ex("UPDATE users SET verification='rejected', verification_note=? WHERE id=?", (note or "Details could not be confirmed.", uid))
        core.notify(uid, "verify", f"Verification wasn't approved: {note or 'details could not be confirmed.'}",
                    url_for("social.settings", tab="payments"))
        flash(f"{c['name']}'s verification was rejected.", "info")
    elif action == "unverify":
        ex("UPDATE users SET verification='none' WHERE id=?", (uid,))
        flash(f"Removed the tick from {c['name']}.", "info")
    core.audit(f"college.{action}", f"{c['name']} (#{uid}) {note}")
    return redirect(request.referrer or url_for("dev.colleges"))


@bp.route("/users")
def users():
    role = request.args.get("role", "")
    text = (request.args.get("q") or "").strip()
    like = f"%{text}%"
    rows = q("""SELECT u.*, (SELECT COUNT(*) FROM registrations r WHERE r.user_id=u.id AND r.status='confirmed') regs
                FROM users u WHERE (?='' OR role=?) AND (?='' OR name LIKE ? OR username LIKE ? OR email LIKE ? OR phone LIKE ?)
                ORDER BY u.created_at DESC LIMIT 200""", (role, role, text, like, like, like, like))
    return render_template("dev/users.html", rows=rows, role=role, qtext=text, temp=request.args.get("temp"), active="users")


@bp.route("/users/<int:uid>/<action>", methods=["POST"])
def user_action(uid, action):
    u = q("SELECT * FROM users WHERE id=?", (uid,), one=True) or abort(404)
    if u["role"] == "dev":
        flash("The developer account can't be changed here.", "error")
        return redirect(url_for("dev.users"))
    temp = None
    if action == "suspend":
        ex("UPDATE users SET status='suspended' WHERE id=?", (uid,))
        flash(f"@{u['username']} is suspended and logged out everywhere.", "info")
    elif action == "restore":
        ex("UPDATE users SET status='active' WHERE id=?", (uid,))
        flash(f"@{u['username']} is active again.", "success")
    elif action == "reset":
        temp = "ef-" + secrets.token_hex(4)
        ex("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(temp), uid))
        flash(f"Temporary password for @{u['username']}: {temp} (shown once, share it privately).", "success")
    core.audit(f"user.{action}", f"@{u['username']} (#{uid})")
    return redirect(request.referrer or url_for("dev.users"))


@bp.route("/events")
def events():
    rows = q("""SELECT e.*, u.name college, u.username,
                       (SELECT COUNT(*) FROM registrations r WHERE r.event_id=e.id AND r.status='confirmed') regs,
                       (SELECT COALESCE(SUM(amount),0) FROM payments p WHERE p.event_id=e.id AND p.status='paid') gmv
                FROM events e JOIN users u ON u.id=e.college_id ORDER BY e.start_dt DESC""")
    return render_template("dev/events.html", rows=rows, active="events")


@bp.route("/events/<int:eid>/<action>", methods=["POST"])
def event_action(eid, action):
    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True) or abort(404)
    if action == "feature":
        ex("UPDATE events SET is_featured=1-is_featured WHERE id=?", (eid,))
        flash(("Unfeatured " if e["is_featured"] else "Featured ") + e["title"], "success")
    elif action == "remove":
        ex("UPDATE events SET is_removed=1-is_removed WHERE id=?", (eid,))
        flash(("Restored " if e["is_removed"] else "Removed ") + e["title"], "info")
    core.audit(f"event.{action}", f"{e['title']} (#{eid})")
    return redirect(request.referrer or url_for("dev.events"))


@bp.route("/payments")
def payments():
    status = request.args.get("status", "")
    rows = q("""SELECT p.*, u.name, u.username, e.title, c.name college FROM payments p JOIN users u ON u.id=p.user_id
                JOIN events e ON e.id=p.event_id JOIN users c ON c.id=e.college_id
                WHERE (?='' OR p.status=?) ORDER BY p.created_at DESC LIMIT 300""", (status, status))
    totals = q("SELECT status, COUNT(*) c, COALESCE(SUM(amount),0) s FROM payments GROUP BY status")
    return render_template("dev/payments.html", rows=rows, status=status, totals=totals, active="payments")


@bp.route("/reports")
def reports():
    tab = request.args.get("tab", "open")
    rows = q("""SELECT r.*, u.name reporter, u.username reporter_username FROM reports r LEFT JOIN users u ON u.id=r.reporter_id
                WHERE (?='all' OR r.status=?) ORDER BY r.created_at DESC""", (tab, tab))
    items = []
    for r in rows:
        d = dict(r)
        if r["target_type"] == "post":
            t = q("""SELECT p.*, u.username, u.name FROM posts p JOIN users u ON u.id=p.author_id WHERE p.id=?""", (r["target_id"],), one=True)
            d["target"] = t
            d["label"] = f"Post by @{t['username']}" if t else "Deleted post"
            d["link"] = url_for("social.post_view", pid=r["target_id"]) if t else None
        elif r["target_type"] in ("user",):
            t = q("SELECT * FROM users WHERE id=?", (r["target_id"],), one=True)
            d["target"] = t
            d["label"] = f"@{t['username']}" if t else "Deleted account"
            d["link"] = url_for("social.profile", username=t["username"]) if t else None
        elif r["target_type"] == "event":
            t = q("SELECT * FROM events WHERE id=?", (r["target_id"],), one=True)
            d["target"] = t
            d["label"] = t["title"] if t else "Deleted event"
            d["link"] = url_for("events.detail", eid=r["target_id"]) if t else None
        else:
            t = q("SELECT * FROM comments WHERE id=?", (r["target_id"],), one=True)
            d["target"] = t
            d["label"] = f"Comment: “{t['body'][:60]}”" if t else "Deleted comment"
            d["link"] = url_for("social.post_view", pid=t["post_id"]) if t else None
        items.append(d)
    return render_template("dev/reports.html", items=items, tab=tab, active="reports")


@bp.route("/reports/<int:rid>/<action>", methods=["POST"])
def report_action(rid, action):
    r = q("SELECT * FROM reports WHERE id=?", (rid,), one=True) or abort(404)
    if action == "takedown":
        if r["target_type"] == "post":
            ex("UPDATE posts SET is_removed=1 WHERE id=?", (r["target_id"],))
        elif r["target_type"] == "comment":
            ex("DELETE FROM comments WHERE id=?", (r["target_id"],))
        elif r["target_type"] == "event":
            ex("UPDATE events SET is_removed=1 WHERE id=?", (r["target_id"],))
        elif r["target_type"] == "user":
            ex("UPDATE users SET status='suspended' WHERE id=? AND role!='dev'", (r["target_id"],))
        ex("UPDATE reports SET status='resolved' WHERE target_type=? AND target_id=? AND status='open'", (r["target_type"], r["target_id"]))
        flash("Content taken down and the report closed.", "success")
    elif action == "dismiss":
        ex("UPDATE reports SET status='dismissed' WHERE id=?", (rid,))
        flash("Report dismissed.", "info")
    core.audit(f"report.{action}", f"{r['target_type']} #{r['target_id']}")
    return redirect(url_for("dev.reports"))


@bp.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        f = request.form
        try:
            fee = min(30.0, max(0.0, float(f.get("platform_fee_pct") or 0)))
        except ValueError:
            fee = 5
        core.set_setting("platform_fee_pct", f"{fee:g}")
        for key in ("signups_open", "college_signups_open", "maintenance", "demo_payments", "require_verified_to_publish"):
            core.set_setting(key, "1" if f.get(key) else "0")
        core.set_setting("banner_text", (f.get("banner_text") or "").strip()[:200])
        core.audit("settings.update", f"fee={fee} maintenance={f.get('maintenance') and 1 or 0}")
        flash("Platform settings saved.", "success")
        return redirect(url_for("dev.settings"))
    return render_template("dev/settings.html", active="settings")


@bp.route("/broadcast", methods=["POST"])
def broadcast():
    text = (request.form.get("text") or "").strip()[:280]
    audience = request.form.get("audience", "all")
    if not text:
        flash("Write a message first.", "error")
        return redirect(url_for("dev.settings"))
    where = {"students": "role='student'", "colleges": "role='college'"}.get(audience, "role!='dev'")
    ids = [r[0] for r in q(f"SELECT id FROM users WHERE status='active' AND {where}")]
    for uid in ids:
        ex("INSERT INTO notifications (user_id, actor_id, kind, text, link) VALUES (?,?,?,?,?)",
           (uid, g.user["id"], "broadcast", text, None))
    core.audit("broadcast", f"{audience}: {text[:80]}")
    flash(f"Sent to {len(ids)} accounts.", "success")
    return redirect(url_for("dev.settings"))


@bp.route("/reset-demo", methods=["POST"])
def reset_demo():
    """Wipe everything and recreate the clean demo (accounts, friends, follows). Used before a live demo."""
    if (request.form.get("confirm") or "").strip().upper() != "RESET":
        flash("Type RESET in the box to confirm.", "error")
        return redirect(url_for("dev.settings"))
    from seed import seed
    app = current_app._get_current_object()
    with db.setup_lock():
        db.drop_all()
        db.create_schema()
        seed(app)
    if not db.is_postgres():                       # local mode keeps uploads on disk
        import shutil
        shutil.rmtree(core.upload_root(), ignore_errors=True)
    session.clear()
    flash("Demo data reset. Everything is back to the clean demo. Log in again.", "success")
    return redirect(url_for("auth.login"))


@bp.route("/audit")
def audit():
    rows = q("""SELECT a.*, u.name, u.username FROM audit_log a LEFT JOIN users u ON u.id=a.actor_id ORDER BY a.id DESC LIMIT 300""")
    return render_template("dev/audit.html", rows=rows, active="audit")
