"""The community: feed, explore, profiles, follows, friends, posts, messages, notifications, saved, settings."""
import json
from datetime import datetime, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash, g, abort, session, Response
from werkzeug.security import generate_password_hash, check_password_hash

import core
from db import q, ex, scalar, in_clause

bp = Blueprint("social", __name__)
PAGE = 8


# ================================================================== post hydration (shared)
def hydrate_posts(rows, me=None):
    """Turn post rows into dicts with author, media, counts and the viewer's like/save state."""
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    clause, args = in_clause(ids)
    media = {}
    for m in q(f"SELECT * FROM post_media WHERE post_id IN {clause} ORDER BY position, id", args):
        media.setdefault(m["post_id"], []).append(dict(m))
    likes = {r[0]: r[1] for r in q(f"SELECT post_id, COUNT(*) FROM post_likes WHERE post_id IN {clause} GROUP BY post_id", args)}
    ccount = {r[0]: r[1] for r in q(f"SELECT post_id, COUNT(*) FROM comments WHERE post_id IN {clause} GROUP BY post_id", args)}
    liked, saved = set(), set()
    if me:
        liked = {r[0] for r in q(f"SELECT post_id FROM post_likes WHERE user_id=? AND post_id IN {clause}", [me["id"]] + args)}
        saved = {r[0] for r in q(f"SELECT post_id FROM post_saves WHERE user_id=? AND post_id IN {clause}", [me["id"]] + args)}
    latest = {}
    for c in q(f"""SELECT c.*, u.username, u.name FROM comments c JOIN users u ON u.id=c.user_id
                   WHERE c.post_id IN {clause} ORDER BY c.created_at DESC""", args):
        lst = latest.setdefault(c["post_id"], [])
        if len(lst) < 2:
            lst.append(dict(c))
    out = []
    for r in rows:
        d = dict(r)
        d["media"] = media.get(r["id"], [])
        d["visual"] = [m for m in d["media"] if m["kind"] in ("image", "video")]
        d["files"] = [m for m in d["media"] if m["kind"] == "file"]
        d["likes"] = likes.get(r["id"], 0)
        d["comment_count"] = ccount.get(r["id"], 0)
        d["liked"] = r["id"] in liked
        d["saved"] = r["id"] in saved
        d["latest"] = list(reversed(latest.get(r["id"], [])))
        out.append(d)
    return out


POST_SELECT = """SELECT p.*, u.username, u.name author_name, u.avatar author_avatar, u.verification,
                        e.title event_title, e.start_dt event_start, e.fee event_fee, e.status event_status, e.venue event_venue
                 FROM posts p JOIN users u ON u.id=p.author_id LEFT JOIN events e ON e.id=p.event_id
                 WHERE p.is_removed=0 AND u.status='active'"""


def event_cards(where="", args=(), limit=12, order="e.start_dt"):
    """Top-level cards: single events and fests (a fest's own events live inside its page)."""
    return q(f"""SELECT e.*, u.name college, u.username college_username, u.avatar college_avatar, u.verification,
                        CASE WHEN e.kind='fest' THEN
                          (SELECT COUNT(DISTINCT r.user_id) FROM registrations r JOIN events s ON s.id=r.event_id
                           WHERE s.parent_id=e.id AND r.status IN ('confirmed','payment_review'))
                        ELSE (SELECT COUNT(*) FROM registrations r WHERE r.event_id=e.id AND r.status IN ('confirmed','payment_review')) END taken,
                        (SELECT COUNT(*) FROM events s WHERE s.parent_id=e.id AND s.is_removed=0 AND s.status!='draft') sub_count
                 FROM events e JOIN users u ON u.id=e.college_id
                 WHERE e.status!='draft' AND e.is_removed=0 AND u.status='active' AND e.parent_id IS NULL {where}
                 ORDER BY {order} LIMIT {int(limit)}""", args)


def friends_going(me, event_ids):
    """{event_id: [friend rows]} for social proof on cards."""
    if not me or not event_ids:
        return {}
    fids = core.friend_ids(me["id"])
    if not fids:
        return {}
    c1, a1 = in_clause(fids)
    c2, a2 = in_clause(event_ids)
    out = {}
    for r in q(f"""SELECT r.event_id, u.id, u.name, u.username, u.avatar FROM registrations r JOIN users u ON u.id=r.user_id
                   WHERE r.status='confirmed' AND r.user_id IN {c1} AND r.event_id IN {c2}""", a1 + a2):
        out.setdefault(r["event_id"], []).append(r)
    return out


# ================================================================== home feed
@bp.route("/")
def home():
    if not g.user:
        events = event_cards("AND e.status='open' AND e.start_dt >= ?", (core.now_iso(),), limit=6,
                             order="e.is_featured DESC, e.start_dt")
        colleges = q("""SELECT u.*, (SELECT COUNT(*) FROM follows f WHERE f.college_id=u.id) followers,
                               (SELECT COUNT(*) FROM events e WHERE e.college_id=u.id AND e.is_removed=0 AND e.status!='draft' AND e.parent_id IS NULL) events
                        FROM users u WHERE role='college' AND status='active' AND verification='verified'
                        ORDER BY followers DESC LIMIT 6""")
        stats = {"colleges": scalar("SELECT COUNT(*) FROM users WHERE role='college' AND status='active'"),
                 "events": scalar("SELECT COUNT(*) FROM events WHERE status!='draft' AND is_removed=0 AND parent_id IS NULL"),
                 "students": scalar("SELECT COUNT(*) FROM users WHERE role='student'"),
                 "friends": scalar("SELECT COUNT(*) FROM friendships WHERE status='accepted'")}
        return render_template("social/landing.html", events=events, colleges=colleges, stats=stats)
    if g.user["role"] == "dev" and not request.args.get("feed"):
        return redirect(url_for("dev.overview"))

    page = max(1, request.args.get("page", 1, type=int))
    me = g.user
    following = [r[0] for r in q("SELECT college_id FROM follows WHERE follower_id=?", (me["id"],))]
    authors = following + [me["id"]] + (core.friend_ids(me["id"]) if me["role"] == "student" else [])
    clause, args = in_clause(following)
    a_clause, a_args = in_clause(authors)
    rows = q(f"""{POST_SELECT} AND p.author_id IN {a_clause} ORDER BY p.created_at DESC LIMIT ? OFFSET ?""",
             a_args + [PAGE + 1, (page - 1) * PAGE])
    suggested = False
    if not rows and page == 1:
        rows = q(f"{POST_SELECT} ORDER BY p.created_at DESC LIMIT ?", (PAGE,))
        suggested = True
    has_more = len(rows) > PAGE
    posts = hydrate_posts(rows[:PAGE], me)
    if request.args.get("partial"):
        return render_template("partials/post_list.html", posts=posts, has_more=has_more, page=page)

    # stories: followed colleges with something coming up in the next 30 days
    stories = []
    if following:
        horizon = core.iso(datetime.now() + timedelta(days=45))
        for e in q(f"""SELECT e.*, u.name college, u.username, u.avatar college_avatar FROM events e JOIN users u ON u.id=e.college_id
                       WHERE e.college_id IN {clause} AND e.status IN ('open','closed') AND e.is_removed=0 AND e.parent_id IS NULL
                       AND e.start_dt BETWEEN ? AND ? ORDER BY e.start_dt""", args + [core.now_iso(), horizon]):
            days = (core.parse_dt(e["start_dt"]).date() - datetime.now().date()).days
            stories.append({"college": e["college"], "username": e["username"], "avatar_path": e["college_avatar"],
                            "profile": url_for("social.profile", username=e["username"]),
                            "ago": "today" if days <= 0 else "tomorrow" if days == 1 else f"in {days} days",
                            "avatar": url_for("media", rel=e["college_avatar"]) if e["college_avatar"] else None,
                            "title": e["title"], "tagline": e["tagline"] or "", "category": e["category"],
                            "when": core.parse_dt(e["start_dt"]).strftime("%a %d %b, %I:%M %p").replace(" 0", " "),
                            "venue": e["venue"], "banner": url_for("media", rel=e["banner"]) if e["banner"] else None,
                            "fee": e["fee"], "fest": e["kind"] == "fest", "url": url_for("events.detail", eid=e["id"]),
                            "color": core.CATEGORIES.get(e["category"], "#7A8399")})
    next_ticket = q("""SELECT r.*, e.title, e.start_dt, e.venue FROM registrations r JOIN events e ON e.id=r.event_id
                       WHERE r.user_id=? AND r.status='confirmed' AND e.end_dt >= ? ORDER BY e.start_dt LIMIT 1""",
                    (me["id"], core.now_iso()), one=True)
    return render_template("social/home.html", posts=posts, has_more=has_more, page=page, suggested=suggested,
                           stories=stories, stories_json=json.dumps(stories), next_ticket=next_ticket,
                           college_suggestions=suggest_colleges(me, 4), people_suggestions=suggest_people(me, 4),
                           trending=event_cards("AND e.status='open' AND e.start_dt >= ?", (core.now_iso(),), limit=4,
                                                order="taken DESC, e.start_dt"))


def suggest_colleges(me, limit=6):
    return q("""SELECT u.*, (SELECT COUNT(*) FROM follows f WHERE f.college_id=u.id) followers FROM users u
                WHERE u.role='college' AND u.status='active' AND u.id != ?
                AND u.id NOT IN (SELECT college_id FROM follows WHERE follower_id=?)
                ORDER BY (u.verification='verified') DESC, followers DESC LIMIT ?""", (me["id"], me["id"], limit))


def suggest_people(me, limit=6):
    """People you may know: mutual friends first, then same college, then same department."""
    fids = core.friend_ids(me["id"])
    pending = [r[0] for r in q("""SELECT CASE WHEN requester_id=? THEN addressee_id ELSE requester_id END FROM friendships
                                  WHERE requester_id=? OR addressee_id=?""", (me["id"], me["id"], me["id"]))]
    exclude = set(fids) | set(pending) | {me["id"]}
    c_ex, a_ex = in_clause(exclude)
    c_f, a_f = in_clause(fids)
    rows = q(f"""SELECT u.*,
                   (SELECT COUNT(*) FROM friendships f WHERE f.status='accepted' AND
                       ((f.requester_id=u.id AND f.addressee_id IN {c_f}) OR (f.addressee_id=u.id AND f.requester_id IN {c_f}))) mutual
                 FROM users u WHERE u.role='student' AND u.status='active' AND u.is_private=0 AND u.id NOT IN {c_ex}
                 ORDER BY mutual DESC, (COALESCE(u.college_name,'')=?) DESC, (COALESCE(u.department,'')=?) DESC, u.id DESC
                 LIMIT ?""", a_f + a_f + a_ex + [me["college_name"] or "~", me["department"] or "~", limit])
    return rows


# ================================================================== explore & search
@bp.route("/explore")
def explore():
    tab = request.args.get("tab", "events")
    qtext = (request.args.get("q") or "").strip()
    like = f"%{qtext.lstrip('#@')}%"
    cat = request.args.get("cat", "")
    when = request.args.get("when", "")
    price = request.args.get("price", "")
    city = (request.args.get("city") or "").strip()
    sort = request.args.get("sort", "soon")
    ctx = {"tab": tab, "qtext": qtext, "cat": cat, "when": when, "price": price, "city": city, "sort": sort}

    where, args = ["AND (e.status IN ('open','closed') OR (e.status='completed' AND ?=1))"], [1 if request.args.get("past") else 0]
    if not request.args.get("past"):
        where.append("AND e.end_dt >= ?")
        args.append(core.now_iso())
    if qtext:
        where.append("AND (e.title LIKE ? OR e.tagline LIKE ? OR e.description LIKE ? OR u.name LIKE ? OR e.venue LIKE ? OR e.city LIKE ?)")
        args += [like] * 6
    if cat in core.CATEGORIES:
        where.append("AND e.category=?")
        args.append(cat)
    if when in ("today", "week", "month"):
        days = {"today": 1, "week": 7, "month": 31}[when]
        where.append("AND e.start_dt <= ?")
        args.append(core.iso((datetime.now() + timedelta(days=days)).replace(hour=23, minute=59)))
    if price == "free":
        where.append("AND e.fee=0")
    elif price == "paid":
        where.append("AND e.fee>0")
    if city:
        where.append("AND (e.city LIKE ? OR u.city LIKE ?)")
        args += [f"%{city}%"] * 2
    order = {"soon": "e.start_dt", "popular": "taken DESC, e.views DESC", "new": "e.created_at DESC",
             "price": "e.fee, e.start_dt"}.get(sort, "e.start_dt")
    events = event_cards(" ".join(where), args, limit=60, order=order) if tab == "events" else []
    featured = event_cards("AND e.is_featured=1 AND e.end_dt >= ?", (core.now_iso(),), limit=6) if tab == "events" and not qtext else []

    colleges = people = posts = []
    if tab == "colleges":
        colleges = q("""SELECT u.*, (SELECT COUNT(*) FROM follows f WHERE f.college_id=u.id) followers,
                               (SELECT COUNT(*) FROM events e WHERE e.college_id=u.id AND e.is_removed=0 AND e.status!='draft' AND e.parent_id IS NULL) events
                        FROM users u WHERE role='college' AND status='active'
                        AND (?='' OR u.name LIKE ? OR u.username LIKE ? OR u.city LIKE ?)
                        ORDER BY (verification='verified') DESC, followers DESC""", (qtext, like, like, like))
        followed = {r[0] for r in q("SELECT college_id FROM follows WHERE follower_id=?", (g.user["id"],))} if g.user else set()
        ctx["followed"] = followed
    elif tab == "people":
        people = q("""SELECT u.* FROM users u WHERE role='student' AND status='active'
                      AND (?='' OR u.name LIKE ? OR u.username LIKE ? OR u.college_name LIKE ? OR u.department LIKE ?)
                      ORDER BY u.name LIMIT 60""", (qtext, like, like, like, like))
        ctx["statuses"] = {p["id"]: core.friend_status(g.user, p["id"]) for p in people} if g.user else {}
    elif tab == "posts":
        rows = q(f"""{POST_SELECT} AND (?='' OR p.caption LIKE ? OR u.name LIKE ?)
                     ORDER BY p.created_at DESC LIMIT 60""", (qtext, like, like))
        posts = hydrate_posts(rows, g.user)
    going = friends_going(g.user, [e["id"] for e in events])
    saved_ids = {r[0] for r in q("SELECT event_id FROM event_saves WHERE user_id=?", (g.user["id"],))} if g.user else set()
    return render_template("social/explore.html", events=events, featured=featured, colleges=colleges, people=people,
                           posts=posts, going=going, saved_ids=saved_ids, welcome=request.args.get("welcome"), **ctx)


# ================================================================== profiles
@bp.route("/u/<username>")
def profile(username):
    user = q("SELECT * FROM users WHERE username=?", (username,), one=True)
    if not user or (user["status"] != "active" and not (g.user and g.user["role"] == "dev")):
        abort(404)
    me = g.user
    tab = request.args.get("tab", "")
    ctx = {"u": user, "is_me": bool(me and me["id"] == user["id"])}
    if user["role"] == "college":
        ctx["followers"] = scalar("SELECT COUNT(*) FROM follows WHERE college_id=?", (user["id"],))
        ctx["following"] = core.is_following(me, user["id"])
        rows = q(f"{POST_SELECT} AND p.author_id=? ORDER BY p.created_at DESC", (user["id"],))
        ctx["posts"] = hydrate_posts(rows, me)
        ctx["events"] = event_cards("AND e.college_id=?", (user["id"],), limit=50,
                                    order="CASE WHEN e.status='completed' THEN 1 ELSE 0 END, e.start_dt")
        ctx["attendees"] = scalar("""SELECT COUNT(DISTINCT r.user_id) FROM registrations r JOIN events e ON e.id=r.event_id
                                     WHERE e.college_id=? AND r.status='confirmed'""", (user["id"],))
        ctx["tab"] = tab or "posts"
        ctx["mutual_followers"] = []
        if me:
            fids = core.friend_ids(me["id"])
            if fids:
                c, a = in_clause(fids)
                ctx["mutual_followers"] = q(f"""SELECT u.name, u.username, u.avatar FROM follows f JOIN users u ON u.id=f.follower_id
                                                WHERE f.college_id=? AND f.follower_id IN {c} LIMIT 3""", [user["id"]] + a)
        ctx["can_message"] = core.can_message(me, user)
        return render_template("social/profile_college.html", **ctx)

    status = core.friend_status(me, user["id"])
    fids = core.friend_ids(user["id"])
    ctx["posts"] = hydrate_posts(q(f"{POST_SELECT} AND p.author_id=? ORDER BY p.created_at DESC LIMIT 60", (user["id"],)), me)
    ctx.update(status=status, friend_count=len(fids), badges=core.user_badges(user["id"]), tab=tab or "events",
               can_message=core.can_message(me, user))
    ctx["events_visible"] = core.can_see_events_of(me, user)
    ctx["events"] = []
    if ctx["events_visible"]:
        ctx["events"] = q("""SELECT e.*, u.name college, u.username college_username, r.attended, r.status reg_status
                             FROM registrations r JOIN events e ON e.id=r.event_id JOIN users u ON u.id=e.college_id
                             WHERE r.user_id=? AND r.status='confirmed' AND e.is_removed=0 ORDER BY e.start_dt DESC""", (user["id"],))
    ctx["attended"] = scalar("SELECT COUNT(*) FROM registrations WHERE user_id=? AND attended=1", (user["id"],))
    ctx["certs"] = core.user_certificates(user["id"]) if ctx["events_visible"] else []
    ctx["cert_count"] = len(core.user_certificates(user["id"])) if not ctx["events_visible"] else len(ctx["certs"])
    ctx["pts"] = core.points_summary(user["id"]) if user["role"] == "student" else None
    ctx["following_count"] = scalar("SELECT COUNT(*) FROM follows WHERE follower_id=?", (user["id"],))
    c, a = in_clause(fids)
    ctx["friends"] = q(f"SELECT * FROM users WHERE id IN {c} AND status='active' ORDER BY name LIMIT 30", a)
    ctx["mutual"] = []
    if me and me["id"] != user["id"]:
        mine = set(core.friend_ids(me["id"]))
        ctx["mutual"] = [f for f in ctx["friends"] if f["id"] in mine]
    return render_template("social/profile_user.html", **ctx)


@bp.route("/settings/certificates/preview.jpg")
@core.login_required
def certificate_preview():
    """A sample certificate with this college's logo, signature and signatory, for the settings page."""
    me = g.user
    if me["role"] != "college":
        abort(404)
    import documents
    data = {"name": "Ananya Sharma", "kind": "participation", "college": me["name"], "college_name": "Your Student's College",
            "department": "Computer Science", "event": "Sample Event", "category": "Technical", "fest": "", "date": "12 October 2026",
            "venue": "Main Auditorium", "team": "", "award": "", "position": 0, "code": "EVF-SAMPLE", "issued": "12 Oct 2026",
            "verify_url": url_for("social.home", _external=True), "logo": core.media_bytes(me["cert_logo"] or me["avatar"]),
            "signature": core.media_bytes(me["cert_signature"]), "signatory": me["cert_signatory"] or "",
            "signatory_title": me["cert_signatory_title"] or ""}
    resp = Response(documents.png_preview(documents.certificate_png(data), 1200), mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/rewards")
@core.login_required
def points():
    me = g.user
    if me["role"] != "student":
        flash("Points are for student accounts.", "info")
        return redirect(url_for("social.home"))
    board = q(f"""SELECT u.id, u.name, u.username, u.avatar, u.college_name, {core.points_earned_sql()} pts
                  FROM users u WHERE u.role='student' AND u.status='active' ORDER BY pts DESC, u.name LIMIT 10""")
    board = [b for b in board if b["pts"] > 0]
    my_rank = scalar(f"""SELECT COUNT(*) + 1 FROM users u WHERE u.role='student' AND u.status='active'
                         AND {core.points_earned_sql()} > ?""", (core.points_summary(me["id"])["earned"],))
    return render_template("social/points.html", pts=core.points_summary(me["id"]), history=core.points_history(me["id"]),
                           board=board, my_rank=my_rank, levels=core.LEVELS,
                           rules={"paid": core.POINTS_PAID_REG, "free": core.POINTS_FREE_REG, "attend": core.POINTS_ATTEND,
                                  "win": core.WIN_POINTS, "pct": core.POINTS_MAX_PCT},
                           certs=core.user_certificates(me["id"]))


@bp.route("/u/<username>/followers")
def followers(username):
    user = q("SELECT * FROM users WHERE username=? AND role='college'", (username,), one=True) or abort(404)
    rows = q("""SELECT u.* FROM follows f JOIN users u ON u.id=f.follower_id WHERE f.college_id=? AND u.status='active'
                ORDER BY f.created_at DESC""", (user["id"],))
    return render_template("social/people_list.html", title=f"Followers of {user['name']}", people=rows, back=user)


# ================================================================== follow & friends (form fallbacks; JS uses /api)
@bp.route("/follow/<int:cid>", methods=["POST"])
@core.login_required
def follow(cid):
    from blueprints.api import do_follow
    do_follow(cid)
    return redirect(request.referrer or url_for("social.home"))


@bp.route("/friends")
@core.login_required
def friends():
    me = g.user
    tab = request.args.get("tab", "friends")
    fids = core.friend_ids(me["id"])
    c, a = in_clause(fids)
    lists = {
        "friends": q(f"SELECT * FROM users WHERE id IN {c} AND status='active' ORDER BY name", a),
        "requests": q("""SELECT u.*, f.created_at requested_at FROM friendships f JOIN users u ON u.id=f.requester_id
                         WHERE f.addressee_id=? AND f.status='pending' AND u.status='active' ORDER BY f.created_at DESC""", (me["id"],)),
        "sent": q("""SELECT u.*, f.created_at requested_at FROM friendships f JOIN users u ON u.id=f.addressee_id
                     WHERE f.requester_id=? AND f.status='pending' ORDER BY f.created_at DESC""", (me["id"],)),
        "suggestions": suggest_people(me, 24),
    }
    return render_template("social/friends.html", tab=tab, lists=lists)


# ================================================================== posts
def _author_can_post():
    return g.user and g.user["role"] in ("college", "student")


def _postable_events(me):
    """Events a post can be linked to: a college's own events; a student's events they're registered for."""
    if me["role"] == "college":
        return q("SELECT id, title FROM events WHERE college_id=? AND is_removed=0 ORDER BY start_dt DESC", (me["id"],))
    return q("""SELECT e.id, e.title FROM registrations r JOIN events e ON e.id=r.event_id
                WHERE r.user_id=? AND r.status='confirmed' AND e.is_removed=0 ORDER BY e.start_dt DESC""", (me["id"],))


@bp.route("/posts/new", methods=["GET", "POST"])
@core.login_required
def post_new():
    if not _author_can_post():
        flash("Only college and student accounts can publish posts.", "info")
        return redirect(url_for("social.home"))
    my_events = _postable_events(g.user)
    if request.method == "POST":
        caption = (request.form.get("caption") or "").strip()[:2200]
        event_id = request.form.get("event_id", type=int)
        if event_id and event_id not in {e["id"] for e in my_events}:
            event_id = None
        files = [f for f in request.files.getlist("media") if f and f.filename]
        if not caption and not files:
            flash("Add a caption or at least one photo, video or file.", "error")
            return render_template("social/post_new.html", my_events=my_events, form=request.form)
        if len(files) > 10:
            flash("You can attach up to 10 files per post.", "error")
            return render_template("social/post_new.html", my_events=my_events, form=request.form)
        saved = []
        try:
            for f in files:
                saved.append(core.save_upload(f))
        except ValueError as e:
            for s in saved:
                core.delete_upload(s["path"])
            flash(str(e), "error")
            return render_template("social/post_new.html", my_events=my_events, form=request.form)
        pid = ex("INSERT INTO posts (author_id, event_id, caption) VALUES (?,?,?)", (g.user["id"], event_id, caption))
        for i, s in enumerate(saved):
            ex("INSERT INTO post_media (post_id, path, kind, original_name, size, position) VALUES (?,?,?,?,?,?)",
               (pid, s["path"], s["kind"], s["original"], s["size"], i))
        if g.user["role"] == "college":
            audience = [r[0] for r in q("SELECT follower_id FROM follows WHERE college_id=?", (g.user["id"],))]
        else:
            audience = core.friend_ids(g.user["id"])
        core.notify_many(audience, "post", f"{g.user['name']} shared a new post.", url_for("social.post_view", pid=pid), g.user["id"])
        flash("Posted! Your " + ("followers" if g.user["role"] == "college" else "friends") + " will see it in their feed.", "success")
        return redirect(url_for("social.post_view", pid=pid))
    return render_template("social/post_new.html", my_events=my_events, form={"event_id": request.args.get("event", "")})


@bp.route("/p/<int:pid>")
def post_view(pid):
    rows = q(f"{POST_SELECT} AND p.id=?", (pid,))
    if not rows:
        abort(404)
    post = hydrate_posts(rows, g.user)[0]
    comments = q("""SELECT c.*, u.username, u.name, u.avatar, u.verification FROM comments c JOIN users u ON u.id=c.user_id
                    WHERE c.post_id=? ORDER BY c.created_at""", (pid,))
    more = hydrate_posts(q(f"{POST_SELECT} AND p.author_id=? AND p.id!=? ORDER BY p.created_at DESC LIMIT 6",
                           (post["author_id"], pid)), g.user)
    return render_template("social/post_view.html", post=post, comments=comments, more=more)


@bp.route("/p/<int:pid>/edit", methods=["POST"])
@core.login_required
def post_edit(pid):
    post = q("SELECT * FROM posts WHERE id=?", (pid,), one=True) or abort(404)
    if post["author_id"] != g.user["id"]:
        abort(403)
    ex("UPDATE posts SET caption=? WHERE id=?", ((request.form.get("caption") or "").strip()[:2200], pid))
    flash("Caption updated.", "success")
    return redirect(url_for("social.post_view", pid=pid))


@bp.route("/p/<int:pid>/delete", methods=["POST"])
@core.login_required
def post_delete(pid):
    post = q("SELECT * FROM posts WHERE id=?", (pid,), one=True) or abort(404)
    if post["author_id"] != g.user["id"] and g.user["role"] != "dev":
        abort(403)
    for m in q("SELECT path FROM post_media WHERE post_id=?", (pid,)):
        core.delete_upload(m["path"])
    ex("DELETE FROM posts WHERE id=?", (pid,))
    flash("Post deleted.", "info")
    return redirect(url_for("social.profile", username=g.user["username"]) if g.user["role"] != "dev" else url_for("dev.reports"))


@bp.route("/p/<int:pid>/comment", methods=["POST"])
@core.login_required
def comment_add(pid):
    from blueprints.api import do_comment
    res = do_comment(pid, request.form.get("body"))
    if res.get("error"):
        flash(res["error"], "error")
    return redirect(url_for("social.post_view", pid=pid) + "#comments")


@bp.route("/comments/<int:cid>/delete", methods=["POST"])
@core.login_required
def comment_delete(cid):
    c = q("""SELECT c.*, p.author_id FROM comments c JOIN posts p ON p.id=c.post_id WHERE c.id=?""", (cid,), one=True) or abort(404)
    if g.user["id"] not in (c["user_id"], c["author_id"]) and g.user["role"] != "dev":
        abort(403)
    ex("DELETE FROM comments WHERE id=?", (cid,))
    flash("Comment removed.", "info")
    return redirect(url_for("social.post_view", pid=c["post_id"]) + "#comments")


@bp.route("/report", methods=["POST"])
@core.login_required
def report():
    t = request.form.get("target_type")
    tid = request.form.get("target_id", type=int)
    reason = (request.form.get("reason") or "").strip()[:300] or "No reason given"
    if t in ("post", "user", "event", "comment") and tid:
        ex("INSERT INTO reports (reporter_id, target_type, target_id, reason) VALUES (?,?,?,?)", (g.user["id"], t, tid, reason))
        dev = scalar("SELECT id FROM users WHERE role='dev'")
        core.notify(dev, "report", f"New report on a {t}: {reason[:80]}", url_for("dev.reports"), g.user["id"])
        flash("Thanks. The EventFlow team will review this.", "success")
    return redirect(request.referrer or url_for("social.home"))


# ================================================================== saved
@bp.route("/saved")
@core.login_required
def saved():
    tab = request.args.get("tab", "events")
    events = q("""SELECT e.*, u.name college, u.username college_username, u.avatar college_avatar, u.verification,
                         (SELECT COUNT(*) FROM registrations r WHERE r.event_id=e.id AND r.status IN ('confirmed','payment_review')) taken
                  FROM event_saves s JOIN events e ON e.id=s.event_id JOIN users u ON u.id=e.college_id
                  WHERE s.user_id=? AND e.is_removed=0 AND e.status!='draft' ORDER BY e.start_dt""", (g.user["id"],))
    rows = q(f"""{POST_SELECT} AND p.id IN (SELECT post_id FROM post_saves WHERE user_id=?) ORDER BY p.created_at DESC""",
             (g.user["id"],))
    return render_template("social/saved.html", tab=tab, events=events, posts=hydrate_posts(rows, g.user),
                           going=friends_going(g.user, [e["id"] for e in events]))


# ================================================================== notifications
@bp.route("/notifications")
@core.login_required
def notifications():
    rows = q("""SELECT n.*, u.name actor_name, u.username actor_username, u.avatar actor_avatar, u.role actor_role
                FROM notifications n LEFT JOIN users u ON u.id=n.actor_id
                WHERE n.user_id=? ORDER BY n.created_at DESC, n.id DESC LIMIT 100""", (g.user["id"],))
    requests_ = q("""SELECT u.* FROM friendships f JOIN users u ON u.id=f.requester_id
                     WHERE f.addressee_id=? AND f.status='pending' ORDER BY f.created_at DESC LIMIT 5""", (g.user["id"],))
    ex("UPDATE notifications SET is_read=1 WHERE user_id=? AND is_read=0", (g.user["id"],))
    today = datetime.now().strftime("%Y-%m-%d")
    week = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    groups = [("Today", [n for n in rows if n["created_at"][:10] == today]),
              ("This week", [n for n in rows if week <= n["created_at"][:10] < today]),
              ("Earlier", [n for n in rows if n["created_at"][:10] < week])]
    return render_template("social/notifications.html", groups=groups, requests=requests_)


# ================================================================== messages
def conversations(me):
    rows = q("""SELECT m.*, CASE WHEN m.sender_id=? THEN m.recipient_id ELSE m.sender_id END other_id
                FROM messages m WHERE m.sender_id=? OR m.recipient_id=? ORDER BY m.id DESC""", (me["id"], me["id"], me["id"]))
    seen, convs = set(), []
    for r in rows:
        if r["other_id"] in seen:
            continue
        seen.add(r["other_id"])
        other = q("SELECT * FROM users WHERE id=?", (r["other_id"],), one=True)
        if not other:
            continue
        unread = scalar("SELECT COUNT(*) FROM messages WHERE sender_id=? AND recipient_id=? AND read_at IS NULL",
                        (other["id"], me["id"]))
        convs.append({"other": other, "last": r, "unread": unread})
    return convs


@bp.route("/messages")
@bp.route("/messages/<username>")
@core.login_required
def messages(username=None):
    me = g.user
    convs = conversations(me)
    other, thread = None, []
    if username:
        other = q("SELECT * FROM users WHERE username=?", (username,), one=True) or abort(404)
        if other["id"] == me["id"]:
            return redirect(url_for("social.messages"))
        thread = q("""SELECT m.*, e.title event_title FROM messages m LEFT JOIN events e ON e.id=m.event_id
                      WHERE (sender_id=? AND recipient_id=?) OR (sender_id=? AND recipient_id=?) ORDER BY m.id""",
                   (me["id"], other["id"], other["id"], me["id"]))
        ex("UPDATE messages SET read_at=? WHERE sender_id=? AND recipient_id=? AND read_at IS NULL",
           (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), other["id"], me["id"]))
    friends_ = []
    fids = core.friend_ids(me["id"])
    if fids:
        c, a = in_clause(fids)
        friends_ = q(f"SELECT * FROM users WHERE id IN {c} AND status='active' ORDER BY name LIMIT 20", a)
    return render_template("social/messages.html", convs=convs, other=other, thread=thread, friends=friends_,
                           can_send=core.can_message(me, other) if other else False)


@bp.route("/messages/<username>/send", methods=["POST"])
@core.login_required
def message_send(username):
    from blueprints.api import do_send_message
    other = q("SELECT * FROM users WHERE username=?", (username,), one=True) or abort(404)
    res = do_send_message(other, request.form.get("body"))
    if res.get("error"):
        flash(res["error"], "error")
    return redirect(url_for("social.messages", username=username))


# ================================================================== settings
SETTINGS_TABS = ["profile", "account", "privacy", "notifications", "appearance", "payments", "certificates", "danger"]
COLLEGE_TABS = ("payments", "certificates")


@bp.route("/settings", methods=["GET", "POST"])
@bp.route("/settings/<tab>", methods=["GET", "POST"])
@core.login_required
def settings(tab="profile"):
    if tab not in SETTINGS_TABS or (tab in COLLEGE_TABS and g.user["role"] != "college"):
        abort(404)
    me = g.user
    if request.method == "POST":
        f = request.form
        if tab == "profile":
            fields = {"name": (f.get("name") or "").strip()[:80], "bio": (f.get("bio") or "").strip()[:300],
                      "city": (f.get("city") or "").strip()[:60], "website": core.clean_url(f.get("website"))}
            if me["role"] == "student":
                fields.update(college_name=(f.get("college_name") or "").strip()[:120],
                              department=f.get("department"), year=f.get("year"))
            if len(fields["name"]) < 2:
                flash("Name is too short.", "error")
                return redirect(url_for("social.settings", tab=tab))
            for key in ("avatar", "cover"):
                file = request.files.get(key)
                if file and file.filename:
                    try:
                        up = core.save_upload(file, allowed=("image",), max_mb=10)
                    except ValueError as e:
                        flash(str(e), "error")
                        return redirect(url_for("social.settings", tab=tab))
                    core.delete_upload(me[key])
                    fields[key] = up["path"]
                elif f.get(f"remove_{key}"):
                    core.delete_upload(me[key])
                    fields[key] = None
            ex(f"UPDATE users SET {', '.join(k + '=?' for k in fields)} WHERE id=?", tuple(fields.values()) + (me["id"],))
            flash("Profile saved.", "success")
        elif tab == "account":
            if not check_password_hash(me["password_hash"], f.get("current_password") or ""):
                flash("Your current password is wrong.", "error")
                return redirect(url_for("social.settings", tab=tab))
            from blueprints.auth import USERNAME_RE, RESERVED
            username = (f.get("username") or "").strip().lstrip("@")
            email = (f.get("email") or "").strip().lower()
            phone = "".join(ch for ch in (f.get("phone") or "") if ch.isdigit() or ch == "+") or None
            if not USERNAME_RE.match(username) or username.lower() in RESERVED:
                flash("That username isn't allowed.", "error")
            elif scalar("SELECT 1 FROM users WHERE username=? AND id!=?", (username, me["id"])):
                flash("That username is taken.", "error")
            elif "@" not in email or scalar("SELECT 1 FROM users WHERE email=? AND id!=?", (email, me["id"])):
                flash("That email is invalid or already in use.", "error")
            elif phone and scalar("SELECT 1 FROM users WHERE phone=? AND id!=?", (phone, me["id"])):
                flash("That mobile number is already in use.", "error")
            else:
                ex("UPDATE users SET username=?, email=?, phone=? WHERE id=?", (username, email, phone, me["id"]))
                new_pw = f.get("new_password") or ""
                if new_pw:
                    if len(new_pw) < 8 or new_pw.isdigit() or new_pw.isalpha():
                        flash("Account details saved, but the new password needs 8+ characters with letters and numbers.", "error")
                        return redirect(url_for("social.settings", tab=tab))
                    ex("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new_pw), me["id"]))
                flash("Account updated.", "success")
        elif tab == "privacy":
            show = f.get("show_events") if f.get("show_events") in ("everyone", "friends", "nobody") else "friends"
            allow = f.get("allow_messages") if f.get("allow_messages") in ("everyone", "friends", "nobody") else "friends"
            ex("UPDATE users SET show_events=?, allow_messages=?, is_private=? WHERE id=?",
               (show, allow, 1 if f.get("is_private") else 0, me["id"]))
            flash("Privacy settings saved.", "success")
        elif tab == "notifications":
            ex("UPDATE users SET notify_posts=?, notify_friends=?, notify_events=? WHERE id=?",
               (1 if f.get("notify_posts") else 0, 1 if f.get("notify_friends") else 0, 1 if f.get("notify_events") else 0, me["id"]))
            flash("Notification preferences saved.", "success")
        elif tab == "appearance":
            theme = f.get("theme") if f.get("theme") in ("light", "dark", "system") else "system"
            ex("UPDATE users SET theme=? WHERE id=?", (theme, me["id"]))
            flash("Appearance updated.", "success")
        elif tab == "payments":
            upi = (f.get("upi_id") or "").strip()
            import re
            if upi and not re.match(r"^[\w.\-]{2,}@[A-Za-z]{2,}$", upi):
                flash("UPI IDs look like name@bank.", "error")
            else:
                ex("UPDATE users SET upi_id=? WHERE id=?", (upi or None, me["id"]))
                if f.get("request_verification") and me["verification"] in ("none", "rejected"):
                    ex("UPDATE users SET verification='pending', verification_note=? WHERE id=?",
                       ((f.get("verification_note") or "").strip()[:300], me["id"]))
                    dev = scalar("SELECT id FROM users WHERE role='dev'")
                    core.notify(dev, "verify", f"{me['name']} asked to be verified.", url_for("dev.colleges", tab="pending"), me["id"])
                    flash("Verification requested. You'll be notified when it's reviewed.", "success")
                else:
                    flash("Payment settings saved.", "success")
        elif tab == "certificates":
            fields = {"cert_signatory": (f.get("cert_signatory") or "").strip()[:60] or None,
                      "cert_signatory_title": (f.get("cert_signatory_title") or "").strip()[:80] or None}
            for key in ("cert_logo", "cert_signature"):
                file = request.files.get(key)
                if file and file.filename:
                    try:
                        up = core.save_upload(file, allowed=("image",), max_mb=5)
                    except ValueError as e:
                        flash(str(e), "error")
                        return redirect(url_for("social.settings", tab=tab))
                    core.delete_upload(me[key])
                    fields[key] = up["path"]
                elif f.get(f"remove_{key}"):
                    core.delete_upload(me[key])
                    fields[key] = None
            ex(f"UPDATE users SET {', '.join(k + '=?' for k in fields)} WHERE id=?", tuple(fields.values()) + (me["id"],))
            flash("Certificate design saved. Every certificate for your events now uses it.", "success")
        elif tab == "danger":
            if me["role"] == "dev":
                flash("The developer account can't be deleted.", "error")
            elif not check_password_hash(me["password_hash"], f.get("password") or ""):
                flash("Password is wrong. Your account was not deleted.", "error")
            else:
                for row in q("SELECT path FROM post_media pm JOIN posts p ON p.id=pm.post_id WHERE p.author_id=?", (me["id"],)):
                    core.delete_upload(row["path"])
                core.delete_upload(me["avatar"])
                core.delete_upload(me["cover"])
                ex("DELETE FROM users WHERE id=?", (me["id"],))
                session.clear()
                flash("Your account and data were deleted.", "info")
                return redirect(url_for("auth.login"))
        return redirect(url_for("social.settings", tab=tab))
    sessions_info = {"joined": me["created_at"], "events": scalar("SELECT COUNT(*) FROM registrations WHERE user_id=?", (me["id"],))}
    return render_template("social/settings.html", tab=tab, tabs=SETTINGS_TABS, info=sessions_info)
