"""Server-made documents: certificates (high-resolution PNG and PDF) and payment receipts (PDF).

Everything is drawn here with Pillow and ReportLab using fonts bundled in assets/fonts, so the files look exactly
the same on every computer and on Render, print sharply, and never depend on the browser's print dialog.
"""
import io
import math
import os
from functools import lru_cache

import qrcode
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

HERE = os.path.dirname(os.path.abspath(__file__))
FONTS = os.path.join(HERE, "assets", "fonts")

IVORY = (251, 248, 240)
NAVY = (20, 33, 61)
GOLD = (184, 137, 45)
GOLD_LIGHT = (221, 186, 108)
INK = (46, 50, 70)
MUTED = (110, 114, 130)

W, H = 3508, 2480          # A4 landscape at 300 dpi


@lru_cache(maxsize=64)
def font(name, size):
    return ImageFont.truetype(os.path.join(FONTS, name), size)


def _open(data):
    if not data:
        return None
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        return ImageOps.exif_transpose(img).convert("RGBA")
    except Exception:
        return None


# ------------------------------------------------------------------ drawing helpers
def _spaced(draw, xy_center, text, f, fill, spacing):
    """Centered text with letter spacing."""
    widths = [f.getlength(ch) for ch in text]
    total = sum(widths) + spacing * (len(text) - 1)
    x = xy_center[0] - total / 2
    for ch, w in zip(text, widths):
        draw.text((x, xy_center[1]), ch, font=f, fill=fill, anchor="ls")
        x += w + spacing
    return total


def _fit(text, name, size, max_w, min_size=40):
    while size > min_size and font(name, size).getlength(text) > max_w:
        size -= 4
    return font(name, size)


def _rich(draw, segments, center_x, top, max_w, line_h):
    """Word-wrapped, centered text made of (text, font, colour) runs. Returns the y below the last line."""
    words = []
    for text, f, color in segments:
        for i, w in enumerate(text.split(" ")):
            if w:
                words.append((w, f, color))
    lines, line, width = [], [], 0
    for w, f, color in words:
        ww = f.getlength(w)
        sp = f.getlength(" ") if line else 0
        if line and width + sp + ww > max_w:
            lines.append((line, width))
            line, width, sp = [], 0, 0
        line.append((w, f, color, sp))
        width += sp + ww
    if line:
        lines.append((line, width))
    y = top
    for line, width in lines:
        x = center_x - width / 2
        for w, f, color, sp in line:
            x += sp
            draw.text((x, y), w, font=f, fill=color, anchor="ls")
            x += f.getlength(w)
        y += line_h
    return y, len(lines)


def _logo_fitted(img, box):
    """Logos with transparency are drawn as they are; photos and square logos become a round medallion."""
    has_alpha = img.getchannel("A").getextrema()[0] < 250
    if has_alpha:
        img = img.copy()
        img.thumbnail((box, box), Image.LANCZOS)
        return img, False
    side = min(img.size)
    img = img.crop(((img.width - side) // 2, (img.height - side) // 2, (img.width + side) // 2, (img.height + side) // 2))
    img = img.resize((box, box), Image.LANCZOS)
    mask = Image.new("L", (box * 4, box * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, box * 4 - 1, box * 4 - 1), fill=255)
    img.putalpha(mask.resize((box, box), Image.LANCZOS))
    return img, True


def _signature(img, max_w, max_h):
    """Any signature photo (white paper or transparent PNG) becomes ink on a transparent background."""
    gray = img.convert("L")
    if img.getchannel("A").getextrema()[0] < 250:           # already transparent: keep its shape
        alpha = img.getchannel("A")
    else:
        alpha = ImageOps.autocontrast(ImageOps.invert(gray), cutoff=2)
        alpha = alpha.point(lambda v: 0 if v < 40 else min(255, int(v * 1.25)))
    bbox = alpha.getbbox()
    if bbox:
        alpha, gray = alpha.crop(bbox), gray.crop(bbox)
    ink = Image.new("RGBA", alpha.size, (*NAVY, 255))
    ink.putalpha(alpha)
    ink.thumbnail((max_w, max_h), Image.LANCZOS)
    return ink


def _seal(draw, cx, cy, r, top_text, mid_text, win):
    pts = []
    n = 64
    for i in range(n * 2):
        ang = math.pi * i / n
        rad = r if i % 2 == 0 else r * 0.93
        pts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
    draw.polygon(pts, fill=GOLD if win else GOLD_LIGHT)
    draw.ellipse((cx - r * .86, cy - r * .86, cx + r * .86, cy + r * .86), fill=NAVY)
    draw.ellipse((cx - r * .78, cy - r * .78, cx + r * .78, cy + r * .78), outline=GOLD_LIGHT, width=6)
    f1 = font("Cinzel-Bold.ttf", int(r * (.42 if len(mid_text) <= 4 else .26)))
    draw.text((cx, cy + r * .12), mid_text, font=f1, fill=GOLD_LIGHT, anchor="ms")
    f2 = font("Cinzel-SemiBold.ttf", int(r * .13))
    _spaced(draw, (cx, cy + r * .42), top_text, f2, (240, 228, 200), 6)
    sr, sy = r * .12, cy - r * .40                       # five-pointed star above the text
    star = [(cx + (sr if i % 2 == 0 else sr * .42) * math.cos(-math.pi / 2 + i * math.pi / 5),
             sy + (sr if i % 2 == 0 else sr * .42) * math.sin(-math.pi / 2 + i * math.pi / 5)) for i in range(10)]
    draw.polygon(star, fill=GOLD_LIGHT)


def _qr(data, size, fill=NAVY, back=IVORY):
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=1)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color=fill, back_color=back).convert("RGB")
    return img.resize((size, size), Image.NEAREST)


# ------------------------------------------------------------------ certificate
def certificate_png(c):
    """c: dict with name, kind ('participation'|'achievement'), college, college_name, department, event, category,
    fest, date, venue, team, award, position, code, issued, verify_url, logo (bytes), signature (bytes),
    signatory, signatory_title. Returns PNG bytes (3508 x 2480, 300 dpi)."""
    key = tuple(sorted((k, v if not isinstance(v, (bytes, bytearray)) else hash(bytes(v))) for k, v in c.items()))
    return _certificate_cached(key, c.get("logo"), c.get("signature"))


@lru_cache(maxsize=4)        # full-size PNGs are a few MB each; Render's free plan has 512 MB
def _certificate_cached(key, logo_bytes, sig_bytes):
    c = dict(key)
    win = c["kind"] == "achievement"
    img = Image.new("RGB", (W, H), IVORY)
    d = ImageDraw.Draw(img)

    # paper: a soft warm glow towards the edges (blurred, so there are no visible bands)
    glow = Image.new("L", (W // 10, H // 10), 0)
    ImageDraw.Draw(glow).rectangle((0, 0, W // 10, H // 10), outline=70, width=14)
    glow = glow.filter(ImageFilter.GaussianBlur(16)).resize((W, H), Image.BICUBIC)
    img.paste(Image.new("RGB", (W, H), (234, 222, 196)), mask=glow)

    logo = _open(logo_bytes)
    if logo:                                   # faint watermark
        wm, _round = _logo_fitted(logo, 1250)
        wm = wm.convert("LA").convert("RGBA")
        a = wm.getchannel("A").point(lambda v: int(v * .055))
        wm.putalpha(a)
        img.paste(wm, ((W - wm.width) // 2, (H - wm.height) // 2 + 120), wm)

    # frame
    d.rectangle((70, 70, W - 70, H - 70), outline=NAVY, width=28)
    d.rectangle((122, 122, W - 122, H - 122), outline=GOLD, width=7)
    d.rectangle((146, 146, W - 146, H - 146), outline=GOLD_LIGHT, width=2)
    for (x, y, sx, sy) in ((146, 146, 1, 1), (W - 146, 146, -1, 1), (146, H - 146, 1, -1), (W - 146, H - 146, -1, -1)):
        d.line((x, y + sy * 120, x + sx * 34, y + sy * 34, x + sx * 120, y), fill=GOLD, width=7)
        d.polygon(((x + sx * 54, y + sy * 34), (x + sx * 70, y + sy * 54), (x + sx * 54, y + sy * 74), (x + sx * 38, y + sy * 54)),
                  fill=GOLD)

    # organiser logo / monogram
    top = 230
    if logo:
        mark, rounded = _logo_fitted(logo, 260)
        x0, y0 = (W - mark.width) // 2, top
        if rounded:
            d.ellipse((x0 - 14, y0 - 14, x0 + mark.width + 14, y0 + mark.height + 14), outline=GOLD, width=8)
        img.paste(mark, (x0, y0), mark)
        y = y0 + mark.height + 110
    else:
        initials = "".join(w[0] for w in c["college"].split()[:3]).upper()
        d.ellipse((W / 2 - 130, top, W / 2 + 130, top + 260), fill=NAVY, outline=GOLD, width=10)
        d.text((W / 2, top + 130), initials, font=_fit(initials, "Cinzel-Bold.ttf", 110, 190, 50), fill=GOLD_LIGHT, anchor="mm")
        y = top + 370
    _spaced(d, (W / 2, y), c["college"].upper(), _fit(c["college"].upper(), "Cinzel-SemiBold.ttf", 62, 2300), NAVY, 10)
    y += 210
    _spaced(d, (W / 2, y), "CERTIFICATE", font("Cinzel-Bold.ttf", 190), NAVY, 26)
    y += 130
    sub = "OF ACHIEVEMENT" if win else "OF PARTICIPATION"
    sw = _spaced(d, (W / 2, y), sub, font("Cinzel-SemiBold.ttf", 66), GOLD, 26)
    for side in (-1, 1):
        x1 = W / 2 + side * (sw / 2 + 50)
        d.line((x1, y - 22, x1 + side * 300, y - 22), fill=GOLD, width=4)
        d.ellipse((x1 + side * 300 - 9, y - 31, x1 + side * 300 + 9, y - 13), fill=GOLD)
    y += 135
    d.text((W / 2, y), "This certificate is proudly presented to", font=font("CormorantGaramond-MediumItalic.ttf", 74),
           fill=MUTED, anchor="ms")
    y += 250
    name_font = _fit(c["name"], "GreatVibes-Regular.ttf", 250, 2300, 120)
    d.text((W / 2, y), c["name"], font=name_font, fill=NAVY, anchor="ms")
    y += 60
    d.line((W / 2 - 820, y, W / 2 + 820, y), fill=GOLD, width=5)
    d.ellipse((W / 2 - 12, y - 12, W / 2 + 12, y + 12), fill=GOLD)
    y += 120

    reg_f, bold_f = font("CormorantGaramond-Medium.ttf", 64), font("CormorantGaramond-SemiBold.ttf", 64)
    seg = []
    if c.get("college_name"):
        seg.append((f"of {c['college_name']}" + (f", {c['department']}," if c.get("department") else ","), reg_f, INK))
    if win:
        seg += [("for being named", reg_f, INK), (c["award"], bold_f, NAVY), ("in", reg_f, INK)]
    else:
        seg.append(("for successfully taking part in", reg_f, INK))
    seg.append((c["event"], bold_f, NAVY))
    if c.get("fest"):
        seg += [("at", reg_f, INK), (c["fest"] + ",", bold_f, NAVY)]
    else:
        seg[-1] = (c["event"] + ",", bold_f, NAVY)
    seg += [("organised by", reg_f, INK), (c["college"], bold_f, NAVY), (f"on {c['date']} at {c['venue']}", reg_f, INK)]
    if c.get("team"):
        seg += [("as a member of team", reg_f, INK), (c["team"] + ".", bold_f, NAVY)]
    else:
        seg[-1] = (seg[-1][0] + ".", reg_f, INK)
    _rich(d, seg, W / 2, y, 2500, 92)

    # signature block (left)
    sx, base = 920, 2150
    sig = _open(sig_bytes)
    if sig:
        ink = _signature(sig, 620, 210)
        img.paste(ink, (int(sx - ink.width / 2), base - 30 - ink.height), ink)
    elif c.get("signatory"):
        d.text((sx, base - 50), c["signatory"], font=_fit(c["signatory"], "GreatVibes-Regular.ttf", 120, 600), fill=NAVY, anchor="ms")
    d.line((sx - 340, base, sx + 340, base), fill=INK, width=4)
    d.text((sx, base + 70), c.get("signatory") or "Event Coordinator", font=font("CormorantGaramond-SemiBold.ttf", 58),
           fill=NAVY, anchor="ms")
    d.text((sx, base + 130), (c.get("signatory_title") or "Authorised signatory").upper(),
           font=_fit((c.get("signatory_title") or "Authorised signatory").upper(), "PublicSans-SemiBold.ttf", 32, 640), fill=MUTED,
           anchor="ms")

    # seal (centre)
    if win:
        mid = {1: "1ST", 2: "2ND", 3: "3RD"}.get(c.get("position"), "★")
        _seal(d, W / 2, 2040, 200, "AWARD", mid, True)
    else:
        _seal(d, W / 2, 2040, 200, "VERIFIED", "EF", False)

    # verification (right)
    qx = 2590
    q_img = _qr(c["verify_url"], 280)
    img.paste(q_img, (qx - 140, 1800))
    d.text((qx, 2140), "SCAN TO VERIFY", font=font("PublicSans-SemiBold.ttf", 30), fill=NAVY, anchor="ms")
    d.text((qx, 2195), f"Certificate no. {c['code']}", font=font("CormorantGaramond-SemiBold.ttf", 46), fill=INK, anchor="ms")
    d.text((qx, 2250), f"Issued {c['issued']}", font=font("CormorantGaramond-Medium.ttf", 42), fill=MUTED, anchor="ms")

    d.text((W / 2, H - 172), "Issued through EventFlow · by Team Tech Knights", font=font("PublicSans-Regular.ttf", 26),
           fill=(150, 150, 160), anchor="ms")
    out = io.BytesIO()
    img.save(out, "PNG", dpi=(300, 300), optimize=True)
    return out.getvalue()


@lru_cache(maxsize=16)
def png_preview(png, width=1600):
    img = Image.open(io.BytesIO(png))
    img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
    out = io.BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=88, optimize=True, progressive=True)
    return out.getvalue()


def certificate_pdf(png, title):
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    pw, ph = landscape(A4)
    cv = canvas.Canvas(buf, pagesize=(pw, ph))
    cv.setTitle(title)
    cv.setAuthor("EventFlow")
    cv.drawImage(ImageReader(io.BytesIO(png)), 0, 0, pw, ph)
    cv.showPage()
    cv.save()
    return buf.getvalue()


# ------------------------------------------------------------------ receipt
_registered = False


def _fonts_for_pdf():
    global _registered
    if _registered:
        return
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    for name, file in (("PS", "PublicSans-Regular.ttf"), ("PS-Semi", "PublicSans-SemiBold.ttf"),
                       ("PS-Bold", "PublicSans-Bold.ttf"), ("Cinzel", "Cinzel-Bold.ttf")):
        pdfmetrics.registerFont(TTFont(name, os.path.join(FONTS, file)))
    _registered = True


def inr(v):
    try:
        v = float(v or 0)
    except (TypeError, ValueError):
        v = 0
    return "₹" + f"{v:,.0f}"


def receipt_pdf(r):
    """r: dict with receipt_no, status, created_at, paid_at, method, utr, college, college_upi, college_logo (bytes),
    buyer, buyer_username, buyer_email, items [(title, sub, base, discount, points, amount)], total, base_total,
    discount_total, points_total, note, verify_url, ticket_codes. Returns PDF bytes (A4)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    _fonts_for_pdf()
    buf = io.BytesIO()
    pw, ph = A4
    cv = canvas.Canvas(buf, pagesize=A4)
    cv.setTitle(f"Receipt {r['receipt_no']}")
    cv.setAuthor("EventFlow")
    navy, gold = (20 / 255, 33 / 255, 61 / 255), (184 / 255, 137 / 255, 45 / 255)
    m = 42

    # header band
    cv.setFillColorRGB(*navy)
    cv.rect(0, ph - 150, pw, 150, stroke=0, fill=1)
    cv.setFillColorRGB(*gold)
    cv.roundRect(m, ph - 74, 26, 26, 6, stroke=0, fill=1)
    cv.setFillColorRGB(1, 1, 1)
    cv.setFont("PS-Bold", 20)
    cv.drawString(m + 36, ph - 68, "EventFlow")
    cv.setFont("Cinzel", 15)
    cv.drawRightString(pw - m, ph - 66, "PAYMENT RECEIPT")
    cv.setFont("PS", 9.5)
    cv.setFillColorRGB(.8, .83, .9)
    cv.drawString(m, ph - 108, f"Receipt {r['receipt_no']}")
    cv.drawString(m, ph - 123, f"Issued {r['created_at']}")
    status = {"paid": ("PAID", (0.13, 0.6, 0.35)), "refunded": ("REFUNDED", (0.85, 0.55, 0.1)),
              "submitted": ("UNDER REVIEW", (0.85, 0.55, 0.1)), "created": ("UNPAID", (0.8, 0.25, 0.3)),
              "rejected": ("REJECTED", (0.8, 0.25, 0.3))}.get(r["status"], (r["status"].upper(), (0.5, 0.5, 0.5)))
    cv.setFont("PS-Bold", 11)
    tw = cv.stringWidth(status[0], "PS-Bold", 11) + 26
    cv.setFillColorRGB(*status[1])
    cv.roundRect(pw - m - tw, ph - 124, tw, 24, 12, stroke=0, fill=1)
    cv.setFillColorRGB(1, 1, 1)
    cv.drawCentredString(pw - m - tw / 2, ph - 116, status[0])

    # organiser + billed to
    y = ph - 196
    logo = _open(r.get("college_logo"))
    x_text = m
    if logo:
        mark, rounded = _logo_fitted(logo, 200)
        bg = Image.new("RGBA", mark.size, (255, 255, 255, 0))
        bg.alpha_composite(mark)
        cv.drawImage(ImageReader(bg), m, y - 38, 48, 48, mask="auto")
        x_text = m + 60
    cv.setFillColorRGB(.42, .44, .5)
    cv.setFont("PS-Semi", 8.5)
    cv.drawString(x_text, y + 2, "ORGANISER (PAID TO)")
    cv.setFillColorRGB(*navy)
    cv.setFont("PS-Bold", 13)
    cv.drawString(x_text, y - 15, r["college"][:52])
    cv.setFont("PS", 9.5)
    cv.setFillColorRGB(.3, .32, .4)
    if r.get("college_upi"):
        cv.drawString(x_text, y - 30, f"UPI {r['college_upi']}")
    cv.setFillColorRGB(.42, .44, .5)
    cv.setFont("PS-Semi", 8.5)
    cv.drawString(pw / 2 + 20, y + 2, "BILLED TO")
    cv.setFillColorRGB(*navy)
    cv.setFont("PS-Bold", 13)
    cv.drawString(pw / 2 + 20, y - 15, r["buyer"][:40])
    cv.setFont("PS", 9.5)
    cv.setFillColorRGB(.3, .32, .4)
    cv.drawString(pw / 2 + 20, y - 30, f"@{r['buyer_username']}" + (f" · {r['buyer_email']}" if r.get("buyer_email") else ""))

    # amount
    y -= 92
    cv.setFillColorRGB(.97, .95, .9)
    cv.roundRect(m, y - 22, pw - 2 * m, 62, 10, stroke=0, fill=1)
    cv.setFillColorRGB(.42, .44, .5)
    cv.setFont("PS-Semi", 9)
    cv.drawString(m + 18, y + 20, "TOTAL PAID" if r["status"] == "paid" else "AMOUNT")
    cv.setFillColorRGB(*navy)
    cv.setFont("PS-Bold", 26)
    cv.drawString(m + 18, y - 8, inr(r["total"]))
    cv.setFont("PS", 9.5)
    cv.setFillColorRGB(.3, .32, .4)
    method = {"upi": "UPI", "demo": "Demo payment", "free": "Free"}.get(r["method"], r["method"])
    cv.drawRightString(pw - m - 18, y + 14, f"Method: {method}")
    if r.get("utr"):
        cv.drawRightString(pw - m - 18, y - 2, f"UPI reference (UTR): {r['utr']}")
    if r.get("paid_at"):
        cv.drawRightString(pw - m - 18, y - 16, f"Confirmed {r['paid_at']}")

    # items
    y -= 62
    cv.setFont("PS-Semi", 8.5)
    cv.setFillColorRGB(.42, .44, .5)
    cols = (m, pw - m - 230, pw - m - 150, pw - m)
    cv.drawString(cols[0], y, "ITEM")
    cv.drawRightString(cols[1] + 40, y, "PRICE")
    cv.drawRightString(cols[2] + 50, y, "SAVINGS")
    cv.drawRightString(cols[3], y, "AMOUNT")
    y -= 8
    cv.setStrokeColorRGB(.85, .85, .88)
    cv.line(m, y, pw - m, y)
    for title, sub, base, disc, pts, amount in r["items"]:
        if y < 170:
            cv.showPage()
            y = ph - 60
        y -= 20
        cv.setFillColorRGB(*navy)
        cv.setFont("PS-Semi", 10.5)
        cv.drawString(cols[0], y, title[:60])
        cv.setFont("PS", 10)
        cv.setFillColorRGB(.2, .22, .3)
        cv.drawRightString(cols[1] + 40, y, inr(base))
        cv.drawRightString(cols[2] + 50, y, ("−" + inr((disc or 0) + (pts or 0))) if (disc or pts) else "—")
        cv.setFont("PS-Semi", 10.5)
        cv.drawRightString(cols[3], y, inr(amount))
        if sub:
            y -= 13
            cv.setFont("PS", 8.5)
            cv.setFillColorRGB(.45, .47, .55)
            cv.drawString(cols[0], y, sub[:90])
        y -= 9
        cv.line(m, y, pw - m, y)

    # totals
    y -= 22
    rows = [("Subtotal", inr(r["base_total"]))]
    if r.get("discount_total"):
        rows.append(("Coupon discount", "−" + inr(r["discount_total"])))
    if r.get("points_total"):
        rows.append((f"EventFlow points ({r['points_total']})", "−" + inr(r["points_total"])))
    rows.append(("Total", inr(r["total"])))
    for i, (k, v) in enumerate(rows):
        last = i == len(rows) - 1
        cv.setFont("PS-Bold" if last else "PS", 12 if last else 10)
        cv.setFillColorRGB(*(navy if last else (.3, .32, .4)))
        cv.drawRightString(pw - m - 120, y, k)
        cv.drawRightString(pw - m, y, v)
        y -= 18 if not last else 24

    # verification + note
    qr_img = _qr(r["verify_url"], 300, fill=NAVY, back=(255, 255, 255))
    cv.drawImage(ImageReader(qr_img), m, 70, 84, 84)
    cv.setFont("PS-Semi", 9)
    cv.setFillColorRGB(*navy)
    cv.drawString(m + 96, 138, "Ticket " + ", ".join(r["ticket_codes"])[:80])
    cv.setFont("PS", 8.5)
    cv.setFillColorRGB(.4, .42, .5)
    lines = [f"Scan to verify this ticket. Paid directly to {r['college']}; EventFlow never holds the money.",
             "Questions about this payment? Contact the organiser from the event page."]
    if r.get("note"):
        lines.insert(0, r["note"][:110])
    for i, line in enumerate(lines):
        cv.drawString(m + 96, 122 - i * 13, line)
    cv.setStrokeColorRGB(*gold)
    cv.setLineWidth(2)
    cv.line(m, 50, pw - m, 50)
    cv.setFont("PS", 8)
    cv.setFillColorRGB(.55, .56, .62)
    cv.drawString(m, 36, "EventFlow · campus events, by Team Tech Knights")
    cv.drawRightString(pw - m, 36, f"Receipt {r['receipt_no']}")
    cv.showPage()
    cv.save()
    return buf.getvalue()
