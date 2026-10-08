"""First-run setup: the single developer account, plus a realistic demo community (optional).

All colleges, people and events here are fictional. Demo media ships in seed_media/ and is
copied into instance/uploads/seed/ so posts, banners and logos work offline.
"""
import os
import random
import shutil
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash

import core
from db import q, ex, scalar

HERE = os.path.dirname(os.path.abspath(__file__))
MEDIA_SRC = os.path.join(HERE, "seed_media")

COLLEGES = [
    dict(key="cit", name="Arunai Engineering College", username="arunai.eng", email="events@arunai-demo.edu",
         phone="9840000001", city="Tiruvannamalai", website="https://arunai-demo.edu", upi="arunaievents@okaxis", verification="verified",
         bio="Engineering, research and the loudest hackathons in Tamil Nadu. 🛠️ Follow for symposiums, workshops and fests.",
         colors=("#2B4CFF", "#00C2FF")),
    dict(key="marina", name="Marina College of Arts & Science", username="marina.arts", email="culturals@marina-demo.edu",
         phone="9840000002", city="Chennai", website="https://marina-demo.edu", upi="marinaculturals@okhdfc", verification="verified",
         bio="Arts, music and the seaside stage. Rhythm Night is our love letter to the city. 🎶",
         colors=("#E0457B", "#FF9F43")),
    dict(key="bayview", name="Bayview Engineering College", username="bayview.ec", email="clubs@bayview-demo.edu",
         phone="9840000003", city="Bengaluru", website="https://bayview-demo.edu", upi="bayviewclubs@okicici", verification="verified",
         bio="Startups, esports and late-night builds. Bengaluru's campus that never logs off. 🚀",
         colors=("#7C3AED", "#22D3EE")),
    dict(key="hillcrest", name="Hillcrest University", username="hillcrest.uni", email="fests@hillcrest-demo.edu",
         phone="9840000004", city="Hyderabad", website="https://hillcrest-demo.edu", upi="hillcrestfest@oksbi", verification="verified",
         bio="A national symposium, a football fiesta and 12,000 students who show up. ⚽",
         colors=("#0F9D58", "#C6F432")),
    dict(key="kaveri", name="Kaveri School of Business", username="kaveri.biz", email="hello@kaveri-demo.edu",
         phone="9840000005", city="Coimbatore", website="https://kaveri-demo.edu", upi="kaveribiz@okaxis", verification="pending",
         bio="Business school with a bias for action. Case competitions every month. 📈",
         colors=("#E8890C", "#FFD166")),
]

STUDENTS = [
    ("Ananya Sharma", "ananya", "CSE", "III", "cit"), ("Rahul Verma", "rahul.v", "IT", "III", "cit"),
    ("Meera Nair", "meera.n", "ECE", "II", "cit"), ("Karthik Raja", "karthik.r", "MECH", "IV", "cit"),
    ("Divya Krishnan", "divya.k", "AI&DS", "II", "cit"), ("Arun Prakash", "arun.p", "CSE", "IV", "bayview"),
    ("Sneha Iyer", "sneha.i", "Visual Comm.", "II", "marina"), ("Vikram Singh", "vikram.s", "EEE", "III", "hillcrest"),
    ("Priyanka Das", "priyanka.d", "B.Com", "I", "kaveri"), ("Harish Kumar", "harish.k", "CSE", "II", "bayview"),
    ("Lakshmi Narayanan", "lakshmi.n", "Physics", "III", "marina"), ("Rohit Menon", "rohit.m", "IT", "IV", "hillcrest"),
    ("Kavya Reddy", "kavya.r", "CSE", "I", "hillcrest"), ("Sanjay Pillai", "sanjay.p", "MBA", "I", "kaveri"),
    ("Nithya Bala", "nithya.b", "English Lit.", "II", "marina"), ("Aditya Joshi", "aditya.j", "AI&DS", "III", "bayview"),
    ("Fathima Begum", "fathima.b", "Biotech", "II", "cit"), ("Gokul Raj", "gokul.r", "MECH", "III", "cit"),
    ("Shruti Patel", "shruti.p", "Design", "II", "bayview"), ("Manoj Selvam", "manoj.s", "Civil", "IV", "hillcrest"),
    ("Deepika Rao", "deepika.r", "ECE", "I", "cit"), ("Varun Chandran", "varun.c", "CSE", "II", "bayview"),
    ("Ishita Gupta", "ishita.g", "Economics", "III", "marina"), ("Naveen Murugan", "naveen.m", "IT", "I", "hillcrest"),
    ("Pooja Hegde", "pooja.h", "BBA", "II", "kaveri"), ("Siddharth Rao", "sid.rao", "CSE", "IV", "cit"),
    ("Zara Khan", "zara.k", "Mass Comm.", "I", "marina"), ("Aravind S", "aravind.s", "EEE", "II", "hillcrest"),
    ("Neha Kulkarni", "neha.k", "AI&DS", "I", "bayview"), ("Joel Thomas", "joel.t", "MECH", "III", "cit"),
    ("Priya Raman", "priya.r", "CSE", "IV", "cit"), ("Karthik Venkat", "karthik.v", "IT", "III", "cit"),
    ("Tara Menon", "tara.m", "Music", "II", "marina"), ("Yash Agarwal", "yash.a", "CSE", "III", "bayview"),
]

# day offsets are relative to "now" when the database is first created
EVENTS = [
    dict(key="codestorm", college="cit", title="CodeStorm 36H Hackathon", category="Technical",
         tagline="36 hours. Smart-campus problems. Real mentors.",
         description="Build a working prototype for one of six smart-campus problem statements. Industry mentors run two review rounds, "
                     "and every team pitches to the jury on Day 2 in a personal 10-minute slot.\n\nPrizes worth ₹1,50,000, internships "
                     "for the top three teams and free food all night. #hackathon #codestorm",
         venue="Main Auditorium", venue_details="Block A, ground floor. Build rounds in Labs 1–4, Block B.",
         city="Chennai", day=5, start=(9, 0), end_day=6, end=(17, 0), deadline=3, capacity=120, fee=299, team_size=4,
         status="open", featured=1, meals=4, food_cost=120, buffer=10, banner="ev_codestorm.jpg",
         schedule=[("Check-in & kit collection", 0, (8, 30), (9, 15), "Main Auditorium foyer"),
                   ("Opening & problem statements", 0, (9, 30), (10, 30), "Main Auditorium"),
                   ("Hacking begins", 0, (10, 30), (13, 0), "Labs 1–4, Block B"),
                   ("Lunch", 0, (13, 0), (14, 0), "Food Court"),
                   ("Mentor round 1", 0, (15, 0), (17, 0), "Labs 1–4, Block B"),
                   ("Dinner & midnight snacks", 0, (20, 0), (21, 0), "Food Court"),
                   ("Final pitches (your personal slot)", 1, (10, 0), (14, 0), "Labs 1, 2 & 4"),
                   ("Results & prizes", 1, (16, 0), (17, 0), "Main Auditorium")],
         faqs=[("What is the team size?", "Teams of up to 4. Every member registers with the same team name."),
               ("Do I need my own laptop?", "Yes, bring your laptop and charger. Wi-Fi and power strips are provided in the labs.")],
         coupons=[("EARLYBIRD", 20, 30), ("CITSTUDENT", 50, 40)]),
    dict(key="aiworkshop", college="cit", title="AI Builders Workshop", category="Workshop",
         tagline="Build your first AI assistant in a day.",
         description="Hands-on workshop on prompting, retrieval and shipping an AI assistant with Python. Laptops required.",
         venue="Lab 3", venue_details="Block B, 1st floor", city="Chennai", day=-12, start=(9, 30), end_day=-12, end=(16, 0),
         deadline=-14, capacity=60, fee=0, team_size=1, status="completed", meals=2, food_cost=100, buffer=10,
         banner="ev_aiworkshop.jpg"),
    dict(key="roborumble", college="cit", title="Robo Rumble", category="Technical",
         tagline="Build it. Drive it. Push the other bot out.",
         description="Teams of up to 3 build a remote-controlled bot under 5 kg and battle in a knockout arena. Kits are available to borrow.",
         venue="Mechanical Workshop", venue_details="Block D, behind the library", city="Chennai", day=16, start=(10, 0),
         end_day=16, end=(17, 0), deadline=13, capacity=40, fee=149, team_size=3, status="open", meals=1, food_cost=110,
         buffer=8, banner="ev_roborumble.jpg"),
    dict(key="rhythm", college="marina", title="Rhythm Night 2026", category="Cultural",
         tagline="Bands, a dance battle and a DJ finale by the sea.",
         description="Our biggest night of the year. Six college bands, an inter-college dance battle and a DJ finale. "
                     "Food stalls open from 6 PM. #rhythmnight",
         venue="Open Air Theatre", venue_details="Enter via Gate 3, behind the sports complex", city="Chennai", day=20,
         start=(17, 0), end_day=20, end=(22, 30), deadline=18, capacity=800, fee=199, team_size=1, status="open", featured=1,
         meals=1, food_cost=150, buffer=12, banner="ev_rhythm.jpg",
         schedule=[("Gates open", 0, (16, 30), (17, 0), "Gate 3"), ("Band showcase", 0, (17, 0), (18, 45), "Open Air Theatre"),
                   ("Dance battle", 0, (18, 45), (20, 15), "Open Air Theatre"), ("DJ finale", 0, (20, 30), (22, 30), "Open Air Theatre")],
         coupons=[("FRIENDS4", 15, 100)]),
    dict(key="photowalk", college="marina", title="Lens & Light Photo Walk", category="Workshop",
         tagline="A sunrise walk along the Marina with a pro photographer.",
         description="Small group, big light. Bring any camera, even a phone. We end with a breakfast critique session.",
         venue="Lighthouse Gate", venue_details="Meet at the lighthouse entrance at 6:15 AM", city="Chennai", day=3,
         start=(6, 30), end_day=3, end=(9, 30), deadline=2, capacity=25, fee=0, team_size=1, status="open", meals=1,
         food_cost=80, buffer=5, banner="ev_photowalk.jpg"),
    dict(key="poetry", college="marina", title="Ink & Verse Poetry Slam", category="Cultural",
         tagline="Three minutes. One mic. No paper.", description="An open poetry slam in English and Tamil.",
         venue="Seminar Hall", venue_details="Arts block, 2nd floor", city="Chennai", day=-20, start=(15, 0), end_day=-20,
         end=(18, 0), deadline=-22, capacity=80, fee=0, team_size=1, status="completed", meals=0, food_cost=0, buffer=0,
         banner="ev_poetry.jpg"),
    dict(key="pitch", college="bayview", title="Pitch Arena: Founders Edition", category="Business",
         tagline="Five minutes in front of real investors.",
         description="Student founders pitch to angel investors and incubator heads. The top three teams win incubation support "
                     "and ₹50,000 in cloud credits. #startups",
         venue="Innovation Hub", venue_details="Bayview Tech Park, 3rd floor", city="Bengaluru", day=9, start=(14, 0),
         end_day=9, end=(18, 0), deadline=7, capacity=50, fee=0, team_size=2, status="open", meals=1, food_cost=90, buffer=5,
         banner="ev_pitch.jpg"),
    dict(key="valorant", college="bayview", title="Valorant Campus Cup", category="Gaming",
         tagline="5v5. Single elimination. One trophy.",
         description="LAN tournament for college teams. Bring your own peripherals; PCs are provided. Finals streamed live.",
         venue="Esports Arena", venue_details="Student centre, basement", city="Bengaluru", day=12, start=(10, 0), end_day=13,
         end=(20, 0), deadline=10, capacity=160, fee=250, team_size=5, status="open", meals=3, food_cost=130, buffer=10,
         banner="ev_valorant.jpg"),
    dict(key="technova", college="hillcrest", title="TechNova National Symposium", category="Technical",
         tagline="Papers, a tech quiz and a keynote on AI in healthcare.",
         description="India's student symposium for research and engineering. Paper presentations, a technical quiz, project "
                     "expo and a keynote on AI in healthcare.",
         venue="Convention Centre", venue_details="Hillcrest main campus, Gate 1", city="Hyderabad", day=25, start=(9, 30),
         end_day=25, end=(17, 0), deadline=22, capacity=300, fee=350, team_size=2, status="open", meals=2, food_cost=140,
         buffer=8, banner="ev_technova.jpg",
         faqs=[("How long is a paper presentation?", "8 minutes plus 2 minutes of questions.")]),
    dict(key="football", college="hillcrest", title="Football Fiesta 7s", category="Sports",
         tagline="Inter-college 7-a-side under the lights.",
         description="Sixteen college teams, floodlit turf and a knockout bracket. Squads of up to 8.",
         venue="Hillcrest Turf", venue_details="Sports complex, Gate 4", city="Hyderabad", day=7, start=(16, 0), end_day=7,
         end=(22, 0), deadline=5, capacity=128, fee=500, team_size=8, status="open", meals=1, food_cost=120, buffer=10,
         banner="ev_football.jpg"),
    dict(key="casechallenge", college="kaveri", title="Market Mavericks Case Challenge", category="Business",
         tagline="Crack a live retail case in 3 hours.",
         description="Teams of two solve a real retail expansion case and present to a panel of brand managers.",
         venue="Case Room 1", venue_details="Kaveri campus, Admin block", city="Coimbatore", day=14, start=(10, 0),
         end_day=14, end=(15, 0), deadline=12, capacity=80, fee=100, team_size=2, status="open", meals=1, food_cost=90,
         buffer=5, banner="ev_casechallenge.jpg"),
]

POSTS = [
    dict(college="cit", event="codestorm", hours=30, media=["post_codestorm.jpg", "CodeStorm_Rulebook.pdf"],
         caption="CodeStorm is back. 36 hours, six smart-campus problem statements and ₹1.5L in prizes. 🔥\n\n"
                 "Register your team of up to 4. Early-bird code EARLYBIRD takes 20% off this week. #hackathon #codestorm"),
    dict(college="cit", event="codestorm", hours=6, media=["post_codestorm_stats.jpg", "post_codestorm_tracks.jpg"],
         caption="Swipe for the six tracks. Pick yours before Day 1 check-in. Mentors from three product companies are confirmed. 👀"),
    dict(college="cit", event="aiworkshop", hours=24 * 11, media=["post_ai_recap.jpg"],
         caption="60 builders, 60 working assistants. Thanks to everyone who came to the AI Builders Workshop. "
                 "Certificates are live on your EventFlow tickets. 🤖"),
    dict(college="cit", event="roborumble", hours=50, media=["teaser_roborumble.mp4", "post_roborumble.jpg"],
         caption="Robo Rumble arena is ready. Teams of 3, bots under 5 kg, one ring. Kits available to borrow. 🤖⚔️ #robotics"),
    dict(college="marina", event="rhythm", hours=20, media=["post_rhythm.jpg", "teaser_rhythm.mp4"],
         caption="RHYTHM NIGHT 2026 🎶 Six bands. One dance battle. A DJ finale by the sea. Grab your passes before they're gone. #rhythmnight"),
    dict(college="marina", event="photowalk", hours=40, media=["post_photowalk.jpg"],
         caption="Sunrise photo walk with a pro photographer. Only 25 spots and they're all taken; join the waitlist and we'll ping you. 📷"),
    dict(college="marina", event="poetry", hours=24 * 19, media=["post_poetry.jpg"],
         caption="Ink & Verse was magic. 31 poets, two languages, zero paper. See you next semester. ✍️"),
    dict(college="bayview", event="pitch", hours=12, media=["post_pitch.jpg"],
         caption="Got a startup idea? Five minutes, real investors, incubation for the top three. Free to enter. 🚀 #startups"),
    dict(college="bayview", event="valorant", hours=60, media=["post_valorant.jpg"],
         caption="Valorant Campus Cup brackets open. 5v5, LAN, finals streamed. Bring your squad. 🎮"),
    dict(college="hillcrest", event="technova", hours=72, media=["post_technova.jpg", "TechNova_Brochure.pdf"],
         caption="TechNova National Symposium: call for papers is open. Brochure attached with tracks and rules. 📄"),
    dict(college="hillcrest", event="football", hours=16, media=["post_football.jpg"],
         caption="16 teams. Floodlit turf. Football Fiesta 7s is here. Squads of up to 8. ⚽"),
    dict(college="kaveri", event="casechallenge", hours=26, media=["post_casechallenge.jpg"],
         caption="Think you can crack a live retail case in 3 hours? Teams of two. 📈"),
    dict(college="hillcrest", event=None, hours=90, media=["post_campus_hillcrest.jpg"],
         caption="New semester, new turf lights. Thank you to everyone who voted for the upgrade. 💚"),
]


def _copy_media(app):
    dst = os.path.join(app.instance_path, "uploads", "seed")
    os.makedirs(dst, exist_ok=True)
    if os.path.isdir(MEDIA_SRC):
        for f in os.listdir(MEDIA_SRC):
            target = os.path.join(dst, f)
            if not os.path.exists(target):
                shutil.copy2(os.path.join(MEDIA_SRC, f), target)


def _m(name):
    path = os.path.join(MEDIA_SRC, name)
    return f"seed/{name}" if os.path.exists(path) else None


def _kind(name):
    return core.media_kind(name)


def seed(app):
    cfg = app.config
    if not scalar("SELECT 1 FROM users WHERE role='dev'"):
        ex("""INSERT INTO users (role, username, name, email, password_hash, bio, show_events, allow_messages, theme)
              VALUES ('dev',?,?,?,?,?,?,?,?)""",
           (cfg["DEV_USERNAME"], cfg["DEV_NAME"], cfg["DEV_EMAIL"].lower(), generate_password_hash(cfg["DEV_PASSWORD"]),
            "Developer of EventFlow.", "nobody", "everyone", "system"))
        core.audit("platform.created", "Developer account created")
    if not cfg.get("SEED_DEMO") or scalar("SELECT COUNT(*) FROM users WHERE role!='dev'"):
        return
    rnd = random.Random(7)
    hashes = {}

    def hp(pw):  # hash each demo password once; hashing is deliberately slow
        if pw not in hashes:
            hashes[pw] = generate_password_hash(pw)
        return hashes[pw]
    now = datetime.now()

    def at(days, hm, base=now):
        d = (base + timedelta(days=days)).replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
        return core.iso(d)

    def ts(dt):
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    # ---------------------------------------------------------------- colleges
    cid = {}
    for c in COLLEGES:
        cid[c["key"]] = ex("""INSERT INTO users (role, username, name, email, phone, password_hash, bio, avatar, cover, city, website,
                                                  upi_id, verification, show_events, allow_messages, created_at)
                              VALUES ('college',?,?,?,?,?,?,?,?,?,?,?,?,'nobody','everyone',?)""",
                           (c["username"], c["name"], c["email"], c["phone"], hp("college123"), c["bio"],
                            _m(f"logo_{c['key']}.png"), _m(f"cover_{c['key']}.jpg"), c["city"], c["website"], c["upi"],
                            c["verification"], ts(now - timedelta(days=rnd.randint(40, 120)))))
    college_name = {c["key"]: c["name"] for c in COLLEGES}

    # ---------------------------------------------------------------- students
    sid = {}
    for i, (name, username, dept, year, ckey) in enumerate(STUDENTS):
        email = "student@eventflow.app" if username == "ananya" else f"{username.replace('.', '')}@mail-demo.in"
        bio = {"ananya": "CSE '27 · building things that help people find their people. Hackathons > sleep. 💻",
               "priya.r": "Lead, CodeStorm 2026. Ask me anything about the hackathon!",
               "karthik.v": "Volunteer at CIT events. Usually holding a scanner. 📷"}.get(username, rnd.choice(
            ["Coffee, code and cricket ☕", "Always at the front row 🎤", "Designer by day, gamer by night 🎮",
             "Trying every event once 🎟️", "Debate · Quiz · Football", "Here for the hackathons", "", "Photography and long walks 📷"]))
        sid[username] = ex("""INSERT INTO users (role, username, name, email, phone, password_hash, bio, college_name, department, year, city,
                                                 show_events, created_at)
                              VALUES ('student',?,?,?,?,?,?,?,?,?,?,?,?)""",
                           (username, name, email, f"98{40000100 + i}", hp("student123"), bio,
                            college_name[ckey], dept, year, next(c["city"] for c in COLLEGES if c["key"] == ckey),
                            "everyone" if i % 3 else "friends", ts(now - timedelta(days=rnd.randint(5, 90)))))
    ananya = sid["ananya"]
    everyone = list(sid.values())

    # ---------------------------------------------------------------- follows & friendships
    for uname, u in sid.items():
        for c in rnd.sample(list(cid.values()), rnd.randint(1, 4)):
            ex("INSERT OR IGNORE INTO follows (follower_id, college_id, created_at) VALUES (?,?,?)",
               (u, c, ts(now - timedelta(days=rnd.uniform(0, 30)))))
    for ck in ("cit", "marina", "bayview"):
        ex("INSERT OR IGNORE INTO follows (follower_id, college_id) VALUES (?,?)", (ananya, cid[ck]))
    for k in ("hillcrest", "kaveri"):
        ex("DELETE FROM follows WHERE follower_id=? AND college_id=?", (ananya, cid[k]))

    def befriend(a, b, status="accepted", days=10):
        ex("INSERT OR IGNORE INTO friendships (requester_id, addressee_id, status, created_at) VALUES (?,?,?,?)",
           (sid[a], sid[b], status, ts(now - timedelta(days=days))))
    for f in ["rahul.v", "meera.n", "divya.k", "priya.r", "sid.rao", "sneha.i", "arun.p", "fathima.b", "joel.t"]:
        befriend("ananya", f, days=rnd.randint(2, 40))
    befriend("varun.c", "ananya", "pending", 1)
    befriend("tara.m", "ananya", "pending", 0.3)
    befriend("ananya", "kavya.r", "pending", 2)
    pool = [u for u in sid if u != "ananya"]
    for _ in range(45):
        a, b = rnd.sample(pool, 2)
        if not scalar("SELECT 1 FROM friendships WHERE (requester_id=? AND addressee_id=?) OR (requester_id=? AND addressee_id=?)",
                      (sid[a], sid[b], sid[b], sid[a])):
            befriend(a, b, days=rnd.randint(1, 60))

    if not cfg.get("SEED_FULL"):
        _clean_extras(sid, now, ts)
        return

    # ---------------------------------------------------------------- events
    eid = {}
    for e in EVENTS:
        eid[e["key"]] = ex("""INSERT INTO events (college_id, title, category, tagline, description, venue, venue_details, city, map_url,
                                start_dt, end_dt, reg_deadline, capacity, fee, team_size, status, banner, food_cost_per_head,
                                meals_count, food_buffer_pct, is_featured, views, created_at)
                              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                           (cid[e["college"]], e["title"], e["category"], e["tagline"], e["description"], e["venue"],
                            e["venue_details"], e["city"], "https://maps.google.com/?q=" + e["venue"].replace(" ", "+") + "+" + e["city"],
                            at(e["day"], e["start"]), at(e["end_day"], e["end"]), at(e["deadline"], (23, 59)), e["capacity"],
                            e["fee"], e["team_size"], e["status"], _m(e["banner"]), e["food_cost"], e["meals"], e["buffer"],
                            e.get("featured", 0), rnd.randint(150, 2400), ts(now - timedelta(days=max(8, 30 - e["day"])))))
        for title, d, s, en, venue in e.get("schedule", []):
            ex("INSERT INTO schedule_items (event_id, title, start_dt, end_dt, venue) VALUES (?,?,?,?,?)",
               (eid[e["key"]], title, at(e["day"] + d, s), at(e["day"] + d, en), venue))
        for qn, an in e.get("faqs", []):
            ex("INSERT INTO faqs (event_id, question, answer) VALUES (?,?,?)", (eid[e["key"]], qn, an))
        for code, pct, uses in e.get("coupons", []):
            ex("INSERT INTO coupons (event_id, code, percent_off, max_uses) VALUES (?,?,?,?)", (eid[e["key"]], code, pct, uses))
    ev = {e["key"]: e for e in EVENTS}

    # event team (hierarchy)
    ex("INSERT INTO event_staff (event_id, user_id, role) VALUES (?,?,'lead')", (eid["codestorm"], sid["priya.r"]))
    ex("INSERT INTO event_staff (event_id, user_id, role) VALUES (?,?,'lead')", (eid["roborumble"], sid["priya.r"]))
    ex("INSERT INTO event_staff (event_id, user_id, role) VALUES (?,?,'volunteer')", (eid["codestorm"], sid["karthik.v"]))

    # ---------------------------------------------------------------- registrations & payments
    teams = ["Null Pointers", "Byte Busters", "Stack Smashers", "Code Crusaders", "Pixel Pirates", "Debug Divas", "Tab Tamers"]

    def register(ekey, uname, status="confirmed", attended=False, team=None, method="upi", pay_status=None, days_before=None):
        e = ev[ekey]
        if scalar("SELECT 1 FROM registrations WHERE event_id=? AND user_id=?", (eid[ekey], sid[uname])):
            return None
        start = core.parse_dt(at(e["day"], e["start"]))
        created = start - timedelta(days=days_before if days_before is not None else rnd.uniform(1, 14))
        if created > now:
            created = now - timedelta(hours=rnd.uniform(1, 72))
        code = core.gen_code("EVF-", "registrations", "pass_code")
        food = rnd.choices(["veg", "nonveg", "none"], [6, 3, 1])[0]
        done = attended and start < now     # past events people attended are complete (certificate unlocked)
        rid = ex("""INSERT INTO registrations (event_id, user_id, pass_code, team_name, food_pref, status, amount, attended,
                                               checkin_time, completed_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                 (eid[ekey], sid[uname], code, team, food, status, e["fee"], 1 if attended else 0,
                  core.iso(start + timedelta(minutes=rnd.randint(-15, 40))) if attended else None,
                  core.iso(start + timedelta(hours=3)) if done else None, ts(created)))
        if e["fee"] or status == "confirmed":
            pstat = pay_status or ("paid" if status == "confirmed" else "submitted")
            m = "free" if not e["fee"] else method
            ex("""INSERT INTO payments (registration_id, user_id, event_id, receipt_no, base_amount, discount, amount, platform_fee,
                                        method, status, utr, screenshot, reviewed_by, reviewed_at, created_at)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               (rid, sid[uname], eid[ekey], core.gen_code("RCPT-", "payments", "receipt_no", 8), e["fee"], 0, e["fee"],
                int(round(e["fee"] * 0.05)), m, pstat,
                (str(rnd.randint(10 ** 11, 10 ** 12 - 1)) if m == "upi" else ("DEMO" + str(rnd.randint(1000, 9999)) if m == "demo" else None)),
                _m("upi_screenshot_demo.jpg") if m == "upi" else None,
                cid[e["college"]] if pstat == "paid" and m == "upi" else None,
                ts(created + timedelta(hours=3)) if pstat == "paid" and m == "upi" else None, ts(created)))
        return rid

    # CodeStorm: 60 people in teams of 3–4; Ananya's team has a slot
    cs_people = ["ananya", "rahul.v", "meera.n", "divya.k"] + rnd.sample(
        [u for u in sid if u not in ("ananya", "rahul.v", "meera.n", "divya.k", "priya.r", "karthik.v",
                                     "zara.k", "yash.a", "neha.k")], 22)
    for i, u in enumerate(cs_people):
        team = "Null Pointers" if i < 4 else teams[(i // 4) % len(teams)] + ("" if i < 28 else " 2")
        register("codestorm", u, team=team, method="demo" if i % 5 == 0 else "upi")
    for u in ["zara.k", "yash.a", "neha.k"]:
        register("codestorm", u, status="payment_review", team="Late Commits", pay_status="submitted", days_before=0.2)
    # CodeStorm Day 2 pitches: 10-minute slots in three labs, one team (up to 4) per slot
    import scheduler
    for lab in ["Lab 1 (Block B)", "Lab 2 (Block B)", "Lab 4 (Block B)"]:
        scheduler.generate([eid["codestorm"]], core.parse_dt(at(6, (10, 0))), 10, 5, 4, 4, lab)
    scheduler.place_new(eid["codestorm"], notify=False)

    # AI workshop (completed): attendance known → certificates + learned show rate
    for u in rnd.sample(pool, 22) + ["ananya"]:
        register("aiworkshop", u, attended=(u == "ananya" or rnd.random() < 0.82), days_before=rnd.uniform(3, 10))
    for u in rnd.sample(pool, 14):
        register("roborumble", u, team=rnd.choice(["Torque Titans", "Bot Brigade", "Circuit Breakers", "Gear Heads"]))
    for u in rnd.sample(pool, 26) + ["ananya"]:
        register("rhythm", u, method="demo" if rnd.random() < 0.3 else "upi")
    # Photo walk is full (25/25) so the waitlist can be demoed
    pw = rnd.sample([u for u in pool if u not in ("ananya",)], 25)
    for u in pw:
        register("photowalk", u)
    for u in rnd.sample(pool, 2):
        if u not in pw:
            r = ex("""INSERT INTO registrations (event_id, user_id, pass_code, status) VALUES (?,?,?,'waitlisted')""",
                   (eid["photowalk"], sid[u], core.gen_code("EVF-", "registrations", "pass_code")))
    for u in rnd.sample(pool, 18) + ["ananya"]:
        register("poetry", u, attended=(u == "ananya" or rnd.random() < 0.7), days_before=rnd.uniform(3, 10))
    for u in rnd.sample(pool, 12):
        register("pitch", u, team=rnd.choice(["FarmLink", "Pocket Tutor", "Kirana OS", "ReWear", "MediQ"]))
    for u in rnd.sample(pool, 20):
        register("valorant", u, team=rnd.choice(["Radiant Rats", "Spike Rushers", "Clutch Kings", "Eco Round"]))
    for u in rnd.sample(pool, 16):
        register("technova", u, team=None)
    register("technova", "ananya", status="payment_review", pay_status="submitted", days_before=0.1)
    for u in rnd.sample(pool, 24):
        register("football", u, team=rnd.choice(["Hillcrest Hawks", "Bayview Blitz", "Marina Mariners", "CIT Strikers"]))
    for u in rnd.sample(pool, 9):
        register("casechallenge", u, team=rnd.choice(["Margin Makers", "Shelf Life", "Unit Economics"]))
    # a rejected payment, so the "resubmit" flow can be shown
    rej = q("""SELECT p.id, p.registration_id FROM payments p WHERE p.event_id=? AND p.status='paid' AND p.method='upi' LIMIT 1""",
            (eid["valorant"],), one=True)
    if rej:
        ex("UPDATE payments SET status='rejected', note='UTR not found in our account. Please check and resubmit.' WHERE id=?", (rej["id"],))
        ex("UPDATE registrations SET status='pending_payment' WHERE id=?", (rej["registration_id"],))

    # ---------------------------------------------------------------- posts, likes, comments, saves
    pid_by_event = {}
    for p in POSTS:
        created = now - timedelta(hours=p["hours"])
        pid = ex("INSERT INTO posts (author_id, event_id, caption, created_at) VALUES (?,?,?,?)",
                 (cid[p["college"]], eid.get(p["event"]) if p["event"] else None, p["caption"], ts(created)))
        pid_by_event.setdefault(p["event"], pid)
        for i, name in enumerate(p["media"]):
            path = _m(name)
            if path:
                size = os.path.getsize(os.path.join(MEDIA_SRC, name))
                ex("INSERT INTO post_media (post_id, path, kind, original_name, size, position) VALUES (?,?,?,?,?,?)",
                   (pid, path, _kind(name), name, size, i))
        for u in rnd.sample(everyone, rnd.randint(6, len(everyone) - 4)):
            ex("INSERT OR IGNORE INTO post_likes (post_id, user_id, created_at) VALUES (?,?,?)",
               (pid, u, ts(created + timedelta(minutes=rnd.randint(5, 600)))))
        for u in rnd.sample(everyone, rnd.randint(0, 4)):
            ex("INSERT OR IGNORE INTO post_saves (post_id, user_id) VALUES (?,?)", (pid, u))
    comments = [
        (pid_by_event["codestorm"], "rahul.v", "Null Pointers are coming for that trophy 🏆"),
        (pid_by_event["codestorm"], "meera.n", "Is the rulebook final? Asking for a friend who wants to use Flutter 😅"),
        (pid_by_event["codestorm"], "arun.p", "Bayview squad registered! See you in Chennai."),
        (pid_by_event["rhythm"], "sneha.i", "Last year's DJ set was unreal. Can't wait!"),
        (pid_by_event["rhythm"], "tara.m", "Our band is on the lineup!! 🎸"),
        (pid_by_event["pitch"], "aditya.j", "Is it okay if our startup is still pre-revenue?"),
        (pid_by_event["technova"], "vikram.s", "Downloaded the brochure. The healthcare track looks great."),
        (pid_by_event["aiworkshop"], "ananya", "Best workshop this semester. Got my certificate on EventFlow!"),
        (pid_by_event["valorant"], "yash.a", "FOLLOW MY PAGE FOR FREE FOLLOWERS!!! link in bio"),
    ]
    spam_id = None
    for pid, u, text in comments:
        c = ex("INSERT INTO comments (post_id, user_id, body, created_at) VALUES (?,?,?,?)",
               (pid, sid[u], text, ts(now - timedelta(hours=rnd.uniform(1, 20)))))
        if "FREE FOLLOWERS" in text:
            spam_id = c
    if spam_id:
        ex("INSERT INTO reports (reporter_id, target_type, target_id, reason) VALUES (?,?,?,?)",
           (sid["rahul.v"], "comment", spam_id, "Spam: advertising followers"))
    for k in ("rhythm", "pitch", "football"):
        ex("INSERT OR IGNORE INTO event_saves (event_id, user_id) VALUES (?,?)", (eid[k], ananya))

    # ---------------------------------------------------------------- announcements
    def announce(ekey, title, body, important=False, kind="general", hours=5):
        ex("INSERT INTO announcements (event_id, title, body, priority, kind, created_by, created_at) VALUES (?,?,?,?,?,?,?)",
           (eid[ekey], title, body, "important" if important else "normal", kind, cid[ev[ekey]["college"]],
            ts(now - timedelta(hours=hours))))
    announce("codestorm", "Problem statements are live",
             "All six smart-campus problem statements are published. Lock your track at Day 1 check-in.", True, hours=28)
    announce("codestorm", "Your pitch slots are out",
             "Every participant has a personal 10-minute pitch slot on Day 2. Open your ticket to see your time and lab.",
             True, "slot", hours=4)
    announce("rhythm", "Lineup announced", "Six bands confirmed, including last year's winners. DJ finale starts at 8:30 PM.", hours=18)
    announce("technova", "Paper submissions open", "Upload your paper abstract by email before the deadline. Format details are in the brochure.", hours=60)

    # ---------------------------------------------------------------- messages
    def msg(a, b, text, mins_ago, event=None, read=True):
        t = now - timedelta(minutes=mins_ago)
        ex("INSERT INTO messages (sender_id, recipient_id, body, event_id, read_at, created_at) VALUES (?,?,?,?,?,?)",
           (sid[a], sid[b], text, eid.get(event) if event else None, ts(t) if read else None, ts(t)))
    msg("rahul.v", "ananya", "Did you see the CodeStorm tracks? Smart parking looks doable in 36 hours", 300)
    msg("ananya", "rahul.v", "Yes! I'm thinking a camera + ESP32 prototype. Meera can do the app", 290)
    msg("rahul.v", "ananya", "Done. Our pitch slot is at 10 AM on Day 2, Lab 1", 280)
    msg("meera.n", "ananya", "Check this out: Rhythm Night 2026", 120, "rhythm")
    msg("meera.n", "ananya", "We're going right? 🎶", 119, read=False)
    msg("sneha.i", "ananya", "Saving you a spot at the photo walk waitlist 😂", 60 * 26)

    # ---------------------------------------------------------------- notifications for the demo student
    def note(uid, kind, text, link, actor=None, hours=1, read=False):
        ex("INSERT INTO notifications (user_id, actor_id, kind, text, link, is_read, created_at) VALUES (?,?,?,?,?,?,?)",
           (uid, actor, kind, text, link, 1 if read else 0, ts(now - timedelta(hours=hours))))
    cs_code = scalar("SELECT pass_code FROM registrations WHERE event_id=? AND user_id=?", (eid["codestorm"], ananya))
    note(ananya, "friend_request", "Tara Menon sent you a friend request.", "/friends?tab=requests", sid["tara.m"], 0.3)
    note(ananya, "slot", "Your pitch slot for CodeStorm 36H Hackathon is set. Open your ticket.", f"/ticket/{cs_code}", cid["cit"], 4)
    note(ananya, "post", "Marina College of Arts & Science shared a new post.", "/", cid["marina"], 20)
    note(ananya, "friend_request", "Varun Chandran sent you a friend request.", "/friends?tab=requests", sid["varun.c"], 24)
    note(ananya, "event", "You're in! Your ticket for Rhythm Night 2026 is ready.", "/tickets", cid["marina"], 30, True)
    note(ananya, "event", "Checked in at AI Builders Workshop. Your certificate is unlocked!", "/tickets?tab=past", cid["cit"], 24 * 12, True)

    # ---------------------------------------------------------------- AI help-desk history
    for qn, ek in [("Is there parking for two-wheelers at Hillcrest?", "technova"), ("Will CodeStorm have a women-only track?", "codestorm")]:
        ex("INSERT INTO chat_logs (user_id, event_id, question, answer, source, answered) VALUES (?,?,?,?,?,0)",
           (sid["kavya.r"], eid[ek], qn, "I don't have that information yet. I've passed it to the organisers.", "local"))

    ex("INSERT INTO audit_log (actor_id, action, detail, created_at) VALUES (?,?,?,?)",
       (scalar("SELECT id FROM users WHERE role='dev'"), "college.verify", COLLEGES[0]["name"], ts(now - timedelta(days=30))))



def _clean_extras(sid, now, ts):
    """Clean live-demo start: no events, posts, stories or registrations. Just people, friends and a little chat."""
    def msg(a, b, text, mins_ago, read=True):
        t = now - timedelta(minutes=mins_ago)
        ex("INSERT INTO messages (sender_id, recipient_id, body, read_at, created_at) VALUES (?,?,?,?,?)",
           (sid[a], sid[b], text, ts(t) if read else None, ts(t)))
    msg("rahul.v", "ananya", "Hey! Heard Arunai is announcing a hackathon soon 👀", 300)
    msg("ananya", "rahul.v", "Yes! Let's team up the moment registrations open", 290)
    msg("meera.n", "ananya", "Count me in too 🙌", 120, read=False)

    def note(uid, kind, text, link, actor=None, hours=1, read=False):
        ex("INSERT INTO notifications (user_id, actor_id, kind, text, link, is_read, created_at) VALUES (?,?,?,?,?,?,?)",
           (uid, actor, kind, text, link, 1 if read else 0, ts(now - timedelta(hours=hours))))
    note(sid["ananya"], "friend_request", "Tara Menon sent you a friend request.", "/friends?tab=requests", sid["tara.m"], 0.3)
    note(sid["ananya"], "friend_request", "Varun Chandran sent you a friend request.", "/friends?tab=requests", sid["varun.c"], 24)
    ex("INSERT INTO audit_log (actor_id, action, detail, created_at) VALUES (?,?,?,?)",
       (scalar("SELECT id FROM users WHERE role='dev'"), "college.verify", COLLEGES[0]["name"], ts(now - timedelta(days=30))))
