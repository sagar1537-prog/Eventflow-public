"""EventFlow AI assistant.

Answers participant questions from LIVE platform data (events, schedules, venues,
announcements, FAQs and the asking student's own registrations / slots).

Two engines:
  * LLM mode   – if ANTHROPIC_API_KEY is set, Claude answers grounded in the live context.
  * Local mode – a built-in intent + TF-IDF retrieval engine. Works fully offline,
                 so the demo never breaks on bad Wi-Fi. Also the automatic fallback.

Questions the agent can't answer are logged so organisers can answer them once
and turn them into FAQs – the agent then knows the answer for everyone.
"""
import json
import math
import os
import re
import urllib.request
from collections import Counter
from datetime import datetime

from db import q, ex

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = os.environ.get("EVENTFLOW_AI_MODEL", "claude-sonnet-5-5")
UNANSWERED_TOKEN = "[[UNANSWERED]]"

STOP = set("""a an the is are was were be been to of in on at for and or what whats when where who whom how which
do does did i me can could would should you your please tell about with by from this that it its will
there their any some get got have has had am we us our they them he she his her if so then than as into
just also there's isnt dont know want need like let""".split())
KIND_WEIGHT = {"faq": 1.3, "announcement": 1.0, "event": 1.0, "schedule": 0.9}
GENERIC = {"event", "events", "2026", "night", "day", "fest", "college", "the"}


def mode():
    return "llm" if os.environ.get("ANTHROPIC_API_KEY") else "local"


# ------------------------------------------------------------------ text utils
def _stem(w):
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > len(suf) + 2 and w.endswith(suf) and not w.endswith("ss"):
            return w[: -len(suf)]
    return w


SYNONYMS = {"wi-fi": "wifi", "wi fi": "wifi", "internet": "wifi", "lunch": "food meal", "dinner": "food meal",
            "breakfast": "food meal", "snacks": "food meal", "eat": "food meal", "parking": "parking vehicle",
            "bike": "vehicle", "car": "vehicle", "laptop": "laptop", "charger": "laptop", "cost": "fee", "price": "fee",
            "pay": "fee", "money": "fee", "free": "fee", "members": "team", "teammates": "team"}


def _normalise(text):
    t = (text or "").lower()
    for k, v in SYNONYMS.items():
        t = re.sub(rf"\b{re.escape(k)}\b", f"{k} {v}", t)
    return t


def tokens(text):
    return [_stem(w) for w in re.findall(r"[a-z0-9]+", _normalise(text)) if w not in STOP and len(w) > 1]


def _dt(s):
    try:
        return datetime.fromisoformat(str(s).replace(" ", "T")[:16])
    except (TypeError, ValueError):
        return None


def fmt(s):
    d = _dt(s)
    return d.strftime("%a %d %b, %I:%M %p").replace(" 0", " ") if d else "TBA"


def fmt_t(s):
    d = _dt(s)
    return d.strftime("%I:%M %p").lstrip("0") if d else ""


# ------------------------------------------------------------------ knowledge base
def build_knowledge(user):
    events = [dict(e) for e in q("""SELECT e.*, u.name college, (SELECT COUNT(*) FROM registrations r
                     WHERE r.event_id=e.id AND r.status IN ('confirmed','payment_review')) AS reg_count
                     FROM events e JOIN users u ON u.id=e.college_id
                     WHERE e.status!='draft' AND e.is_removed=0 AND u.status='active' ORDER BY e.start_dt""")]
    by_id = {e["id"]: e for e in events}
    docs = []
    for e in events:
        e["seats_left"] = max(e["capacity"] - e["reg_count"], 0)
        fee = f"Entry fee **₹{e['fee']}**." if e["fee"] else "Free entry."
        answer = (f"**{e['title']}** by {e['college']} ({e['category']}) runs from **{fmt(e['start_dt'])}** to {fmt(e['end_dt'])} "
                  f"at **{e['venue']}**" + (f" — {e['venue_details']}" if e["venue_details"] else "") + ". "
                  f"{e['reg_count']} registered, {e['seats_left']} seats left. {fee}")
        if e["status"] == "open" and e["reg_deadline"]:
            answer += f" Registration closes {fmt(e['reg_deadline'])}."
        elif e["status"] == "completed":
            answer += " This event has already been completed."
        docs.append({"kind": "event", "event": e, "answer": answer,
                     "text": f"{e['title']} {e['college']} {e['category']} {e['tagline'] or ''} {e['description'] or ''} {e['venue']} {e['venue_details'] or ''} {e['city'] or ''}"})
    for s in q("SELECT * FROM schedule_items ORDER BY start_dt"):
        e = by_id.get(s["event_id"])
        if not e:
            continue
        end = f"–{fmt_t(s['end_dt'])}" if s["end_dt"] else ""
        docs.append({"kind": "schedule", "event": e,
                     "answer": f"**{s['title']}** ({e['title']}) — {fmt(s['start_dt'])}{end} at **{s['venue'] or e['venue']}**.",
                     "text": f"{s['title']} {s['description'] or ''} {s['venue'] or ''} schedule timing {e['title']}"})
    for a in q("""SELECT a.*, e.title event_title FROM announcements a LEFT JOIN events e ON e.id=a.event_id
                  WHERE a.event_id IS NOT NULL ORDER BY a.created_at DESC LIMIT 40"""):
        if a["event_id"] not in by_id:
            continue  # drafts and removed events stay private
        docs.append({"kind": "announcement", "event": by_id.get(a["event_id"]),
                     "answer": f"📢 **{a['title']}** — {a['body']}",
                     "text": f"{a['title']} {a['body']} {a['event_title'] or ''} announcement notice update"})
    for f in q("SELECT f.*, e.title event_title FROM faqs f LEFT JOIN events e ON e.id=f.event_id"):
        if f["event_id"] is not None and f["event_id"] not in by_id:
            continue
        docs.append({"kind": "faq", "event": by_id.get(f["event_id"]), "answer": f["answer"],
                     "text": f"{f['question']} {f['question']} {f['answer']} {f['event_title'] or ''}"})

    mine = []
    if user and user["role"] == "student":
        mine = [dict(r) for r in q("""SELECT r.*, e.title, e.venue, e.start_dt, e.end_dt, e.status event_status
                     FROM registrations r JOIN events e ON e.id=r.event_id
                     WHERE r.user_id=? AND r.status IN ('confirmed','payment_review') ORDER BY e.start_dt""", (user["id"],))]
    return {"events": events, "docs": docs, "mine": mine}


def context_text(kb, user):
    lines = [f"Current date/time: {datetime.now().strftime('%A %d %B %Y, %I:%M %p')}", "", "EVENTS:"]
    for e in kb["events"]:
        lines.append(f"- {e['title']} [{e['category']}, status {e['status']}] {fmt(e['start_dt'])} to {fmt(e['end_dt'])}; "
                     f"venue {e['venue']} ({e['venue_details'] or 'no extra details'}); capacity {e['capacity']}, "
                     f"{e['reg_count']} registered, {e['seats_left']} seats left; registration deadline {fmt(e['reg_deadline'])}; "
                     f"meals provided: {e['meals_count']}; fee ₹{e['fee']}; organised by {e['college']}. About: {e['description'] or e['tagline'] or ''}")
    lines.append("\nSCHEDULE / ANNOUNCEMENTS / FAQ:")
    for d in kb["docs"]:
        if d["kind"] != "event":
            lines.append(f"- [{d['kind']}] {d['answer']}")
    lines.append("\nHOW THE PLATFORM WORKS: EventFlow is a community where colleges post events. Students follow colleges, add "
                 "friends, open an event and press Register. Paid events are paid by UPI: scan the QR, then submit the 12-digit UTR; "
                 "the college verifies it and the ticket activates. Each ticket (My tickets) has a QR pass scanned at the entry desk. "
                 "Certificates unlock once checked in. Personal slots (time + room) appear on the ticket. Cancelling a paid ticket "
                 "requests a refund from the college. Full events offer a waitlist.")
    if user:
        lines.append(f"\nTHE PERSON ASKING: {user['name']} ({user['role']}, dept {user['department'] or '-'}).")
        if kb["mine"]:
            lines.append("Their registrations:")
            for r in kb["mine"]:
                slot = (f"personal slot {fmt(r['slot_start'])}–{fmt_t(r['slot_end'])} in {r['slot_venue']}"
                        if r["slot_start"] else "no personal slot assigned yet")
                lines.append(f"- {r['title']} at {r['venue']} from {fmt(r['start_dt'])}; pass {r['pass_code']}; {slot}; "
                             f"attendance {'marked' if r['attended'] else 'not marked yet'}; team {r['team_name'] or '-'}")
        elif user["role"] == "student":
            lines.append("They have not registered for any events yet.")
    else:
        lines.append("\nThe person asking is NOT logged in (for personal slots/passes they must log in).")
    return "\n".join(lines)


# ------------------------------------------------------------------ local engine
def _match_event(ql, events):
    best, best_score = None, 0
    qtok = set(re.findall(r"[a-z0-9]+", ql))
    for e in events:
        title_tok = {t for t in re.findall(r"[a-z0-9]+", e["title"].lower()) if t not in GENERIC and len(t) > 3}
        hits = title_tok & qtok
        score = len(hits) + (1 if e["category"].lower() in qtok else 0) * 0.3
        if score > best_score:
            best, best_score = e, score
    return best if best_score >= 1 else None


def _rank(question, docs):
    qt = tokens(question)
    if not qt:
        return []
    n = len(docs)
    doc_toks = [Counter(tokens(d["text"])) for d in docs]
    df = Counter()
    for c in doc_toks:
        df.update(set(c))
    idf = {t: math.log((n + 1) / (df[t] + 1)) + 1 for t in df}
    unknown_idf = math.log(n + 1) + 1  # a word no document mentions is the most informative of all
    qv = Counter(qt)
    qnorm = math.sqrt(sum((v * idf.get(t, 1)) ** 2 for t, v in qv.items())) or 1
    q_weight = sum(idf.get(t, unknown_idf) for t in qv) or 1
    scored = []
    for d, c in zip(docs, doc_toks):
        dot = sum(qv[t] * idf.get(t, 0) * c.get(t, 0) * idf.get(t, 0) for t in qv)
        if not dot:
            continue
        dnorm = math.sqrt(sum((v * idf[t]) ** 2 for t, v in c.items())) or 1
        # coverage: how much of what was asked does this document actually talk about?
        coverage = sum(idf.get(t, unknown_idf) for t in qv if t in c) / q_weight
        weight = KIND_WEIGHT.get(d["kind"], 1.0)
        scored.append((dot / (qnorm * dnorm) * (0.35 + 0.65 * coverage) * weight, d))
    scored.sort(key=lambda x: -x[0])
    return scored


def _personal(user, kb, ql):
    if not user:
        return "Please **log in** first — then I can tell you your personal slot, venue and pass. 🔐", True
    if user["role"] != "student":
        return "You're signed in as an organiser. Your events are in **Studio**.", True
    mine = kb["mine"]
    if not mine:
        return "You haven't registered for any events yet. Open an event on the home page and press **Register**.", True
    upcoming = [r for r in mine if r["event_status"] != "completed"]
    if upcoming and not re.search(r"certificate|attended|past", ql):
        mine = upcoming
    if re.search(r"slot|room|where|when|time", ql):
        mine = sorted(mine, key=lambda r: (r["slot_start"] is None, r["start_dt"]))
    lines = []
    for r in mine:
        line = f"• **{r['title']}** — {fmt(r['start_dt'])} at **{r['venue']}**"
        if r["slot_start"]:
            line += f"\n   ⏱ Your slot: **{fmt(r['slot_start'])}–{fmt_t(r['slot_end'])}** in **{r['slot_venue']}**"
        if "pass" in ql or "qr" in ql:
            line += f"\n   🎟 Pass code: **{r['pass_code']}**"
        if r["attended"]:
            line += "\n   ✅ Attendance marked — certificate available"
        lines.append(line)
    return "Here's what you're registered for:\n" + "\n".join(lines), True


def local_answer(question, user, kb):
    ql = question.lower().strip()
    ev = _match_event(ql, kb["events"])

    if re.fullmatch(r"(hi+|hello|hey+|good (morning|afternoon|evening)|yo|hola|vanakkam)[\s!.]*", ql):
        name = f" {user['name'].split()[0]}" if user else ""
        return (f"Hi{name}! 👋 I'm the EventFlow assistant. Ask me about event timings, venues, your personal slot, "
                "passes, certificates or registration."), True

    personal = re.search(r"\b(my|mine|am i|i'm|im|do i|should i)\b", ql)
    if personal and re.search(r"slot|venue|room|where|when|time|timing|schedule|pass|qr|tickets?|register|events?|go|lab|turn|booking", ql):
        if not ev or not user:
            return _personal(user, kb, ql)
        mine = [r for r in kb["mine"] if r["event_id"] == ev["id"]]
        if mine:
            r = mine[0]
            if r["slot_start"]:
                return (f"For **{ev['title']}**, your personal slot is **{fmt(r['slot_start'])}–{fmt_t(r['slot_end'])}** "
                        f"in **{r['slot_venue']}**. The event itself is at {ev['venue']}. Your pass code is **{r['pass_code']}**."), True
            return (f"You're registered for **{ev['title']}** — {fmt(ev['start_dt'])} at **{ev['venue']}**. "
                    f"No personal slot has been assigned yet; you'll see it on your dashboard once it's announced."), True
        return f"You're not registered for **{ev['title']}** yet. Open it from the home page and press **Register**.", True

    if "certificate" in ql:
        msg = ("Certificates unlock automatically once your attendance is marked (your QR pass is scanned at the event). "
               "Open **My tickets**, pick the event and tap **Certificate** to view or save it as PDF.")
        if user and kb["mine"]:
            done = [r["title"] for r in kb["mine"] if r["attended"]]
            msg += (f"\n\n✅ You can already download certificates for: **{', '.join(done)}**." if done
                    else "\n\nYou don't have any certificates unlocked yet.")
        return msg, True

    if (re.search(r"how many|count|number of", ql) and re.search(r"regist|participant|people|student|joined|attend", ql)
            and not re.search(r"team|member|per group", ql)):
        if ev:
            return f"**{ev['reg_count']}** participants are registered for **{ev['title']}** ({ev['seats_left']} of {ev['capacity']} seats left).", True
        total = sum(e["reg_count"] for e in kb["events"])
        rows = "\n".join(f"• {e['title']}: **{e['reg_count']}**" for e in kb["events"])
        return f"There are **{total}** registrations across all events:\n{rows}", True

    if re.search(r"seat|available|left|full|space|capacity", ql):
        targets = [ev] if ev else [e for e in kb["events"] if e["status"] == "open"]
        rows = "\n".join(f"• **{e['title']}**: {e['seats_left']} of {e['capacity']} seats left" for e in targets)
        return rows or "No events are open for registration right now.", True

    if ev:
        if re.search(r"deadline|last date|close|until when|till when", ql):
            return f"Registration for **{ev['title']}** closes **{fmt(ev['reg_deadline'])}**.", True
        if re.search(r"schedule|agenda|timeline|itinerary|programme|program|plan", ql):
            items = [d["answer"] for d in kb["docs"] if d["kind"] == "schedule" and d["event"] and d["event"]["id"] == ev["id"]]
            if items:
                return f"Schedule for **{ev['title']}**:\n" + "\n".join("• " + i for i in items), True
        if re.search(r"tell me about|what is|what's|details|about|describe|overview", ql) and not re.search(r"\bwhen\b|\bwhere\b|time", ql):
            desc = ev["description"] or ev["tagline"] or ""
            base = next(d["answer"] for d in kb["docs"] if d["kind"] == "event" and d["event"]["id"] == ev["id"])
            return (desc + "\n\n" + base).strip(), True
        if re.search(r"food|lunch|dinner|meal|breakfast|snack|eat", ql):
            meal_items = [d["answer"] for d in kb["docs"] if d["kind"] == "schedule" and d["event"]
                          and d["event"]["id"] == ev["id"] and re.search(r"lunch|dinner|breakfast|snack|food", d["answer"].lower())]
            n = ev["meals_count"]
            if meal_items and re.search(r"\bwhen\b|time|where", ql):
                return "Meal times for **" + ev["title"] + "**:\n" + "\n".join("• " + i for i in meal_items), True
            return (f"**{ev['title']}** includes **{n} meal{'s' if n != 1 else ''}** for participants." if n
                    else f"No meals are planned for **{ev['title']}**."), True
        if re.search(r"\bwhere\b|venue|location|place|room|hall|reach|direction", ql):
            extra = f" — {ev['venue_details']}" if ev["venue_details"] else ""
            return f"**{ev['title']}** is at **{ev['venue']}**{extra}.", True
        if re.search(r"\bwhen\b|time|date|start|begin|timing|end", ql):
            return f"**{ev['title']}** starts **{fmt(ev['start_dt'])}** and ends {fmt(ev['end_dt'])}, at {ev['venue']}.", True
        if re.search(r"register|join|sign ?up|enrol|participate", ql):
            if ev["status"] != "open":
                return f"Registration for **{ev['title']}** is currently **{ev['status']}**.", True
            return (f"To join **{ev['title']}**: log in, open the event from the home page and press **Register**. "
                    f"{ev['seats_left']} seats left; registration closes {fmt(ev['reg_deadline'])}."), True

    if re.search(r"\b(fee|fees|price|prices|cost|costs|how much|pay|paid|payment|upi|refunds?|free|cheap)\b", ql):
        if ev:
            if ev["fee"]:
                return (f"**{ev['title']}** costs **₹{ev['fee']}**. Pay by UPI at checkout: scan the QR, then enter the 12-digit UTR. "
                        f"The college verifies it and your ticket activates."), True
            return f"**{ev['title']}** is **free**. Just press Register.", True
        if "refund" in ql:
            return ("Cancel from **My tickets** before the event. For paid tickets, a refund request goes to the college, "
                    "who sends it back to your UPI. You'll get a notification when it's sent."), True
        free = [e for e in kb["events"] if not e["fee"] and e["status"] == "open"]
        paid = [e for e in kb["events"] if e["fee"] and e["status"] == "open"]
        out = ""
        if free:
            out += "Free: " + ", ".join(f"**{e['title']}**" for e in free[:6]) + ".\n"
        if paid:
            out += "Paid: " + ", ".join(f"**{e['title']}** (₹{e['fee']})" for e in paid[:6]) + "."
        return out or "No events are open right now.", True

    if re.search(r"how (do|can|to) (i )?(register|join|sign ?up|participate)|registration process", ql):
        return ("1. **Sign up** as a student\n2. Open any event from your feed or Explore\n3. Press **Register** "
                "(add a team name for team events) and pay by UPI if it's paid\n4. Your **QR ticket** appears in My tickets. Show it at the entry desk."), True

    ranked = _rank(question, kb["docs"])
    if ev:  # prefer documents about the event the user mentioned
        ranked = sorted(ranked, key=lambda x: -(x[0] + (0.15 if x[1]["event"] and x[1]["event"]["id"] == ev["id"] else 0)))
    if ranked and ranked[0][0] >= 0.18:
        best = ranked[0][1]["answer"]
        if len(ranked) > 1 and ranked[1][0] >= max(0.3, ranked[0][0] * 0.8) and ranked[1][1]["answer"] != best:
            best += "\n\n" + ranked[1][1]["answer"]
        return best, True
    if ev:
        return next(d["answer"] for d in kb["docs"] if d["kind"] == "event" and d["event"]["id"] == ev["id"]), True
    return ("I don't have that information yet — I've passed your question to the organisers, "
            "and the answer will be added here soon. 🙏"), False


# ------------------------------------------------------------------ LLM engine
def _call_claude(system, messages, max_tokens=500):
    body = json.dumps({"model": MODEL, "max_tokens": max_tokens, "system": system, "messages": messages}).encode()
    req = urllib.request.Request(API_URL, data=body, headers={
        "x-api-key": os.environ["ANTHROPIC_API_KEY"],
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=25) as resp:
        data = json.load(resp)
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()


def _clean_history(history):
    out = []
    for m in (history or [])[-8:]:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
            continue
        text = str(m.get("content", ""))[:1500].strip()
        if not text:
            continue
        if out and out[-1]["role"] == m["role"]:
            out[-1]["content"] += "\n" + text
        else:
            out.append({"role": m["role"], "content": text})
    while out and out[0]["role"] != "user":
        out.pop(0)
    if out and out[-1]["role"] == "user":
        out.pop()
    return out


def llm_answer(question, user, kb, history):
    system = (
        "You are the EventFlow Assistant, the friendly help desk of EventFlow, a community where colleges post events and students register. "
        "Answer using ONLY the facts in the CONTEXT. Be concise (under 120 words), warm and specific — give exact "
        "dates, times, venues and room numbers. Use **bold** for key facts and short bullet lines starting with '• ' "
        "when listing. Never invent dates, venues, prices or rules. Never reveal other participants' personal details. "
        f"If the answer is not in the context, say you don't have that information yet and that the organisers have "
        f"been notified, then end your reply with {UNANSWERED_TOKEN}.\n\nCONTEXT:\n" + context_text(kb, user))
    messages = _clean_history(history) + [{"role": "user", "content": question}]
    text = _call_claude(system, messages)
    answered = UNANSWERED_TOKEN not in text
    return text.replace(UNANSWERED_TOKEN, "").strip(), answered


# ------------------------------------------------------------------ public API
def answer(question, user=None, history=None, event_id=None):
    question = str(question or "").strip()[:500]
    if not question:
        return {"answer": "Ask me anything about the events! 🙂", "source": "local", "answered": True}
    kb = build_knowledge(user)
    matched = _match_event(question.lower(), kb["events"])
    try:
        ctx_id = int(event_id) if event_id else None
    except (TypeError, ValueError):
        ctx_id = None
    log_event = matched["id"] if matched else (ctx_id if any(e["id"] == ctx_id for e in kb["events"]) else None)
    if event_id and not matched:
        try:
            ctx_ev = next((e for e in kb["events"] if e["id"] == int(event_id)), None)
        except (TypeError, ValueError):
            ctx_ev = None
        if ctx_ev and not re.search(r"\bmy\b|\ball\b|events", question.lower()):
            question = f"{question} ({ctx_ev['title']})"
    source = "local"
    if mode() == "llm":
        try:
            text, answered = llm_answer(question, user, kb, history)
            source = "llm"
        except Exception:  # network/key problems -> graceful offline fallback
            text, answered = local_answer(question, user, kb)
    else:
        text, answered = local_answer(question, user, kb)
    ex("INSERT INTO chat_logs (user_id,event_id,question,answer,source,answered) VALUES (?,?,?,?,?,?)",
       (user["id"] if user else None, log_event, question, text, source, 1 if answered else 0))
    return {"answer": text, "source": source, "answered": answered}


def draft_announcement(title, notes, event=None, slot_info=None):
    """Turn a few rough notes into a polished announcement body."""
    title = (title or "").strip()
    notes = (notes or "").strip()
    if mode() == "llm":
        try:
            ctx = ""
            if event:
                ctx = f"Event: {event['title']} on {fmt(event['start_dt'])} at {event['venue']}."
            return _call_claude(
                "You write short, clear announcements for a college event platform. Write only the announcement body "
                "(60–110 words), friendly but precise, plain text, no headings, at most one emoji. Do not invent facts "
                "beyond the notes and event details provided.",
                [{"role": "user", "content": f"Title: {title}\nNotes: {notes}\n{ctx}"}], max_tokens=300)
        except Exception:
            pass
    parts = []
    parts.append(f"Update for {event['title']} participants:" if event else "Hello everyone!")
    body = notes or title
    body = body[0].upper() + body[1:] if body else ""
    if body and body[-1] not in ".!?":
        body += "."
    parts.append(body)
    if event:
        parts.append(f"📍 {event['venue']}, {fmt(event['start_dt'])}.")
    parts.append("Open your ticket in EventFlow for your QR pass and timings. Questions? Ask the EventFlow assistant.")
    return " ".join(p for p in parts if p)
