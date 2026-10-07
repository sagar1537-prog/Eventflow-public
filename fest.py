"""Fests: one parent event with tracks (Technical, Non-technical, Sports...) and many sub-events.

Pricing, decided per track:
  * 'event' - each sub-event has its own fee. A sub-event can charge per person or per team
              (the team captain pays once; teammates join free once the captain's ticket is confirmed).
  * 'pass'  - one pass fee covers every sub-event in the track. The first registration in the track
              carries the pass (covers='pass'); later ones in the same track cost nothing.

Every sub-event is a normal event (own seats, ticket QR, check-in, winners, certificates), so all the
existing studio tools work per sub-event. Several sub-events picked together are paid in one go:
their payment rows share a `bundle` code and are submitted, approved or rejected together.
"""
from db import q, ex, scalar, in_clause
import core

HELD = ("confirmed", "payment_review", "pending_payment")   # statuses that keep a pass / team alive

TEMPLATE = [
    # (track, emoji, blurb, pricing, pass_fee, [(title, category, fee, fee_type, team_size, label)])
    ("Technical events", "💻", "Challenge your skills. Build. Solve. Compete.", "pass", 75, [
        ("Hackathon", "Technical", 0, "person", 4, None), ("Tech Maze", "Technical", 0, "person", 1, None),
        ("Debugging", "Technical", 0, "person", 1, None), ("Logo Designing", "Technical", 0, "person", 1, None),
        ("Tech Quiz", "Technical", 0, "person", 2, None), ("Paper Presentation", "Technical", 0, "person", 2, None)]),
    ("Non-technical events", "🎭", "Show your talent, creativity, confidence and teamwork.", "event", 0, [
        ("Ramp Walk", "Cultural", 75, "person", 1, None), ("Solo / Group Dance", "Cultural", 75, "person", 6, None),
        ("TuneTopia", "Cultural", 75, "person", 1, None), ("Treasure Hunt", "Cultural", 75, "person", 4, None),
        ("Mobile Gaming", "Gaming", 75, "person", 4, "BGMI · Free Fire"), ("AdapTune", "Cultural", 75, "person", 1, None),
        ("Singing (Solo)", "Cultural", 75, "person", 1, None), ("Connexion", "Cultural", 75, "person", 2, None),
        ("Squid Game", "Cultural", 75, "person", 1, None), ("Pass the Ball", "Cultural", 75, "person", 1, None)]),
    ("Sports", "🏆", "Team fee. Valid college ID needed. Played on both fest days.", "event", 0, [
        ("Cricket (Tennis Ball)", "Sports", 600, "team", 11, "Boys only"), ("Football (7s)", "Sports", 600, "team", 7, "Boys only"),
        ("Volleyball", "Sports", 600, "team", 6, "Boys only"), ("Kabaddi", "Sports", 600, "team", 7, "Boys only"),
        ("Kho-Kho", "Sports", 600, "team", 9, "Boys & Girls"), ("Throwball", "Sports", 600, "team", 7, "Boys & Girls"),
        ("Chess", "Sports", 150, "team", 2, "Boys & Girls"), ("Carrom", "Sports", 150, "team", 2, "Boys & Girls")]),
]


# ------------------------------------------------------------------ reading
def get_fest(event):
    """The parent fest of a sub-event (or the event itself if it is a fest), else None."""
    if not event:
        return None
    if event["kind"] == "fest":
        return event
    if event["parent_id"]:
        return q("SELECT * FROM events WHERE id=?", (event["parent_id"],), one=True)
    return None


def tracks(fest_id):
    return q("SELECT * FROM fest_tracks WHERE fest_id=? ORDER BY position, id", (fest_id,))


def sub_events(fest_id, include_drafts=False):
    extra = "" if include_drafts else "AND e.status!='draft'"
    return q(f"""SELECT e.*,
                   (SELECT COUNT(*) FROM registrations r WHERE r.event_id=e.id AND r.status IN ('confirmed','payment_review')) taken,
                   (SELECT COUNT(*) FROM registrations r WHERE r.event_id=e.id AND r.status='confirmed') reg_count,
                   (SELECT COUNT(*) FROM registrations r WHERE r.event_id=e.id AND r.attended=1) att_count,
                   (SELECT COALESCE(SUM(p.amount),0) FROM payments p WHERE p.event_id=e.id AND p.status='paid') revenue,
                   (SELECT COUNT(*) FROM payments p WHERE p.event_id=e.id AND p.status='submitted') review_count
                 FROM events e WHERE e.parent_id=? AND e.is_removed=0 {extra} ORDER BY e.position, e.id""", (fest_id,))


def track_of(event):
    return q("SELECT * FROM fest_tracks WHERE id=?", (event["track_id"],), one=True) if event["track_id"] else None


def holds_pass(user_id, track_id, exclude_reg=None):
    """The user's registration that carries this track's pass, if any (confirmed or on its way)."""
    return q(f"""SELECT r.* FROM registrations r JOIN events e ON e.id=r.event_id
                 WHERE r.user_id=? AND e.track_id=? AND r.covers='pass' AND r.status IN {HELD} AND r.id!=?
                 ORDER BY r.id LIMIT 1""", (user_id, track_id, exclude_reg or 0), one=True)


def team_captain(event_id, team):
    return q("""SELECT r.*, u.name FROM registrations r JOIN users u ON u.id=r.user_id
                WHERE r.event_id=? AND r.team_name=? COLLATE NOCASE AND r.covers='team' AND r.status IN
                ('confirmed','payment_review','pending_payment') ORDER BY r.id LIMIT 1""", (event_id, team), one=True)


def structure(fest_id, user=None, include_drafts=False):
    """Tracks with their sub-events, plus what this user already has (for the fest page)."""
    subs = sub_events(fest_id, include_drafts)
    mine = {}
    if user:
        c, a = in_clause([s["id"] for s in subs])
        for r in q(f"""SELECT * FROM registrations WHERE user_id=? AND event_id IN {c} AND status!='cancelled'""", [user["id"]] + a):
            mine[r["event_id"]] = r
    out = []
    for t in tracks(fest_id):
        items = []
        for s in subs:
            if s["track_id"] != t["id"]:
                continue
            open_, reason = core.reg_state(s, s["taken"])
            items.append({"e": s, "open": open_, "reason": reason, "left": max(0, s["capacity"] - s["taken"]), "mine": mine.get(s["id"])})
        has_pass = bool(user and t["pricing"] == "pass" and holds_pass(user["id"], t["id"]))
        out.append({"t": t, "items": items, "has_pass": has_pass})
    loose = [s for s in subs if not s["track_id"] or s["track_id"] not in {t["t"]["id"] for t in out}]
    if loose:
        out.append({"t": {"id": 0, "name": "More events", "emoji": "✨", "blurb": "", "pricing": "event", "pass_fee": 0},
                    "items": [{"e": s, "open": core.reg_state(s, s["taken"])[0], "reason": core.reg_state(s, s["taken"])[1],
                               "left": max(0, s["capacity"] - s["taken"]), "mine": mine.get(s["id"])} for s in loose],
                    "has_pass": False})
    return out


def price_label(sub, track):
    if track and track["pricing"] == "pass":
        return "Included in pass"
    if not sub["fee"]:
        return "Free"
    return f"₹{sub['fee']:,}" + (" / team" if sub["fee_type"] == "team" else "")


def quote(fest, picks, user):
    """picks: [(sub_event_row, team_name)] -> (items, errors). Each item: event, track, amount, covers, note, team."""
    items, errors = [], []
    pass_in_quote = set()
    for sub, team in picks:
        tr = track_of(sub)
        open_, reason = core.reg_state(sub)
        existing = q("SELECT * FROM registrations WHERE event_id=? AND user_id=?", (sub["id"], user["id"]), one=True)
        if existing and existing["status"] in HELD + ("waitlisted",):
            errors.append(f"You're already registered for {sub['title']}.")
            continue
        if not open_:
            errors.append(f"{sub['title']}: {reason}")
            continue
        if sub["team_size"] > 1 and not team:
            errors.append(f"{sub['title']} is a team event. Enter your team name.")
            continue
        if team and sub["team_size"] > 1:
            members = scalar("""SELECT COUNT(*) FROM registrations WHERE event_id=? AND team_name=? COLLATE NOCASE
                                AND status IN ('confirmed','payment_review','pending_payment') AND user_id!=?""", (sub["id"], team, user["id"]))
            if members >= sub["team_size"]:
                errors.append(f"Team “{team}” in {sub['title']} already has {sub['team_size']} members.")
                continue
        item = {"event": sub, "track": tr, "team": team if sub["team_size"] > 1 else None, "amount": 0, "covers": None, "note": ""}
        if tr and tr["pricing"] == "pass":
            held = holds_pass(user["id"], tr["id"])
            if held and held["status"] != "confirmed" and tr["id"] not in pass_in_quote:
                errors.append(f"Your {tr['name']} pass payment isn't confirmed yet. Finish it from My tickets, then add more events.")
                continue
            if tr["id"] in pass_in_quote or held:
                item["note"] = f"Included in your {tr['name']} pass"
            else:
                item.update(amount=tr["pass_fee"], covers="pass", note=f"{tr['name']} pass · covers every event in it")
                pass_in_quote.add(tr["id"])
                if not tr["pass_fee"]:
                    item["note"] = f"{tr['name']} · free"
        elif sub["fee_type"] == "team" and sub["team_size"] > 1:
            cap = team_captain(sub["id"], team)
            if cap:
                if cap["status"] != "confirmed":
                    errors.append(f"Team “{team}” in {sub['title']}: the captain ({cap['name']}) hasn't finished paying yet. "
                                  f"Join once their ticket is confirmed.")
                    continue
                item["note"] = f"Team {team} · fee paid by {cap['name'].split()[0]}"
            else:
                item.update(amount=sub["fee"], covers="team" if sub["fee"] else None,
                            note=f"Team fee for {team} (you're the captain)" if sub["fee"] else f"Team {team}")
        else:
            item.update(amount=sub["fee"], note="Entry fee" if sub["fee"] else "Free")
        items.append(item)
    return items, errors


def dependents(reg):
    """Registrations that only exist thanks to this one (its track pass or its team fee)."""
    if reg["covers"] == "pass":
        e = q("SELECT track_id FROM events WHERE id=?", (reg["event_id"],), one=True)
        if not e or not e["track_id"] or holds_pass(reg["user_id"], e["track_id"], exclude_reg=reg["id"]):
            return []
        return q(f"""SELECT r.* FROM registrations r JOIN events e ON e.id=r.event_id
                     WHERE r.user_id=? AND e.track_id=? AND r.id!=? AND r.status IN {HELD} AND COALESCE(r.covers,'')!='pass'""",
                 (reg["user_id"], e["track_id"], reg["id"]))
    if reg["covers"] == "team" and reg["team_name"]:
        return q(f"""SELECT * FROM registrations WHERE event_id=? AND team_name=? COLLATE NOCASE AND id!=?
                     AND status IN {HELD} AND COALESCE(covers,'')!='team'""", (reg["event_id"], reg["team_name"], reg["id"]))
    return []


def bundle_payments(payment):
    """All payment rows paid together with this one (same bundle), else just this one."""
    if payment["bundle"]:
        return q("SELECT * FROM payments WHERE bundle=? AND user_id=? ORDER BY id", (payment["bundle"], payment["user_id"]))
    return [payment]


# ------------------------------------------------------------------ writing
def sync(fest_id):
    """Keep the fest's own fee ('from ₹X'), capacity and dates in step with its sub-events (used on cards and filters)."""
    subs = q("SELECT fee, capacity, track_id, start_dt, end_dt FROM events WHERE parent_id=? AND is_removed=0 AND status!='draft'", (fest_id,))
    prices = [s["fee"] for s in subs if s["fee"]]
    prices += [t["pass_fee"] for t in tracks(fest_id) if t["pricing"] == "pass" and t["pass_fee"]]
    cap = sum(s["capacity"] for s in subs) or 100
    ex("UPDATE events SET fee=?, capacity=? WHERE id=?", (min(prices) if prices else 0, cap, fest_id))


def apply_template(fest):
    """Create the poster-style structure: Technical (one pass), Non-technical (per event), Sports (per team)."""
    for pos, (name, emoji, blurb, pricing, pass_fee, events) in enumerate(TEMPLATE):
        tid = ex("INSERT INTO fest_tracks (fest_id, name, emoji, blurb, pricing, pass_fee, position) VALUES (?,?,?,?,?,?,?)",
                 (fest["id"], name, emoji, blurb, pricing, pass_fee, pos))
        for i, (title, cat, fee, fee_type, team, label) in enumerate(events):
            add_sub(fest, tid, title=title, category=cat, fee=fee, fee_type=fee_type, team_size=team, label=label, position=i,
                    capacity=40 if cat == "Sports" else 100)
    sync(fest["id"])


def add_sub(fest, track_id, title, category="Technical", fee=0, fee_type="person", team_size=1, label=None, position=None,
            capacity=100, venue=None, start_dt=None, end_dt=None, status=None):
    if position is None:
        position = (scalar("SELECT MAX(position) FROM events WHERE parent_id=?", (fest["id"],)) or 0) + 1
    return ex("""INSERT INTO events (college_id, title, category, tagline, venue, venue_details, city, map_url, start_dt, end_dt,
                                     reg_deadline, capacity, fee, team_size, status, food_cost_per_head, meals_count,
                                     food_buffer_pct, kind, parent_id, track_id, fee_type, label, position)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'event',?,?,?,?,?)""",
              (fest["college_id"], title[:120], category if category in core.CATEGORIES else "Other",
               f"Part of {fest['title']}", venue or fest["venue"], fest["venue_details"], fest["city"], fest["map_url"],
               start_dt or fest["start_dt"], end_dt or fest["end_dt"], fest["reg_deadline"], max(1, int(capacity)),
               max(0, int(fee)), max(1, int(team_size)), status or fest["status"], fest["food_cost_per_head"],
               fest["meals_count"], fest["food_buffer_pct"], fest["id"], track_id,
               fee_type if fee_type in ("person", "team") else "person", (label or "").strip()[:40] or None, position))
