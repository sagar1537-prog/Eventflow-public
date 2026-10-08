"""JSON endpoints used by the front-end (and reusable by the future Android app)."""
from datetime import datetime

from flask import Blueprint, request, jsonify, g, url_for

import ai_agent
import core
from db import q, ex, scalar

bp = Blueprint("api", __name__, url_prefix="/api")


def body():
    return request.get_json(silent=True) or request.form


# ------------------------------------------------------------------ shared actions (also used by form fallbacks)
def do_follow(cid):
    college = q("SELECT * FROM users WHERE id=? AND role='college' AND status='active'", (cid,), one=True)
    if not college or college["id"] == g.user["id"]:
        return {"error": "You can't follow this account."}
    if core.is_following(g.user, cid):
        ex("DELETE FROM follows WHERE follower_id=? AND college_id=?", (g.user["id"], cid))
        following = False
    else:
        ex("INSERT OR IGNORE INTO follows (follower_id, college_id) VALUES (?,?)", (g.user["id"], cid))
        core.notify(cid, "follow", f"{g.user['name']} started following you.",
                    url_for("social.profile", username=g.user["username"]), g.user["id"])
        following = True
    return {"following": following, "followers": scalar("SELECT COUNT(*) FROM follows WHERE college_id=?", (cid,))}


def do_comment(pid, text):
    text = (text or "").strip()[:1000]
    post = q("SELECT * FROM posts WHERE id=? AND is_removed=0", (pid,), one=True)
    if not post:
        return {"error": "This post no longer exists."}
    if not text:
        return {"error": "Write something first."}
    cid = ex("INSERT INTO comments (post_id, user_id, body) VALUES (?,?,?)", (pid, g.user["id"], text))
    core.notify(post["author_id"], "comment", f"{g.user['name']} commented: “{text[:60]}”",
                url_for("social.post_view", pid=pid) + "#comments", g.user["id"])
    return {"ok": True, "id": cid, "count": scalar("SELECT COUNT(*) FROM comments WHERE post_id=?", (pid,))}


def do_send_message(other, text, event_id=None):
    text = (text or "").strip()[:2000]
    if not text and not event_id:
        return {"error": "Type a message first."}
    if event_id:
        try:
            event_id = int(event_id)
        except (TypeError, ValueError):
            event_id = None
        if event_id and not scalar("SELECT 1 FROM events WHERE id=? AND status!='draft' AND is_removed=0", (event_id,)):
            event_id = None
    if not core.can_message(g.user, other):
        return {"error": f"{other['name']} only accepts messages from friends." if other["allow_messages"] == "friends"
                else f"{other['name']} isn't accepting messages."}
    mid = ex("INSERT INTO messages (sender_id, recipient_id, body, event_id) VALUES (?,?,?,?)",
             (g.user["id"], other["id"], text, event_id))
    return {"ok": True, "id": mid}


# ------------------------------------------------------------------ endpoints
@bp.route("/posts/<int:pid>/like", methods=["POST"])
@core.login_required
def like(pid):
    post = q("SELECT * FROM posts WHERE id=? AND is_removed=0", (pid,), one=True)
    if not post:
        return jsonify(error="Post not found."), 404
    if scalar("SELECT 1 FROM post_likes WHERE post_id=? AND user_id=?", (pid, g.user["id"])):
        if body().get("only_like"):
            liked = True
        else:
            ex("DELETE FROM post_likes WHERE post_id=? AND user_id=?", (pid, g.user["id"]))
            liked = False
    else:
        ex("INSERT INTO post_likes (post_id, user_id) VALUES (?,?)", (pid, g.user["id"]))
        liked = True
        n = scalar("SELECT COUNT(*) FROM post_likes WHERE post_id=?", (pid,))
        if n in (1, 10, 50, 100) or n % 250 == 0:
            core.notify(post["author_id"], "like", f"{g.user['name']}{'' if n == 1 else f' and {n - 1} others'} liked your post.",
                        url_for("social.post_view", pid=pid), g.user["id"])
    return jsonify(liked=liked, likes=scalar("SELECT COUNT(*) FROM post_likes WHERE post_id=?", (pid,)))


@bp.route("/posts/<int:pid>/save", methods=["POST"])
@core.login_required
def save_post(pid):
    if scalar("SELECT 1 FROM post_saves WHERE post_id=? AND user_id=?", (pid, g.user["id"])):
        ex("DELETE FROM post_saves WHERE post_id=? AND user_id=?", (pid, g.user["id"]))
        return jsonify(saved=False)
    if not scalar("SELECT 1 FROM posts WHERE id=?", (pid,)):
        return jsonify(error="Post not found."), 404
    ex("INSERT INTO post_saves (post_id, user_id) VALUES (?,?)", (pid, g.user["id"]))
    return jsonify(saved=True)


@bp.route("/posts/<int:pid>/comments", methods=["POST"])
@core.login_required
def comment(pid):
    res = do_comment(pid, body().get("body"))
    if res.get("error"):
        return jsonify(res), 400
    res.update(name=g.user["name"], username=g.user["username"])
    return jsonify(res)


@bp.route("/events/<int:eid>/save", methods=["POST"])
@core.login_required
def save_event(eid):
    if scalar("SELECT 1 FROM event_saves WHERE event_id=? AND user_id=?", (eid, g.user["id"])):
        ex("DELETE FROM event_saves WHERE event_id=? AND user_id=?", (eid, g.user["id"]))
        return jsonify(saved=False)
    if not scalar("SELECT 1 FROM events WHERE id=? AND status!='draft' AND is_removed=0", (eid,)):
        return jsonify(error="Event not found."), 404
    ex("INSERT INTO event_saves (event_id, user_id) VALUES (?,?)", (eid, g.user["id"]))
    return jsonify(saved=True)


@bp.route("/follow/<int:cid>", methods=["POST"])
@core.login_required
def follow(cid):
    res = do_follow(cid)
    return (jsonify(res), 400) if res.get("error") else jsonify(res)


@bp.route("/friends/<int:uid>/<action>", methods=["POST"])
@core.login_required
def friend_action(uid, action):
    me = g.user
    other = q("SELECT * FROM users WHERE id=? AND status='active'", (uid,), one=True)
    if not other or other["id"] == me["id"] or other["role"] != "student" or me["role"] != "student":
        return jsonify(error="Friends are between student accounts."), 400
    status = core.friend_status(me, uid)
    pair = ("(requester_id=? AND addressee_id=?) OR (requester_id=? AND addressee_id=?)", (me["id"], uid, uid, me["id"]))
    if action == "request" and status == "none":
        ex("INSERT INTO friendships (requester_id, addressee_id) VALUES (?,?)", (me["id"], uid))
        core.notify(uid, "friend_request", f"{me['name']} sent you a friend request.", url_for("social.friends", tab="requests"), me["id"])
        status = "outgoing"
    elif action == "request" and status == "incoming":
        action = "accept"
    if action == "accept" and status == "incoming":
        ex("UPDATE friendships SET status='accepted' WHERE requester_id=? AND addressee_id=?", (uid, me["id"]))
        core.notify(uid, "friend_accept", f"{me['name']} accepted your friend request.",
                    url_for("social.profile", username=me["username"]), me["id"])
        status = "friends"
    elif action in ("decline", "cancel", "remove") and status in ("incoming", "outgoing", "friends"):
        ex(f"DELETE FROM friendships WHERE {pair[0]}", pair[1])
        status = "none"
    return jsonify(status=status, friends=len(core.friend_ids(me["id"])))


@bp.route("/messages/<username>", methods=["GET", "POST"])
@core.login_required
def messages(username):
    other = q("SELECT * FROM users WHERE username=?", (username,), one=True)
    if not other:
        return jsonify(error="User not found."), 404
    if request.method == "POST":
        data = body()
        res = do_send_message(other, data.get("body"), data.get("event_id"))
        return (jsonify(res), 400) if res.get("error") else jsonify(res)
    after = request.args.get("after", 0, type=int)
    rows = q("""SELECT m.id, m.sender_id, m.body, m.created_at, m.event_id, e.title event_title FROM messages m
                LEFT JOIN events e ON e.id=m.event_id
                WHERE m.id>? AND ((sender_id=? AND recipient_id=?) OR (sender_id=? AND recipient_id=?)) ORDER BY m.id""",
             (after, g.user["id"], other["id"], other["id"], g.user["id"]))
    ex("UPDATE messages SET read_at=? WHERE sender_id=? AND recipient_id=? AND read_at IS NULL",
       (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), other["id"], g.user["id"]))
    return jsonify(messages=[{**dict(r), "mine": r["sender_id"] == g.user["id"],
                              "time": core.parse_dt(r["created_at"]).strftime("%I:%M %p").lstrip("0"),
                              "event_url": url_for("events.detail", eid=r["event_id"]) if r["event_id"] else None}
                             for r in rows])


@bp.route("/share/<int:eid>", methods=["POST"])
@core.login_required
def share_event(eid):
    """Send an event to one or more friends as a chat message."""
    event = q("SELECT * FROM events WHERE id=?", (eid,), one=True)
    if not event:
        return jsonify(error="Event not found."), 404
    sent = 0
    for uid in (body().get("to") or [])[:20]:
        other = q("SELECT * FROM users WHERE id=?", (uid,), one=True)
        if other and not do_send_message(other, f"Check this out: {event['title']}", eid).get("error"):
            sent += 1
    return jsonify(sent=sent)


@bp.route("/counts")
def counts():
    if not g.user:
        return jsonify(notifications=0, messages=0, requests=0)
    uid = g.user["id"]
    extra = {}
    if g.user["role"] == "student":               # keep the live event-day notice current while the app is open
        import live
        extra["live"] = live.refresh_user(uid)
    return jsonify(
        **extra,
        notifications=scalar("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0", (uid,)),
        messages=scalar("SELECT COUNT(*) FROM messages WHERE recipient_id=? AND read_at IS NULL", (uid,)),
        requests=scalar("SELECT COUNT(*) FROM friendships WHERE addressee_id=? AND status='pending'", (uid,)),
    )


@bp.route("/search")
def search():
    text = (request.args.get("q") or "").strip().lstrip("#@")
    if len(text) < 1:
        return jsonify(results=[])
    like = f"%{text}%"
    out = []
    for u in q("""SELECT name, username, role, avatar, verification, college_name, city FROM users
                  WHERE status='active' AND role!='dev' AND (name LIKE ? OR username LIKE ?)
                  ORDER BY role='college' DESC, name LIMIT 6""", (like, like)):
        out.append({"type": u["role"], "title": u["name"], "sub": "@" + u["username"] + (f" · {u['city']}" if u["city"] else ""),
                    "url": url_for("social.profile", username=u["username"]),
                    "avatar": url_for("media", rel=u["avatar"]) if u["avatar"] else None,
                    "verified": u["verification"] == "verified"})
    for e in q("""SELECT e.id, e.title, e.start_dt, e.category, e.kind, u.name college,
                         (SELECT title FROM events f WHERE f.id=e.parent_id) fest FROM events e JOIN users u ON u.id=e.college_id
                  WHERE e.status!='draft' AND e.is_removed=0 AND (e.title LIKE ? OR e.tagline LIKE ? OR e.category LIKE ?)
                  ORDER BY e.end_dt >= datetime('now','localtime') DESC, e.start_dt LIMIT 6""", (like, like, like)):
        out.append({"type": "event", "title": e["title"],
                    "sub": f"{e['fest'] or e['college']} · {core.parse_dt(e['start_dt']).strftime('%d %b')}",
                    "url": url_for("events.detail", eid=e["id"]), "emoji": core.CATEGORY_EMOJI.get(e["category"], "✨")})
    return jsonify(results=out)


@bp.route("/coupon/<int:eid>", methods=["POST"])
@core.login_required
def coupon(eid):
    from blueprints.events import price_for
    event = q("SELECT * FROM events WHERE id=? AND status='open' AND is_removed=0", (eid,), one=True)
    if not event:
        return jsonify(error="Event not found."), 404
    res = price_for(event, body().get("code"))
    return jsonify(res)


@bp.route("/ask", methods=["POST"])
def ask():
    data = body()
    history = data.get("history") if isinstance(data.get("history"), list) else None
    return jsonify(ai_agent.answer(data.get("question", ""), g.user, history, data.get("event_id")))


@bp.route("/events/<int:eid>")
def event_json(eid):
    """Event or fest as JSON (for the Android app). Fests include tracks, prices and each event."""
    import fest as festlib
    e = q("""SELECT e.*, u.name college, u.username college_username FROM events e JOIN users u ON u.id=e.college_id
             WHERE e.id=? AND e.status!='draft' AND e.is_removed=0""", (eid,), one=True)
    if not e:
        return jsonify(error="Event not found."), 404
    base = lambda x: {"id": x["id"], "title": x["title"], "category": x["category"], "start": x["start_dt"], "end": x["end_dt"],
                      "venue": x["venue"], "fee": x["fee"], "fee_type": x["fee_type"], "team_size": x["team_size"],
                      "capacity": x["capacity"], "status": x["status"], "label": x["label"],
                      "banner": url_for("media", rel=x["banner"], _external=True) if x["banner"] else None,
                      "url": url_for("events.detail", eid=x["id"], _external=True)}
    out = dict(base(e), kind=e["kind"], tagline=e["tagline"], description=e["description"], college=e["college"],
               college_username=e["college_username"], parent_id=e["parent_id"])
    if e["kind"] == "fest":
        me = g.user if g.get("user") and g.user["role"] == "student" else None
        out["tracks"] = [{"id": gr["t"]["id"], "name": gr["t"]["name"], "emoji": gr["t"]["emoji"], "pricing": gr["t"]["pricing"],
                          "pass_fee": gr["t"]["pass_fee"], "has_pass": gr["has_pass"],
                          "events": [dict(base(it["e"]), open=it["open"], seats_left=it["left"],
                                          registered=bool(it["mine"]), ticket=it["mine"]["pass_code"] if it["mine"] else None)
                                     for it in gr["items"]]}
                         for gr in festlib.structure(eid, me)]
        out["register_url"] = url_for("events.join", eid=eid, _external=True)
    return jsonify(out)
