# EventFlow: the campus events community

**Built by Team Tech-Knights ♞**

Colleges post their events like Instagram posts. Students follow colleges, add friends, register, pay by UPI and walk in
with a QR ticket. Every college gets a full admin studio for each event, and you (the developer) control the whole platform.

---

## Put it online with Render (about 5 minutes, free)

Everything Render needs is in **`render.yaml`**. It creates the web app **and** its PostgreSQL database together and
connects them, so there are no keys, passwords or config files to fill in.

1. Sign in at **https://dashboard.render.com** with your GitHub account.
2. Click **New** → **Blueprint**, pick this repository, and click **Apply**.
3. Wait for the first deploy (about 3–5 minutes). Render shows a link like `https://eventflow-xxxx.onrender.com`. Open it.
   The first start creates all the tables and the clean demo accounts by itself.

That's it. Every `git push` redeploys automatically, and your data (accounts, events, payments, **and uploaded photos,
videos and PDFs**) lives in the Render database, so restarts and redeploys lose nothing.

**Before a live demo:** log in as the developer → **Platform** → **Reset demo data** gives a clean slate.
**Free plan notes:** the free web app **sleeps after 15 minutes without visitors** and takes about a minute to wake, so
open the link a couple of minutes before you present. The free database holds **1 GB** and **expires 30 days after it's
created** (Render then gives 14 days to upgrade). Upgrade the app and database to a paid instance in the Render dashboard
to keep it always on.

## Run it on your own computer (Windows)

1. Install **Python 3.10+** from python.org (tick **"Add python.exe to PATH"**).
2. Double-click **`setup.bat`** once, then **`run.bat`** every time.

No database setup is needed: on your computer EventFlow uses a built-in SQLite file (`instance/eventflow.db`).
The browser opens at **http://localhost:5000** with a short "EventFlow → By Team Tech Knights" intro (once per browser
session; tap **Skip** or add `?intro=1` to the URL to replay it). The first start creates the database and a **clean live-demo
community**: colleges, students, follows and friendships are there, but **no events, posts, stories or registrations**, so you
can announce an event and walk through registration live.

| File | What it does |
|---|---|
| `render.yaml` | **Render Blueprint**: the web app + PostgreSQL database, wired together |
| `setup.bat` | Creates `venv\` and installs the packages (one time) |
| `run.bat` | Starts EventFlow and opens the browser. Phones on the same Wi-Fi can open `http://<your-PC-IP>:5000` |
| `demo-online.bat` | Temporary public link from your laptop via a free Cloudflare Tunnel (if you're not using Render) |
| `reset-demo.bat` | Wipes the data on this computer and restores the clean demo |
| `run.sh` | Same as run.bat for macOS / Linux |

### Demo logins (one tap on the login page)

| Who | Login | Password | Shows |
|---|---|---|---|
| **Developer (you)** | `dev@eventflow.app` | `dev12345` | Developer console: verify colleges, users, payments, reports, platform switches |
| **College admin** | `events@arunai-demo.edu` (or `arunai.eng`) | `college123` | Studio for **Arunai Engineering College** |
| **Student** | `student@eventflow.app` (or `ananya`) | `student123` | Feed, friends, tickets, rewards, certifications |
| **Second student** | `rahul.v` | `student123` | Ananya's friend, for chat and team registration |

The developer account is **Vidhya Sagar** (`@vidhyasagar`).

Every demo student uses `student123`, and every demo college uses `college123`. All colleges and people in the demo are fictional.
Change the developer login with environment variables before going live (see Settings below).

---

## Live demo script (clean start)

1. Open the site: the **intro** plays, then the landing page. Log in as **Arunai Engineering College**.
2. **Studio → New event**: create *HackArunai 24H* (₹200, open for registration) → **Create post** with a poster, link the event.
   Or pick **Fest · many events**, keep the Technical · Non-technical · Sports layout ticked, upload your poster and create:
   24 events with a ₹75 Technical pass, ₹75 per Non-technical event and ₹600 / ₹150 per Sports team appear instantly.
3. On a phone (or a second browser), log in as **Ananya**: the event appears in her **story** circle and **feed** (she follows Arunai).
4. Open the event → tap **Register & pay** on the sticky bar → checkout with UPI QR → **Pay (demo)** → confetti and **+50 points**.
   For a fest: tick a few events across tracks (watch the total update), add team names, **Register** → one payment for all.
5. **Rewards** (menu → points): balance, level, history, leaderboard. On her next paid ticket she can tap **Apply** to spend points (1 point = ₹1, up to 50% of a ticket).
6. Back as the college: **Check-in scanner** → type her ticket code → she gets **+30 points** and a certificate.
7. **Studio → event → 🏆 Winners** → pick her (or her team) as **1st** → **Announce**. Her ticket shows a winner banner, the event page shows a **podium**, and a **certificate of achievement** lands in **Profile → Certifications**.
8. **Messages** between Ananya and Rahul update live. On phones, the **menu** (top-right) has settings, dark mode and **Log out**.

Want the old busy demo with 11 sample events, posts and hundreds of registrations? Start with `set EVENTFLOW_FULL_DEMO=1`
before `run.bat` (after `reset-demo.bat`).

---

## What's inside

**Accounts & settings.** Separate student and college sign-up, login by email / username / mobile, password strength meter,
password reset, profile photo and cover, privacy (who sees your events, who can message you), notification switches,
light / dark / system theme, account deletion.

**Community.** Instagram-style feed with stories, multi-photo/video carousels, file attachments, likes (double-tap),
comments, saves, share, report. College profiles with blue ticks, followers, post grid, events. Student profiles with
badges, friends and events. Friend requests, suggestions (mutual friends, same college), direct messages with live updates,
share events to friends, notifications with live badges, saved items, search with instant suggestions (`/` to focus).

**Events.** Event pages with parallax banner, schedule timeline, venue + maps link, FAQs, updates, "friends going",
who's going, add to calendar (.ics), save, share. Registration with food choice, team names and team-size limits, coupon
codes, waitlist with automatic promotion, UPI checkout (QR + UTR + screenshot), demo instant pay, receipts, QR tickets,
personal slots, cancellation with refund requests, certificates with public verification links, month calendar, and
**winners** (1st / 2nd / 3rd / special mention, per person or whole team) with a podium on the event page and certificates of achievement.

**Fests (many events under one name).** Create a fest like *TechTrove 3.0* and group its events into tracks such as
Technical, Non-technical and Sports. Each track is priced either **per event** or with **one pass** that covers every
event in it; each event can charge **per person** or **per team** (the captain pays, teammates join free). One click
adds the poster-style Technical · Non-technical · Sports layout (24 events) that you can edit. Students tick several
events, enter team names and pay **once**; each event still gets its own QR ticket, check-in desk, winners and
certificates. The college gets a fest control room (all tracks and events, combined payments approved per order,
updates to every participant, one check-in desk that accepts any of the fest's tickets). Cancelling the ticket that
carries a pass or a team fee also cancels the events that depended on it.

**Rewards.** Points for every confirmed registration (+50 paid, +20 free), check-in (+30) and wins (+250 / +150 / +100 / +50).
Spend them at checkout (1 point = ₹1 off, up to 50% of a ticket). Levels from Rookie to Legend, a history and a leaderboard.
Points are computed from the records themselves, so cancelling a ticket or a rejected payment automatically adjusts them.
Every certificate a student earns appears in the **Certifications** tab on their profile.

**College studio (event admin).** Dashboard with live KPIs and charts, event editor with banner upload, per-event control
room (registrations, payment verification, personal slots, schedule, food budget with learned turnout, coupons,
announcements, FAQ, stats), QR check-in scanner (camera or typed), CSV export, post analytics, AI assistant console, and a
**team hierarchy**: leads see only their events; volunteers can only scan tickets.

**Developer console.** Platform KPIs and growth chart, college verification queue, user search / suspend / password reset,
feature or take down events, every payment, moderation of reports, platform switches (sign-ups, demo payments,
verified-only publishing, maintenance mode, platform fee %, site-wide banner), broadcast notifications, audit log.

**AI assistant.** Floating on every page. Answers from live data (events, fees, seats, schedules, slots, tickets,
announcements, FAQs). Unanswered questions go to the organisers, who can answer once and teach it. Works offline; set
`ANTHROPIC_API_KEY` to use Claude (`EVENTFLOW_AI_MODEL`, default `claude-sonnet-5-5`).

---

## Payments

Payments go **straight to the college's UPI ID**. EventFlow never holds money:

1. The student scans the generated UPI QR (amount and note are pre-filled) and pays from any UPI app.
2. The student enters the 12-digit UTR, with an optional screenshot. The ticket shows *"payment being verified"* and the seat is held.
3. The college approves it in **Studio → event → Payments**, and the ticket activates instantly. If they reject it, the student is asked to resubmit.

Cancelling a paid ticket marks a refund as due; the college sends it and taps **Mark refund sent**. **Demo pay** confirms
instantly for presentations. Switch it off in **Developer → Platform** before real use. The platform fee is recorded per
payment for your revenue reports.

---

## Settings (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `DEV_EMAIL` / `DEV_PASSWORD` / `DEV_NAME` / `DEV_USERNAME` | dev@eventflow.app / dev12345 / Vidhya Sagar / vidhyasagar | The single developer account (created on first run) |
| `EVENTFLOW_DEMO` | `1` | `0` = start with an empty platform (only the developer account) |
| `EVENTFLOW_FULL_DEMO` | `0` | `1` = also create sample events, posts, registrations and payments |
| `ANTHROPIC_API_KEY` | (none) | Claude for the assistant and announcement drafting |
| `PORT` / `HOST` | 5000 / 0.0.0.0 in run.bat | Where to listen |
| `SECRET_KEY` | auto-generated (`instance/secret.key` locally, by Render online) | Session signing |
| `DATABASE_URL` | not set (SQLite) · set by Render | PostgreSQL connection; uploads are stored in it too |
| `TZ` | your computer's clock · `IST-5:30` (India) on Render | Time zone for dates, slots and "today"; the database follows it |

Set them in `run.bat` (e.g. `set DEV_PASSWORD=MyStrongPass1`) or in your shell before starting.

## Database

EventFlow picks its database automatically:

| Where | Database | Uploaded files |
|---|---|---|
| **Render** (`DATABASE_URL` is set by the Blueprint) | **PostgreSQL** | stored **in the database** (table `media_files`), because Render's disk is wiped on every restart |
| **Your computer** (no `DATABASE_URL`) | **SQLite** file `instance/eventflow.db` | `instance/uploads/` |

The app's queries are written once. `db.py` translates them for PostgreSQL (cached) and runs them through a small
connection pool that waits instead of failing when many people arrive at once, and reconnects by itself if the
database restarts. Tables are created on the first start, under a database lock so two servers starting together never
set up twice. Videos stored in the database are streamed in pieces with HTTP ranges, so seeking works on every phone.

- **Reset the online demo:** Developer console → **Platform** → **Reset demo data** (type RESET).
- **Connect from your laptop to the Render database** (optional): copy the **External Database URL** from the database's
  page in Render, then `set DATABASE_URL=<that URL>` before `run.bat`.
- `seed_media/` holds the demo pictures (served straight from the code); `tools/make_seed_media.py` regenerates them
  (needs Pillow, numpy, reportlab, ffmpeg; the app itself doesn't).

## Android app

See **[ANDROID.md](ANDROID.md)**: a ready-to-paste Android Studio WebView app (Kotlin) that points at your hosted
EventFlow and handles UPI app links, file uploads, the camera for QR check-in, Save as PDF and the back button.
The whole UI is built mobile-first, so every screen (including the fest picker and the intro) works inside the app.
Fests and events are also available as JSON at `/api/events/<id>` for native screens later.

## Hosting

- **Live demo from your laptop:** `demo-online.bat` (Cloudflare Tunnel).
- **Render (recommended):** see [Put it online with Render](#put-it-online-with-render-about-5-minutes-free). Any other Python host
  works too: run `gunicorn app:app` and set `DATABASE_URL` to a PostgreSQL database.
  Cloudflare Pages / Workers can't run this Python server directly, but Cloudflare Tunnel or Cloudflare DNS in front of a VM works.

## Project layout

```
app.py            app factory, sessions, CSRF, maintenance gate, media serving
render.yaml       Render Blueprint (web app + PostgreSQL database)
db.py             database layer: PostgreSQL (Render) or SQLite (local), schema (27 tables), query translation
tools/            reset_db.py, make_seed_media.py
core.py           permissions & hierarchy, notifications, uploads, settings, formatting, food & slot logic
ai_agent.py       assistant: Claude (optional) + offline intent / TF-IDF engine
seed.py           developer account + demo community
blueprints/       auth · social · events · studio · dev · api
templates/        Jinja pages (social/, events/, studio/, dev/, auth/, partials/)
static/           app.css, app.js, bundled fonts, Chart.js, QR scanner (works offline)
seed_media/       demo posters, banners, logos, teaser videos, PDFs
```
