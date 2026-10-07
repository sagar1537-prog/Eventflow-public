"""Generates the demo media in seed_media/ (posters, banners, logos, covers, teaser videos, brochures).

Only needed if you want to regenerate the demo pictures. Requires: pip install pillow numpy reportlab
and ffmpeg on PATH for the teaser videos. The app itself does NOT need any of this.
"""
import math
import os
import random
import subprocess
import sys
from datetime import datetime, timedelta

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "seed_media")
FONTS = os.path.join(ROOT, "tools", "fonts")
os.makedirs(OUT, exist_ok=True)

from seed import COLLEGES, EVENTS  # noqa: E402


RUPEE_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def font(name, size, text=""):
    if "₹" in text and os.path.exists(RUPEE_FONT):
        return ImageFont.truetype(RUPEE_FONT, int(size * 0.92))
    files = {"display": "bricolage-grotesque-latin-800-normal.woff", "display7": "bricolage-grotesque-latin-700-normal.woff",
             "display5": "bricolage-grotesque-latin-500-normal.woff", "body": "public-sans-latin-400-normal.woff",
             "body6": "public-sans-latin-600-normal.woff", "body7": "public-sans-latin-700-normal.woff"}
    return ImageFont.truetype(os.path.join(FONTS, files[name]), size)


def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def gradient(w, h, c1, c2, angle=35):
    a = math.radians(angle)
    x = np.linspace(0, 1, w)[None, :]
    y = np.linspace(0, 1, h)[:, None]
    t = (x * math.cos(a) + y * math.sin(a))
    t = (t - t.min()) / (t.max() - t.min())
    c1, c2 = np.array(hex2rgb(c1)), np.array(hex2rgb(c2))
    arr = (c1[None, None, :] * (1 - t[..., None]) + c2[None, None, :] * t[..., None]).astype(np.uint8)
    return Image.fromarray(arr, "RGB")


def grain(img, amount=10):
    amount = amount * 0.6
    noise = np.random.default_rng(3).normal(0, amount, (img.height, img.width, 1))
    arr = np.clip(np.asarray(img).astype(np.int16) + noise, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, "RGB")


PALETTES = {
    "Technical": ("#0B1640", "#2B4CFF", "#00E0FF"),
    "Cultural": ("#3A0A2E", "#E0457B", "#FFB347"),
    "Workshop": ("#062A26", "#10A37F", "#B7F171"),
    "Business": ("#2A1A05", "#E8890C", "#FFE08A"),
    "Gaming": ("#130A2E", "#7C3AED", "#22D3EE"),
    "Sports": ("#04210F", "#0F9D58", "#C6F432"),
    "Other": ("#111827", "#475569", "#E2E8F0"),
}


def motif(img, category, rnd):
    w, h = img.size
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    dark, mid, hi = (hex2rgb(c) for c in PALETTES.get(category, PALETTES["Other"]))
    if category == "Technical":
        step = w // 14
        for x in range(0, w, step):
            d.line([(x, 0), (x, h)], fill=hi + (28,), width=2)
        for y in range(0, h, step):
            d.line([(0, y), (w, y)], fill=hi + (28,), width=2)
        for _ in range(5):
            r = rnd.randint(w // 10, w // 4)
            cx, cy = rnd.randint(0, w), rnd.randint(0, h)
            d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=hi + (120,), width=6)
    elif category == "Cultural":
        cx, cy = int(w * 0.78), int(h * 0.28)
        for i in range(9, 0, -1):
            r = i * w // 14
            d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=hi + (40 + i * 8,), width=5)
        for k in range(24):
            ang = k * math.pi / 12
            d.line([(cx, cy), (cx + math.cos(ang) * w, cy + math.sin(ang) * w)], fill=hi + (22,), width=8)
    elif category == "Workshop":
        for x in range(0, w, 36):
            for y in range(0, h, 36):
                d.ellipse([x, y, x + 6, y + 6], fill=hi + (60,))
        d.rounded_rectangle([w * 0.55, h * 0.08, w * 1.1, h * 0.55], radius=60, outline=hi + (150,), width=10)
    elif category == "Business":
        n = 7
        bw = w // (n * 2)
        for i in range(n):
            bh = int(h * 0.12 + i * h * 0.07)
            x = int(w * 0.42) + i * (bw + 16)
            d.rounded_rectangle([x, h * 0.62 - bh, x + bw, h * 0.62], radius=12, fill=hi + (60 + i * 18,))
    elif category == "Gaming":
        for i in range(-h, w, 70):
            d.line([(i, 0), (i + h, h)], fill=hi + (34,), width=18)
        d.polygon([(w * 0.62, h * 0.1), (w * 0.95, h * 0.28), (w * 0.62, h * 0.46)], outline=hi + (180,), width=8)
    elif category == "Sports":
        cx, cy, r = int(w * 0.75), int(h * 0.3), int(w * 0.32)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=hi + (150,), width=10)
        d.arc([cx - r * 2, cy - r // 2, cx, cy + r * 1.5], 270, 90, fill=hi + (90,), width=8)
        d.line([(0, h * 0.62), (w, h * 0.5)], fill=hi + (50,), width=6)
    layer = layer.filter(ImageFilter.GaussianBlur(0.6))
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gx, gy, gr = rnd.randint(0, w), rnd.randint(0, h // 2), w // 2
    gd.ellipse([gx - gr, gy - gr, gx + gr, gy + gr], fill=hi + (70,))
    glow = glow.filter(ImageFilter.GaussianBlur(w // 8))
    base = img.convert("RGBA")
    base.alpha_composite(glow)
    base.alpha_composite(layer)
    return base.convert("RGB")


def wrap(draw, text, fnt, max_w):
    words, lines, line = text.split(), [], ""
    for w_ in words:
        test = (line + " " + w_).strip()
        if draw.textlength(test, font=fnt) <= max_w or not line:
            line = test
        else:
            lines.append(line)
            line = w_
    lines.append(line)
    return lines


def chip(draw, xy, text, fnt, fill, fg):
    x, y = xy
    tw = draw.textlength(text, font=fnt)
    bb = fnt.getbbox("Ag")
    h = bb[3] - bb[1] + 26
    draw.rounded_rectangle([x, y, x + tw + 40, y + h], radius=h // 2, fill=fill)
    draw.text((x + 20, y + 13 - bb[1]), text, font=fnt, fill=fg)
    return x + tw + 40


def poster(e, college, w=1080, h=1350, headline=None, sub=None, lines=None, seed=1):
    rnd = random.Random(seed)
    dark, mid, hi = PALETTES.get(e["category"], PALETTES["Other"])
    img = gradient(w, h, dark, mid, angle=rnd.choice([30, 55, 70, 110]))
    img = motif(img, e["category"], rnd)
    d = ImageDraw.Draw(img)
    pad = int(w * 0.075)
    # header
    d.text((pad, pad), college["name"].upper() if len(college["name"]) < 30 else college["name"], font=font("body6", int(w * 0.026)),
           fill=(255, 255, 255))
    chip(d, (pad, pad + int(w * 0.06)), e["category"], font("body7", int(w * 0.024)), hex2rgb(hi), hex2rgb(dark))
    # headline
    title = headline or e["title"]
    size = int(w * 0.13) if len(title) < 16 else int(w * 0.105) if len(title) < 26 else int(w * 0.088)
    fnt = font("display", size)
    tl = wrap(d, title, fnt, w - pad * 2)
    y = int(h * 0.50) - (len(tl) * size * 0.95) / 2
    for line in tl:
        d.text((pad, y), line, font=fnt, fill=(255, 255, 255))
        y += size * 0.98
    sub = sub if sub is not None else e["tagline"]
    if sub:
        sf = font("display5", int(w * 0.042))
        for line in wrap(d, sub, sf, w - pad * 2)[:3]:
            d.text((pad, y + 18), line, font=sf, fill=(255, 238, 214) if e["category"] == "Cultural" else hex2rgb(hi))
            y += int(w * 0.052)
    # footer info
    lines = lines or []
    fy = h - pad - int(w * 0.16)
    d.line([(pad, fy - 24), (w - pad, fy - 24)], fill=(255, 255, 255), width=2)
    bf = font("body7", int(w * 0.034))
    sf2 = font("body", int(w * 0.028))
    for i, (big, small) in enumerate(lines[:3]):
        x = pad + i * (w - pad * 2) // 3
        d.text((x, fy), big, font=font("body7", int(w * 0.034), big), fill=(255, 255, 255))
        d.text((x, fy + int(w * 0.048)), small, font=sf2, fill=(220, 225, 235))
    d.text((pad, h - pad + 4), "Register on EventFlow", font=font("body6", int(w * 0.024)), fill=hex2rgb(hi))
    return grain(img, 7)


def event_lines(e):
    start = datetime.now() + timedelta(days=e["day"])
    when = start.strftime("%d %b")
    t = f"{e['start'][0] % 12 or 12}:{e['start'][1]:02d} {'AM' if e['start'][0] < 12 else 'PM'}"
    fee = "Free" if not e["fee"] else f"₹{e['fee']}"
    venue = e["venue"] if len(e["venue"]) <= 15 else " ".join(e["venue"].split()[:2])
    return [(when, t), (venue, e["city"]), (fee, "per person" if e["team_size"] == 1 else f"teams of {e['team_size']}")]


def banner(e, college, seed):
    """Pure artwork (no text): the app overlays the real title on top."""
    w, h = 1600, 900
    rnd = random.Random(seed)
    dark, mid, hi = PALETTES.get(e["category"], PALETTES["Other"])
    img = gradient(w, h, dark, mid, angle=rnd.choice([20, 35, 160]))
    img = motif(img, e["category"], rnd)
    img = motif(img, e["category"], random.Random(seed + 99))
    return grain(img, 6)


def logo(c):
    s = 512
    c1, c2 = c["colors"]
    img = gradient(s, s, c1, c2, angle=45).convert("RGBA")
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, s, s], fill=255)
    d = ImageDraw.Draw(img)
    letters = "".join(w[0] for w in c["name"].replace("&", "").split() if w[0].isupper())[:3]
    f = font("display", 210 if len(letters) <= 2 else 170)
    tw = d.textlength(letters, font=f)
    bb = f.getbbox(letters)
    d.text(((s - tw) / 2, (s - (bb[3] - bb[1])) / 2 - bb[1]), letters, font=f, fill=(255, 255, 255))
    d.ellipse([18, 18, s - 18, s - 18], outline=(255, 255, 255, 90), width=6)
    out = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def cover(c, seed):
    w, h = 1500, 500
    rnd = random.Random(seed)
    c1, c2 = c["colors"]
    img = gradient(w, h, c1, c2, angle=15).convert("RGBA")
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for _ in range(14):
        r = rnd.randint(40, 260)
        x, y = rnd.randint(0, w), rnd.randint(-100, h + 100)
        d.ellipse([x - r, y - r, x + r, y + r], outline=(255, 255, 255, rnd.randint(30, 90)), width=rnd.randint(2, 8))
    img.alpha_composite(layer)
    return grain(img.convert("RGB"), 6)


def stat_card(e, college, title, stats, seed):
    w, h = 1080, 1350
    dark, mid, hi = PALETTES.get(e["category"], PALETTES["Other"])
    img = gradient(w, h, dark, dark, 0)
    img = motif(img, e["category"], random.Random(seed))
    d = ImageDraw.Draw(img)
    d.text((80, 90), title, font=font("display7", 64), fill=(255, 255, 255))
    d.text((80, 170), college["name"], font=font("body", 32), fill=(210, 215, 230))
    y = 320
    for big, small in stats:
        d.text((80, y), big, font=font("display", 150, big), fill=hex2rgb(hi))
        d.text((80, y + 160), small, font=font("body6", 40), fill=(255, 255, 255))
        y += 290
    return grain(img, 6)


def tracks_card(e, college):
    w, h = 1080, 1350
    dark, mid, hi = PALETTES["Technical"]
    img = gradient(w, h, "#0A1030", "#1A2C7A", 70)
    d = ImageDraw.Draw(img)
    d.text((80, 90), "Six tracks", font=font("display", 110), fill=(255, 255, 255))
    d.text((80, 220), "Pick one at Day 1 check-in", font=font("body6", 38), fill=hex2rgb(hi))
    tracks = ["Smart parking", "Campus energy", "Canteen queues", "Lost & found", "Safe commute", "Accessible campus"]
    y = 340
    for i, t in enumerate(tracks):
        d.rounded_rectangle([80, y, w - 80, y + 130], radius=28, fill=(28, 44, 110), outline=hex2rgb(hi), width=3)
        d.text((120, y + 34), f"{i + 1:02d}", font=font("display", 60), fill=hex2rgb(hi))
        d.text((260, y + 40), t, font=font("display7", 54), fill=(255, 255, 255))
        y += 152
    return grain(img, 6)


def upi_screenshot():
    w, h = 720, 1280
    img = Image.new("RGB", (w, h), (246, 248, 252))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w, 120], fill=(31, 41, 78))
    d.text((40, 42), "DEMO SCREENSHOT", font=font("body7", 34), fill=(255, 210, 90))
    d.ellipse([w / 2 - 90, 220, w / 2 + 90, 400], fill=(16, 163, 127))
    d.line([(w / 2 - 45, 312), (w / 2 - 10, 350), (w / 2 + 55, 270)], fill=(255, 255, 255), width=16)
    d.text((w / 2, 470), "Payment successful", font=font("display7", 52), fill=(20, 24, 40), anchor="mm")
    d.text((w / 2, 560), "₹299", font=font("display", 96, "₹"), fill=(20, 24, 40), anchor="mm")
    y = 680
    for k, v in [("To", "citfest@okaxis"), ("UPI ref no.", "4123 5678 9012"), ("Note", "Sample image for the demo")]:
        d.text((60, y), k, font=font("body", 32), fill=(110, 118, 140))
        d.text((w - 60, y), v, font=font("body6", 32), fill=(20, 24, 40), anchor="ra")
        y += 70
    return img


def video(src_jpg, out_mp4, seconds=6):
    """Slow push-in on a poster, H.264, plays everywhere."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", src_jpg, "-vf",
           f"scale=2160:-1,zoompan=z='min(zoom+0.0009,1.12)':d={seconds * 30}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=720x900:fps=30,format=yuv420p",
           "-t", str(seconds), "-c:v", "libx264", "-preset", "medium", "-crf", "26", "-movflags", "+faststart", out_mp4]
    subprocess.run(cmd, check=True)


def brochure(e, college, out_pdf, sections):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(out_pdf, pagesize=A4)
    W, H = A4
    dark, mid, hi = PALETTES.get(e["category"], PALETTES["Other"])
    c.setFillColor(dark)
    c.rect(0, H - 220, W, 220, fill=1, stroke=0)
    c.setFillColor("#FFFFFF")
    c.setFont("Helvetica-Bold", 30)
    c.drawString(50, H - 110, e["title"])
    c.setFont("Helvetica", 14)
    c.drawString(50, H - 140, college["name"])
    c.setFillColor(hi)
    c.drawString(50, H - 170, e["tagline"])
    y = H - 270
    for head, body in sections:
        c.setFillColor("#111827")
        c.setFont("Helvetica-Bold", 16)
        c.drawString(50, y, head)
        y -= 22
        c.setFont("Helvetica", 12)
        c.setFillColor("#374151")
        for line in body:
            c.drawString(60, y, "•  " + line)
            y -= 18
        y -= 16
    c.setFont("Helvetica-Oblique", 10)
    c.setFillColor("#6B7280")
    c.drawString(50, 40, "Demo document generated for the EventFlow sample data. Fictional institution.")
    c.save()


def main():
    cmap = {c["key"]: c for c in COLLEGES}
    emap = {e["key"]: e for e in EVENTS}
    for i, c in enumerate(COLLEGES):
        logo(c).save(os.path.join(OUT, f"logo_{c['key']}.png"))
        cover(c, i).save(os.path.join(OUT, f"cover_{c['key']}.jpg"), quality=80)
    for i, e in enumerate(EVENTS):
        banner(e, cmap[e["college"]], i + 10).save(os.path.join(OUT, e["banner"]), quality=80)
    posters = {
        "post_codestorm.jpg": ("codestorm", None, None),
        "post_roborumble.jpg": ("roborumble", None, None),
        "post_rhythm.jpg": ("rhythm", None, None),
        "post_photowalk.jpg": ("photowalk", None, "Sunrise. Sea. Your camera."),
        "post_pitch.jpg": ("pitch", None, None),
        "post_valorant.jpg": ("valorant", "Valorant Campus Cup", None),
        "post_technova.jpg": ("technova", "TechNova 2026", "Call for papers is open"),
        "post_football.jpg": ("football", None, None),
        "post_casechallenge.jpg": ("casechallenge", "Market Mavericks", "A live retail case in 3 hours"),
    }
    for i, (fname, (ek, head, sub)) in enumerate(posters.items()):
        e = emap[ek]
        poster(e, cmap[e["college"]], headline=head, sub=sub, lines=event_lines(e), seed=i + 3).save(os.path.join(OUT, fname), quality=82)
    e = emap["codestorm"]
    stat_card(e, cmap["cit"], "CodeStorm in numbers", [("36h", "of building"), ("₹1.5L", "in prizes"), ("6", "smart-campus tracks")], 5)\
        .save(os.path.join(OUT, "post_codestorm_stats.jpg"), quality=82)
    tracks_card(e, cmap["cit"]).save(os.path.join(OUT, "post_codestorm_tracks.jpg"), quality=82)
    stat_card(emap["aiworkshop"], cmap["cit"], "That's a wrap", [("60", "builders"), ("60", "working assistants"), ("1", "very long day")], 8)\
        .save(os.path.join(OUT, "post_ai_recap.jpg"), quality=82)
    stat_card(emap["poetry"], cmap["marina"], "Ink & Verse", [("31", "poets on stage"), ("2", "languages"), ("0", "sheets of paper")], 9)\
        .save(os.path.join(OUT, "post_poetry.jpg"), quality=82)
    camp = dict(emap["football"], title="Lights on.", tagline="New turf lights are live at Hillcrest")
    poster(camp, cmap["hillcrest"], lines=[("Turf 2", "now floodlit"), ("6 PM", "to 11 PM"), ("Free", "for students")], seed=21)\
        .save(os.path.join(OUT, "post_campus_hillcrest.jpg"), quality=82)
    upi_screenshot().save(os.path.join(OUT, "upi_screenshot_demo.jpg"), quality=80)
    video(os.path.join(OUT, "post_roborumble.jpg"), os.path.join(OUT, "teaser_roborumble.mp4"))
    video(os.path.join(OUT, "post_rhythm.jpg"), os.path.join(OUT, "teaser_rhythm.mp4"))
    brochure(emap["codestorm"], cmap["cit"], os.path.join(OUT, "CodeStorm_Rulebook.pdf"), [
        ("Teams", ["Up to 4 members", "Every member registers with the same team name", "Cross-college teams welcome"]),
        ("Timeline", ["Day 1, 9:30 AM: problem statements", "Day 1: two mentor rounds", "Day 2: personal 10-minute pitch slots"]),
        ("Judging", ["Impact 30%", "Working prototype 40%", "Pitch 20%", "Code quality 10%"]),
        ("Rules", ["All code written during the event", "Open-source libraries allowed", "Be kind. Help other teams."])])
    brochure(emap["technova"], cmap["hillcrest"], os.path.join(OUT, "TechNova_Brochure.pdf"), [
        ("Tracks", ["AI in healthcare", "Energy & climate", "Robotics & automation", "Open track"]),
        ("Papers", ["Abstract of up to 300 words", "8 minutes + 2 minutes Q&A", "Teams of up to 2"]),
        ("Prizes", ["Best paper per track", "Best project expo stall", "Quiz champions"])])
    # app icon
    icon = Image.new("RGBA", (512, 512), (15, 20, 36, 255))
    d = ImageDraw.Draw(icon)
    d.rounded_rectangle([96, 136, 416, 376], radius=40, fill=(245, 165, 36))
    d.ellipse([60, 216, 140, 296], fill=(15, 20, 36))
    d.ellipse([372, 216, 452, 296], fill=(15, 20, 36))
    for x in range(176, 340, 28):
        d.rectangle([x, 252, x + 12, 260], fill=(15, 20, 36))
    os.makedirs(os.path.join(ROOT, "static", "img"), exist_ok=True)
    icon.save(os.path.join(ROOT, "static", "img", "icon.png"))
    print("Seed media written to", OUT)


if __name__ == "__main__":
    main()
