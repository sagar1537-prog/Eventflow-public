"""EventFlow: the campus events community.

Run:  python app.py      (or run.bat on Windows)
"""
import os
import re
import secrets
import sys
from datetime import datetime

from flask import (Flask, g, session, request, redirect, url_for, render_template, flash, jsonify,
                   send_from_directory, abort, stream_with_context)
from markupsafe import Markup
from werkzeug.middleware.proxy_fix import ProxyFix

import core
import db
from db import q, ex

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MEDIA_CHUNK = 2 * 1024 * 1024      # bytes per piece when streaming uploads out of PostgreSQL


def _secret_key(instance_path):
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    path = os.path.join(instance_path, "secret.key")
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    with open(path, "w") as f:
        f.write(key)
    return key


def create_app():
    app = Flask(__name__, instance_path=os.path.join(BASE_DIR, "instance"))
    os.makedirs(app.instance_path, exist_ok=True)
    app.config.update(
        SECRET_KEY=_secret_key(app.instance_path),
        # the SQLite file used when DATABASE_URL is not set (running on your own computer)
        DATABASE=os.environ.get("EVENTFLOW_DB", os.path.join(app.instance_path, "eventflow.db")),
        MAX_CONTENT_LENGTH=120 * 1024 * 1024,
        SEED_DEMO=os.environ.get("EVENTFLOW_DEMO", "1") == "1",
        # "1" = clean demo (accounts, friends, follows; no events/posts/registrations) — ready for a live walkthrough.
        # set EVENTFLOW_FULL_DEMO=1 to also seed sample events, posts, registrations and payments.
        SEED_FULL=os.environ.get("EVENTFLOW_FULL_DEMO", "0") == "1",
        DEV_EMAIL=os.environ.get("DEV_EMAIL", "dev@eventflow.app"),
        DEV_PASSWORD=os.environ.get("DEV_PASSWORD", "dev12345"),
        DEV_NAME=os.environ.get("DEV_NAME", "Vidhya Sagar"),
        DEV_USERNAME=os.environ.get("DEV_USERNAME", "vidhyasagar"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=bool(os.environ.get("RENDER")),   # Render serves https only
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
        SEND_FILE_MAX_AGE_DEFAULT=0,
    )
    # behind Render's proxy: trust its https / host headers so links, QR codes and cookies use https
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    db.init_app(app)
    core.register_filters(app)

    with app.app_context(), db.setup_lock():
        db.create_schema()
        from seed import seed
        seed(app)

    # ------------------------------------------------------------ hooks
    @app.before_request
    def load_user():
        g.user = None
        if request.endpoint in ("static", "media", "manifest", "healthz"):
            return  # files never need the database (saves a database round trip per image)
        uid = session.get("uid")
        if uid:
            user = q("SELECT * FROM users WHERE id=?", (uid,), one=True)
            if user and user["status"] == "active":
                g.user = user
                last = core.parse_dt(user["last_seen"])
                if not last or (datetime.now() - last).total_seconds() > 60:
                    ex("UPDATE users SET last_seen=? WHERE id=?", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), uid))
            else:
                session.clear()
                if user and user["status"] == "suspended":
                    flash("This account is suspended. Contact the EventFlow team.", "error")

    @app.before_request
    def maintenance_gate():
        if request.endpoint in ("static", "media", "auth.login", "auth.logout") or (g.user and g.user["role"] == "dev"):
            return None
        if core.setting("maintenance") == "1":
            return render_template("maintenance.html"), 503
        return None

    @app.before_request
    def csrf_protect():
        if request.method != "POST":
            return None
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        if sent and sent == session.get("_csrf"):
            return None
        if request.path.startswith("/api/") or request.is_json:
            return jsonify(error="Your session expired. Refresh the page and try again."), 400
        flash("Your session expired. Please try again.", "error")
        return redirect(request.referrer or url_for("social.home"))

    static = os.path.join(app.root_path, "static")
    ASSET_V = int(max(os.path.getmtime(os.path.join(static, f)) for f in ("css/app.css", "js/app.js")))

    @app.context_processor
    def inject():
        def csrf_token():
            if "_csrf" not in session:
                session["_csrf"] = secrets.token_hex(16)
            return session["_csrf"]
        ctx = {
            "csrf_token": csrf_token,
            "csrf_field": lambda: Markup(f'<input type="hidden" name="csrf_token" value="{csrf_token()}">'),
            "now": datetime.now(),
            "setting": core.setting,
            "CATEGORIES": core.CATEGORIES,
            "unread_notifs": 0, "unread_msgs": 0, "friend_requests": 0, "is_studio": False, "my_points": None,
            "theme": "system",
        }
        if g.get("user"):
            uid = g.user["id"]
            counts = q("""SELECT (SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0),
                                 (SELECT COUNT(*) FROM messages WHERE recipient_id=? AND read_at IS NULL),
                                 (SELECT COUNT(*) FROM friendships WHERE addressee_id=? AND status='pending')""",
                       (uid, uid, uid), one=True)  # one round trip for all three header badges
            ctx.update(
                unread_notifs=counts[0], unread_msgs=counts[1], friend_requests=counts[2],
                is_studio=core.is_studio_user(g.user),
                theme=g.user["theme"],
            )
            if g.user["role"] == "student":
                ctx["my_points"] = core.points_summary(uid)
        return ctx

    # ------------------------------------------------------------ media & app shell
    SEED_MEDIA = os.path.join(BASE_DIR, "seed_media")

    @app.route("/media/<path:rel>")
    def media(rel):
        if rel.startswith("seed/"):                        # demo pictures ship with the code
            resp = send_from_directory(SEED_MEDIA, rel[5:], conditional=True)
        elif db.is_postgres():                             # Render: uploads live in the database
            resp = _db_media(rel)
        else:
            root = os.path.join(app.instance_path, "uploads")
            if not os.path.isfile(os.path.normpath(os.path.join(root, rel))):
                abort(404)
            resp = send_from_directory(root, rel, conditional=True)
        resp.headers["Cache-Control"] = "public, max-age=86400"
        return resp

    def _db_media(rel):
        """Serve an upload stored in PostgreSQL, with HTTP ranges so videos stream and seek in every browser."""
        meta = db.file_meta(rel)
        if not meta:
            abort(404)
        size, etag = meta["size"], '"' + rel.replace("/", "-") + '"'
        if request.headers.get("If-None-Match") == etag:
            return app.response_class(status=304, headers={"ETag": etag})
        rng = re.match(r"bytes=(\d*)-(\d*)$", request.headers.get("Range", ""))
        if rng and size:
            a, b = rng.groups()
            if a == "":                                    # suffix range: the last N bytes
                start = max(0, size - int(b or 0))
                end = size - 1
            else:
                start = int(a)
                end = min(int(b), size - 1) if b else min(size - 1, start + MEDIA_CHUNK - 1)
            if start >= size or end < start:
                return app.response_class(status=416, headers={"Content-Range": f"bytes */{size}"})
            body = db.file_read(rel, start, end - start + 1)
            resp = app.response_class(body, status=206, mimetype=meta["mime"])
            resp.headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        elif size > MEDIA_CHUNK:                           # big file, no range: stream it in pieces
            def pieces():
                for pos in range(0, size, MEDIA_CHUNK):
                    yield db.file_read(rel, pos, MEDIA_CHUNK)
            resp = app.response_class(stream_with_context(pieces()), mimetype=meta["mime"])
            resp.headers["Content-Length"] = str(size)
        else:
            resp = app.response_class(db.file_read(rel), mimetype=meta["mime"])
        resp.headers["Accept-Ranges"] = "bytes"
        resp.headers["ETag"] = etag
        return resp

    @app.route("/manifest.webmanifest")
    def manifest():
        return jsonify({
            "name": "EventFlow", "short_name": "EventFlow", "start_url": "/", "display": "standalone",
            "background_color": "#0F1424", "theme_color": "#0F1424",
            "icons": [{"src": url_for("static", filename="img/icon.png"), "sizes": "512x512", "type": "image/png"}],
        })

    @app.route("/healthz")
    def healthz():
        return {"ok": True, "time": datetime.now().isoformat(timespec="seconds")}

    # ------------------------------------------------------------ errors
    def err(code, title, message):
        if request.path.startswith("/api/"):
            return jsonify(error=message), code
        return render_template("error.html", code=code, title=title, message=message), code

    app.register_error_handler(403, lambda e: err(403, "No access", "This page belongs to another account or team."))
    app.register_error_handler(404, lambda e: err(404, "Not found", "That page, post or event doesn't exist anymore."))
    app.register_error_handler(413, lambda e: err(413, "File too large", "Uploads can be up to 120 MB in total."))
    app.register_error_handler(500, lambda e: err(500, "Something broke", "Please try again. If it keeps happening, tell the EventFlow team."))

    # ------------------------------------------------------------ blueprints
    from blueprints.auth import bp as auth_bp
    from blueprints.social import bp as social_bp
    from blueprints.events import bp as events_bp
    from blueprints.studio import bp as studio_bp
    from blueprints.dev import bp as dev_bp
    from blueprints.api import bp as api_bp
    for bp in (auth_bp, social_bp, events_bp, studio_bp, dev_bp, api_bp):
        app.register_blueprint(bp)
    return app


try:
    app = create_app()
except db.ConfigError as err:
    print("\n  EventFlow could not start: database problem\n")
    print("  " + str(err))
    print("\n  On Render: check that the database in render.yaml was created and is available.")
    print("  On your computer: remove the DATABASE_URL environment variable to use the built-in SQLite file.\n")
    sys.exit(1)

if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", 5000))
    print(f"\n  Database: {app.config['DB_LABEL']}")
    print(f"  EventFlow is running:  http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}\n")
    app.run(host=host, port=port, debug=os.environ.get("FLASK_DEBUG", "0") == "1", threaded=True)
