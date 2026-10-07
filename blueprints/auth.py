"""Login portals, sign-up (student / college), password reset."""
import re
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, g
from werkzeug.security import generate_password_hash, check_password_hash

import core
from db import q, ex, scalar

bp = Blueprint("auth", __name__)

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.]{3,30}$")
RESERVED = {"admin", "dev", "studio", "api", "explore", "login", "signup", "settings", "events", "media", "static",
            "eventflow", "support", "help", "root", "system", "u", "c", "messages", "notifications"}


def _login(user, remember=True):
    session.clear()
    session["uid"] = user["id"]
    session.permanent = remember


def _safe_next(default):
    nxt = request.args.get("next") or request.form.get("next") or ""
    return nxt if nxt.startswith("/") and not nxt.startswith("//") else default


def home_for(user):
    if user["role"] == "dev":
        return url_for("dev.overview")
    if user["role"] == "college":
        return url_for("studio.overview")
    return url_for("social.home")


def validate_common(form, require_phone=False):
    errors = {}
    name = (form.get("name") or "").strip()
    username = (form.get("username") or "").strip().lstrip("@")
    email = (form.get("email") or "").strip().lower()
    phone = re.sub(r"[^\d+]", "", form.get("phone") or "")
    pw = form.get("password") or ""
    if len(name) < 2:
        errors["name"] = "Enter your full name."
    if not USERNAME_RE.match(username):
        errors["username"] = "3–30 characters: letters, numbers, dots or underscores."
    elif username.lower() in RESERVED:
        errors["username"] = "That username is reserved."
    elif scalar("SELECT 1 FROM users WHERE username=?", (username,)):
        errors["username"] = "That username is taken."
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        errors["email"] = "Enter a valid email address."
    elif scalar("SELECT 1 FROM users WHERE email=?", (email,)):
        errors["email"] = "An account with this email already exists."
    if phone:
        if not re.match(r"^\+?\d{10,13}$", phone):
            errors["phone"] = "Enter a 10-digit mobile number."
        elif scalar("SELECT 1 FROM users WHERE phone=?", (phone,)):
            errors["phone"] = "This mobile number is already registered."
    elif require_phone:
        errors["phone"] = "Mobile number is required."
    if len(pw) < 8:
        errors["password"] = "Use at least 8 characters."
    elif pw.isdigit() or pw.isalpha():
        errors["password"] = "Mix letters and numbers."
    return {"name": name, "username": username, "email": email, "phone": phone or None, "password": pw}, errors


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(home_for(g.user))
    portal = request.args.get("as", "student")
    if request.method == "POST":
        ident = (request.form.get("identifier") or "").strip().lstrip("@")
        phone = re.sub(r"[^\d+]", "", ident)
        user = q("SELECT * FROM users WHERE email=? OR username=? OR (phone IS NOT NULL AND phone=?)",
                 (ident.lower(), ident, phone or "-"), one=True)
        if user and check_password_hash(user["password_hash"], request.form.get("password") or ""):
            if user["status"] != "active":
                flash("This account is suspended. Contact the EventFlow team.", "error")
            else:
                _login(user, bool(request.form.get("remember")))
                flash(f"Welcome back, {user['name'].split()[0]}!", "success")
                return redirect(_safe_next(home_for(user)))
        else:
            flash("That login didn't match. Check your email, username or mobile and password.", "error")
    return render_template("auth/login.html", portal=portal)


@bp.route("/signup")
def signup_choice():
    if g.user:
        return redirect(home_for(g.user))
    return render_template("auth/signup_choice.html")


@bp.route("/signup/student", methods=["GET", "POST"])
def signup_student():
    if g.user:
        return redirect(home_for(g.user))
    if core.setting("signups_open") != "1":
        flash("New sign-ups are paused right now. Please try again later.", "info")
        return redirect(url_for("auth.login"))
    form, errors = request.form, {}
    if request.method == "POST":
        data, errors = validate_common(form)
        if not request.form.get("agree"):
            errors["agree"] = "Please accept the community guidelines."
        if not errors:
            uid = ex("""INSERT INTO users (role, username, name, email, phone, password_hash, college_name, department, year, city)
                        VALUES ('student',?,?,?,?,?,?,?,?,?)""",
                     (data["username"], data["name"], data["email"], data["phone"], generate_password_hash(data["password"]),
                      (form.get("college_name") or "").strip()[:120], form.get("department"), form.get("year"),
                      (form.get("city") or "").strip()[:60]))
            user = q("SELECT * FROM users WHERE id=?", (uid,), one=True)
            _login(user)
            flash("Welcome to EventFlow! Follow a few colleges to fill your feed.", "success")
            return redirect(url_for("social.explore", tab="colleges", welcome=1))
    return render_template("auth/signup_student.html", form=form, errors=errors)


@bp.route("/signup/college", methods=["GET", "POST"])
def signup_college():
    if g.user:
        return redirect(home_for(g.user))
    if core.setting("college_signups_open") != "1":
        flash("College registrations are paused right now.", "info")
        return redirect(url_for("auth.login", **{"as": "college"}))
    form, errors = request.form, {}
    if request.method == "POST":
        data, errors = validate_common(form, require_phone=True)
        city = (form.get("city") or "").strip()
        if len(city) < 2:
            errors["city"] = "Enter your city."
        upi = (form.get("upi_id") or "").strip()
        if upi and not re.match(r"^[\w.\-]{2,}@[A-Za-z]{2,}$", upi):
            errors["upi_id"] = "UPI IDs look like name@bank."
        if not request.form.get("agree"):
            errors["agree"] = "Please confirm you represent this institution."
        if not errors:
            uid = ex("""INSERT INTO users (role, username, name, email, phone, password_hash, city, website, upi_id, bio,
                                           verification, allow_messages, show_events)
                        VALUES ('college',?,?,?,?,?,?,?,?,?,'pending','everyone','nobody')""",
                     (data["username"], data["name"], data["email"], data["phone"], generate_password_hash(data["password"]),
                      city, core.clean_url(form.get("website")), upi or None, (form.get("bio") or "").strip()[:300]))
            dev = scalar("SELECT id FROM users WHERE role='dev'")
            core.notify(dev, "verify", f"{data['name']} asked to be verified.", url_for("dev.colleges", tab="pending"), uid)
            user = q("SELECT * FROM users WHERE id=?", (uid,), one=True)
            _login(user)
            flash("Your college is on EventFlow. Verification is pending; you can set up events meanwhile.", "success")
            return redirect(url_for("studio.overview"))
    return render_template("auth/signup_college.html", form=form, errors=errors)


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You're logged out. See you soon!", "info")
    return redirect(url_for("auth.login"))


@bp.route("/forgot", methods=["GET", "POST"])
def forgot():
    sent = False
    if request.method == "POST":
        ident = (request.form.get("identifier") or "").strip().lstrip("@")
        user = q("SELECT * FROM users WHERE email=? OR username=? OR phone=?", (ident.lower(), ident, ident), one=True)
        if user:
            token = secrets.token_urlsafe(24)
            ex("INSERT INTO password_resets (token, user_id, expires_at) VALUES (?,?,?)",
               (token, user["id"], (datetime.now() + timedelta(hours=1)).isoformat(timespec="seconds")))
            link = url_for("auth.reset", token=token, _external=True)
            # No email server in this build: the link goes to the server console only, never to the browser.
            print(f"\n[EventFlow] Password reset link for {user['email']}:\n  {link}\n", flush=True)
        sent = True
    return render_template("auth/forgot.html", sent=sent)


@bp.route("/reset/<token>", methods=["GET", "POST"])
def reset(token):
    row = q("SELECT * FROM password_resets WHERE token=?", (token,), one=True)
    if not row or core.parse_dt(row["expires_at"]) < datetime.now():
        flash("That reset link has expired. Request a new one.", "error")
        return redirect(url_for("auth.forgot"))
    if request.method == "POST":
        pw = request.form.get("password") or ""
        if len(pw) < 8 or pw.isdigit() or pw.isalpha():
            flash("Use at least 8 characters with letters and numbers.", "error")
        elif pw != request.form.get("confirm"):
            flash("The two passwords don't match.", "error")
        else:
            ex("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(pw), row["user_id"]))
            ex("DELETE FROM password_resets WHERE user_id=?", (row["user_id"],))
            flash("Password changed. Log in with your new password.", "success")
            return redirect(url_for("auth.login"))
    return render_template("auth/reset.html")
