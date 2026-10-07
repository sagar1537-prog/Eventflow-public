/* EventFlow front-end. No framework, progressive enhancement: every action also works as a plain form. */
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const csrf = $('meta[name="csrf-token"]')?.content || "";
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  document.documentElement.classList.add("js");
  const loggedIn = document.body.dataset.auth === "1";

  async function api(url, data, method = "POST") {
    const opts = { method, headers: { "X-CSRF-Token": csrf, "Accept": "application/json" } };
    if (data !== undefined && method !== "GET") { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(data); }
    const res = await fetch(url, opts);
    let json = {};
    try { json = await res.json(); } catch (e) { /* ignore */ }
    if (res.status === 401 && json.login) { location.href = json.login + "?next=" + encodeURIComponent(location.pathname); throw new Error("login"); }
    if (!res.ok) throw new Error(json.error || "Something went wrong. Try again.");
    return json;
  }
  const esc = (s) => { const d = document.createElement("div"); d.textContent = s ?? ""; return d.innerHTML; };
  const fmt = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/\n/g, "<br>");
  window.EF = { api, esc, fmt };

  // ------------------------------------------------------------ toasts
  const toastBox = $(".toasts") || document.body.appendChild(Object.assign(document.createElement("div"), { className: "toasts" }));
  function toast(msg, kind = "info", ms = 4200) {
    const t = document.createElement("div");
    t.className = `toast ${kind}`;
    t.setAttribute("role", "status");
    t.innerHTML = `<span class="ti">${kind === "success" ? "✓" : kind === "error" ? "!" : "i"}</span><div>${esc(msg)}</div><button aria-label="Dismiss">×</button>`;
    toastBox.appendChild(t);
    const kill = () => { t.classList.add("out"); setTimeout(() => t.remove(), 350); };
    t.querySelector("button").onclick = kill;
    setTimeout(kill, ms);
  }
  window.EF.toast = toast;
  $$("[data-flash]").forEach((f, i) => setTimeout(() => toast(f.dataset.flash, f.dataset.kind), 120 + i * 140));

  // ------------------------------------------------------------ ripple + loading buttons
  document.addEventListener("pointerdown", (e) => {
    const b = e.target.closest(".btn");
    if (!b || reduced) return;
    const r = b.getBoundingClientRect(), s = Math.max(r.width, r.height);
    const span = document.createElement("span");
    span.className = "ripple";
    span.style.cssText = `width:${s}px;height:${s}px;left:${e.clientX - r.left - s / 2}px;top:${e.clientY - r.top - s / 2}px`;
    b.appendChild(span);
    setTimeout(() => span.remove(), 600);
  });
  document.addEventListener("submit", (e) => {
    const f = e.target;
    if (f.dataset.confirm && !confirm(f.dataset.confirm)) { e.preventDefault(); return; }
    if (f.dataset.ajax) return;
    const b = f.querySelector("button[type=submit]:not([data-noload]), button:not([type]):not([data-noload])");
    if (b && !f.target) setTimeout(() => b.classList.add("is-loading"), 10);
  });

  // ------------------------------------------------------------ reveal on scroll + count-up
  const io = "IntersectionObserver" in window ? new IntersectionObserver((entries) => {
    entries.forEach((en) => {
      if (!en.isIntersecting) return;
      show(en.target);
      io.unobserve(en.target);
    });
  }, { rootMargin: "0px 0px -6% 0px" }) : null;
  function watch(root = document) {
    $$(".reveal:not(.in), [data-count]:not(.counted)", root).forEach((el, i) => {
      if (el.classList.contains("reveal") && !el.style.getPropertyValue("--d")) el.style.setProperty("--d", `${Math.min(i, 8) * 0.05}s`);
      io ? io.observe(el) : show(el);
    });
  }
  // reveal once, then drop the helper classes so hover effects on the element work normally
  function show(el) {
    if (el.dataset.count !== undefined && !el.classList.contains("counted")) countUp(el);
    if (!el.classList.contains("reveal") || el.classList.contains("in")) return;
    el.classList.add("in");
    const delay = parseFloat(el.style.getPropertyValue("--d") || "0") * 1000;
    setTimeout(() => el.classList.remove("reveal", "in"), 800 + delay);
  }
  function countUp(el) {
    el.classList.add("counted");
    const target = parseFloat(el.dataset.count || "0"), prefix = el.dataset.prefix || "", suffix = el.dataset.suffix || "";
    if (reduced || !target) { el.textContent = prefix + target.toLocaleString("en-IN") + suffix; return; }
    const t0 = performance.now(), dur = 900;
    const step = (t) => {
      const p = Math.min(1, (t - t0) / dur), v = target * (1 - Math.pow(1 - p, 3));
      el.textContent = prefix + Math.round(v).toLocaleString("en-IN") + suffix;
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }
  watch();
  // safety net: anything already scrolled into view is shown even if the observer misses it
  const sweep = () => $$(".reveal:not(.in), [data-count]:not(.counted)").forEach((el) => { if (el.getBoundingClientRect().top < innerHeight * 1.05) show(el); });
  addEventListener("scroll", () => requestAnimationFrame(sweep), { passive: true });
  addEventListener("load", sweep);
  setTimeout(sweep, 400);

  // ------------------------------------------------------------ 3D tilt
  function tilt(root = document) {
    if (reduced) return;
    if (matchMedia("(hover: none)").matches) return touchTilt(root);
    $$("[data-tilt]", root).forEach((el) => {
      if (el._tilt) return;
      el._tilt = true;
      const max = parseFloat(el.dataset.tilt || "6");
      el.addEventListener("pointermove", (e) => {
        const r = el.getBoundingClientRect(), x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
        el.style.transform = `perspective(900px) rotateX(${(0.5 - y) * max}deg) rotateY(${(x - 0.5) * max}deg) translateY(-4px)`;
        el.style.setProperty("--gx", `${x * 100}%`);
        el.style.setProperty("--gy", `${y * 100}%`);
      });
      el.addEventListener("pointerleave", () => { el.style.transform = ""; });
    });
  }
  // phones: the same 3D tilt follows your finger while you press a card
  function touchTilt(root) {
    $$("[data-tilt]", root).forEach((el) => {
      if (el._tilt) return;
      el._tilt = true;
      const max = parseFloat(el.dataset.tilt || "6") * 1.4;
      const move = (t) => {
        const r = el.getBoundingClientRect(), x = (t.clientX - r.left) / r.width, y = (t.clientY - r.top) / r.height;
        if (x < -0.2 || x > 1.2 || y < -0.2 || y > 1.2) return;
        el.style.transition = "transform .12s ease-out";
        el.style.transform = `perspective(800px) rotateX(${(0.5 - y) * max}deg) rotateY(${(x - 0.5) * max}deg) scale(.985)`;
        el.style.setProperty("--gx", `${x * 100}%`); el.style.setProperty("--gy", `${y * 100}%`);
      };
      el.addEventListener("touchstart", (e) => { el.classList.add("touching"); move(e.touches[0]); }, { passive: true });
      el.addEventListener("touchmove", (e) => move(e.touches[0]), { passive: true });
      const end = () => { el.classList.remove("touching"); el.style.transition = "transform .6s cubic-bezier(.34,1.56,.64,1)"; el.style.transform = ""; };
      el.addEventListener("touchend", end, { passive: true });
      el.addEventListener("touchcancel", end, { passive: true });
    });
  }
  tilt();

  // ------------------------------------------------------------ sticky register bar on phones (event page)
  const cta = $("[data-cta]"), regBox = $("#register");
  if (cta && regBox && "IntersectionObserver" in window) {
    new IntersectionObserver(([en]) => cta.classList.toggle("away", en.isIntersecting), { rootMargin: "0px 0px -120px 0px" }).observe(regBox);
    if (!cta.classList.contains("fest-cta")) $("[data-cta-go]", cta)?.addEventListener("click", (e) => {
      e.preventDefault();
      regBox.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
      regBox.querySelector(".card")?.animate([{ boxShadow: "0 0 0 0 rgba(245,165,36,.7)" }, { boxShadow: "0 0 0 14px rgba(245,165,36,0)" }], { duration: 1200, delay: 450 });
    });
  }

  // ------------------------------------------------------------ Android app (WebView) bridge
  // The Android wrapper adds "EventFlowApp" to its user agent and exposes window.EventFlowAndroid (see ANDROID.md).
  const app = window.EventFlowAndroid;
  if (/EventFlowApp/.test(navigator.userAgent) || app) document.documentElement.classList.add("in-app");
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-save-pdf]");
    if (!b) return;
    e.preventDefault();
    if (app && app.printPage) app.printPage(b.dataset.savePdf || document.title);   // native PrintManager -> Save as PDF
    else window.print();
  });

  // ------------------------------------------------------------ fest: pick several events, live total, pay once
  const festForm = $("[data-fest-form]");
  if (festForm) {
    const inr = (v) => "₹" + Math.round(v).toLocaleString("en-IN");
    const lines = $("[data-lines]"), go = $("[data-fest-go]");
    const ctaTotal = $("[data-cta-total]"), ctaCount = $("[data-cta-count]"), ctaGo = $(".fest-cta [data-cta-go]");
    const calc = () => {
      let total = 0, count = 0; const rows = [];
      $$(".track-card", festForm).forEach((tc) => {
        const picked = $$(".sub-row input[name=pick]:checked", tc).map((i) => i.closest(".sub-row"));
        if (!picked.length) return;
        count += picked.length;
        const tname = $("h2", tc).textContent.trim();
        if (tc.dataset.pricing === "pass") {
          const fee = tc.dataset.hasPass === "1" ? 0 : +tc.dataset.passFee;
          total += fee;
          rows.push([`${tname} pass`, `${picked.length} event${picked.length > 1 ? "s" : ""}: ${picked.map((r) => $("b", r).textContent).join(", ")}`, fee, tc.dataset.hasPass === "1" ? "Already yours" : null]);
        } else {
          picked.forEach((r) => { const fee = +r.dataset.fee; total += fee;
            rows.push([$("b", r).textContent, r.dataset.feeType === "team" && fee ? "Team fee (captain pays)" : tname, fee, null]); });
        }
      });
      if (lines) lines.innerHTML = count ? rows.map(([a, b, fee, tag]) => `<div><span><b>${esc(a)}</b><br><small class="muted">${esc(b)}</small></span><span>${tag ? esc(tag) : fee ? inr(fee) : "Free"}</span></div>`).join("")
        + `<div class="total"><span>Total</span><span>${total ? inr(total) : "Free"}</span></div>`
        : `<p class="muted small mb-0">Tick the events you want above. You pay once for all of them.</p>`;
      if (!go) return;
      go.disabled = !count;
      go.textContent = count ? `Register for ${count} event${count > 1 ? "s" : ""} · ${total ? inr(total) : "Free"}` : "Pick events to continue";
      if (ctaTotal) { ctaTotal.textContent = count ? (total ? inr(total) : "Free") : "Pick events"; ctaCount.textContent = count ? `${count} event${count > 1 ? "s" : ""} selected` : ctaCount.dataset.idle || ctaCount.textContent; }
      if (ctaGo) ctaGo.textContent = count ? "Register" : "Choose";
    };
    if (ctaCount) ctaCount.dataset.idle = ctaCount.textContent;
    const sync = (input) => {
      const row = input.closest(".sub-row"); row.classList.toggle("on", input.checked);
      const team = $(".team-in", row);
      if (team) { team.hidden = !input.checked; const t = $("input", team); t.required = input.checked; if (input.checked && !t.value) setTimeout(() => t.focus({ preventScroll: true }), 50); }
    };
    $$(".sub-row input[name=pick]", festForm).forEach((i) => { sync(i); i.addEventListener("change", () => { sync(i); calc(); if (i.checked && navigator.vibrate) navigator.vibrate(8); }); });
    // typing a team name shouldn't toggle the row
    $$(".team-in", festForm).forEach((t) => t.addEventListener("click", (e) => e.preventDefault()));
    $$(".team-in input", festForm).forEach((t) => t.addEventListener("click", (e) => { e.stopPropagation(); t.focus(); }));
    $$(".sub-more", festForm).forEach((a) => a.addEventListener("click", (e) => { e.stopPropagation(); }));
    calc();
    ctaGo?.addEventListener("click", (e) => {
      e.preventDefault();
      if (!$$(".sub-row input[name=pick]:checked", festForm).length) { $(".track-card", festForm)?.scrollIntoView({ behavior: "smooth", block: "start" }); return; }
      const missing = $$(".team-in:not([hidden]) input", festForm).find((t) => !t.value.trim());
      if (missing) { missing.scrollIntoView({ behavior: "smooth", block: "center" }); missing.focus({ preventScroll: true }); toast("Enter a team name for each team event.", "error"); return; }
      festForm.requestSubmit ? festForm.requestSubmit() : festForm.submit();
    });
  }

  // ------------------------------------------------------------ tables become labelled cards on phones
  $$("table.data").forEach((t) => {
    const heads = $$("thead th", t).map((th) => th.textContent.trim());
    if (!heads.length) return;
    t.classList.add("stackable");
    $$("tbody tr", t).forEach((tr) => $$(":scope > td", tr).forEach((td, i) => {
      const label = heads[i] || "";
      if (td.colSpan > 1) { td.classList.add("td-full"); return; }
      td.dataset.label = label;
      if (!label || i === 0) td.classList.add("td-full");
    }));
  });

  // ------------------------------------------------------------ tabs (panes + sliding bar)
  function placeBar(tabs) {
    let bar = tabs.querySelector(".bar");
    if (!bar) { bar = document.createElement("i"); bar.className = "bar"; tabs.appendChild(bar); }
    const on = tabs.querySelector(".on");
    if (on) { bar.style.left = on.offsetLeft + "px"; bar.style.width = on.offsetWidth + "px"; }
  }
  $$(".tabs").forEach((tabs) => {
    placeBar(tabs);
    if (tabs.dataset.panes === undefined) return;
    const show = (id) => {
      const pane = id && document.getElementById(id);
      if (!pane || !pane.classList.contains("tab-pane")) return false;
      $$(".tab-pane", tabs.parentElement).forEach((p) => p.classList.toggle("on", p === pane));
      $$("a", tabs).forEach((a) => a.classList.toggle("on", a.getAttribute("href") === "#" + id));
      placeBar(tabs);
      watch(pane);
      return true;
    };
    tabs.addEventListener("click", (e) => {
      const a = e.target.closest("a[href^='#']");
      if (!a) return;
      e.preventDefault();
      show(a.getAttribute("href").slice(1));
      history.replaceState(null, "", a.getAttribute("href"));
    });
    if (!show(location.hash.slice(1))) show(tabs.querySelector("a").getAttribute("href").slice(1));
    addEventListener("hashchange", () => show(location.hash.slice(1)));
  });
  addEventListener("resize", () => $$(".tabs").forEach(placeBar));

  // ------------------------------------------------------------ carousels, double-tap like, autoplay video
  function carousels(root = document) {
    $$(".post-media", root).forEach((m) => {
      if (m._c) return;
      m._c = true;
      const track = m.querySelector(".track"), slides = $$(".slide", m), dots = $$(".dots i", m), counter = m.querySelector(".counter");
      let i = 0, sx = null, dx = 0;
      const go = (n) => {
        i = Math.max(0, Math.min(slides.length - 1, n));
        track.style.transform = `translateX(${-i * 100}%)`;
        dots.forEach((d, k) => d.classList.toggle("on", k === i));
        if (counter) counter.textContent = `${i + 1}/${slides.length}`;
        slides.forEach((s, k) => { const v = s.querySelector("video"); if (v) k === i ? v.play().catch(() => {}) : v.pause(); });
        m.querySelector(".prev")?.toggleAttribute("hidden", i === 0);
        m.querySelector(".next")?.toggleAttribute("hidden", i === slides.length - 1);
      };
      m.querySelector(".prev")?.addEventListener("click", (e) => { e.stopPropagation(); go(i - 1); });
      m.querySelector(".next")?.addEventListener("click", (e) => { e.stopPropagation(); go(i + 1); });
      m.addEventListener("pointerdown", (e) => { sx = e.clientX; dx = 0; track.style.transition = "none"; });
      m.addEventListener("pointermove", (e) => {
        if (sx === null || slides.length < 2) return;
        dx = e.clientX - sx;
        track.style.transform = `translateX(calc(${-i * 100}% + ${dx}px))`;
      });
      const end = () => {
        if (sx === null) return;
        track.style.transition = "";
        if (Math.abs(dx) > 50) go(i + (dx < 0 ? 1 : -1)); else go(i);
        sx = null;
      };
      m.addEventListener("pointerup", end);
      m.addEventListener("pointerleave", end);
      let last = 0;
      m.addEventListener("click", (e) => {
        if (Math.abs(dx) > 8) return;
        const now = Date.now();
        if (now - last < 320) {
          const post = m.closest("[data-post]");
          const btn = post && post.querySelector("[data-like]");
          if (btn) like(btn, true);
          const h = document.createElement("div");
          h.innerHTML = `<svg class="big-heart" viewBox="0 0 24 24" fill="currentColor"><path d="M12 21s-7.5-4.6-9.6-9.3C.9 8.3 3 4.5 6.6 4.5c2.1 0 3.6 1.2 5.4 3.2 1.8-2 3.3-3.2 5.4-3.2 3.6 0 5.7 3.8 4.2 7.2C19.5 16.4 12 21 12 21z"/></svg>`;
          m.appendChild(h.firstChild);
          setTimeout(() => m.querySelector(".big-heart")?.remove(), 950);
        }
        last = now;
      });
      go(0);
    });
    if ("IntersectionObserver" in window) {
      const vo = new IntersectionObserver((es) => es.forEach((en) => {
        const v = en.target;
        if (en.isIntersecting && v.closest(".slide, .pgrid, .phone") ) { v.play().catch(() => {}); } else { v.pause(); }
      }), { threshold: 0.6 });
      $$("video[data-autoplay]", root).forEach((v) => vo.observe(v));
    }
  }
  carousels();

  // ------------------------------------------------------------ likes / saves / comments
  function burst(btn) {
    if (reduced) return;
    const colors = ["#F0405E", "#F5A524", "#7C5CFF", "#22D3EE"];
    for (let k = 0; k < 10; k++) {
      const p = document.createElement("i");
      const ang = (k / 10) * Math.PI * 2, dist = 22 + Math.random() * 12;
      p.className = "burst";
      p.style.background = colors[k % colors.length];
      p.style.setProperty("--x", `${Math.cos(ang) * dist}px`);
      p.style.setProperty("--y", `${Math.sin(ang) * dist}px`);
      btn.appendChild(p);
      setTimeout(() => p.remove(), 650);
    }
  }
  async function like(btn, onlyLike = false) {
    if (!loggedIn) { location.href = "/login?next=" + encodeURIComponent(location.pathname); return; }
    if (onlyLike && btn.classList.contains("liked")) return;
    const was = btn.classList.contains("liked");
    btn.classList.toggle("liked", !was);
    if (!was) burst(btn);
    const counter = btn.closest("[data-post]")?.querySelector("[data-likes]");
    try {
      const r = await api(`/api/posts/${btn.dataset.like}/like`, { only_like: onlyLike });
      btn.classList.toggle("liked", r.liked);
      if (counter) counter.textContent = r.likes === 1 ? "1 like" : `${r.likes.toLocaleString("en-IN")} likes`;
    } catch (e) { btn.classList.toggle("liked", was); if (e.message !== "login") toast(e.message, "error"); }
  }
  document.addEventListener("click", async (e) => {
    const lb = e.target.closest("[data-like]");
    if (lb) { e.preventDefault(); like(lb); return; }
    const sb = e.target.closest("[data-save-post]");
    if (sb) {
      e.preventDefault();
      try { const r = await api(`/api/posts/${sb.dataset.savePost}/save`); sb.classList.toggle("saved", r.saved); toast(r.saved ? "Saved to your collection" : "Removed from saved", "success", 2000); }
      catch (err) { if (err.message !== "login") toast(err.message, "error"); }
      return;
    }
    const se = e.target.closest("[data-save-event]");
    if (se) {
      e.preventDefault(); e.stopPropagation();
      try { const r = await api(`/api/events/${se.dataset.saveEvent}/save`); se.classList.toggle("saved", r.saved); toast(r.saved ? "Event saved" : "Removed from saved", "success", 2000); }
      catch (err) { if (err.message !== "login") toast(err.message, "error"); }
      return;
    }
    const fb = e.target.closest("[data-follow]");
    if (fb) {
      e.preventDefault();
      fb.classList.add("is-loading");
      try {
        const r = await api(`/api/follow/${fb.dataset.follow}`);
        fb.classList.remove("is-loading");
        fb.textContent = r.following ? "Following" : "Follow";
        fb.classList.toggle("ghost", r.following); fb.classList.toggle("brand", !r.following);
        $$(`[data-followers="${fb.dataset.follow}"]`).forEach((el) => { el.textContent = r.followers.toLocaleString("en-IN"); });
        if (r.following) toast("Following. Their posts will show up in your feed.", "success", 2400);
      } catch (err) { fb.classList.remove("is-loading"); if (err.message !== "login") toast(err.message, "error"); }
      return;
    }
    const fr = e.target.closest("[data-friend]");
    if (fr) {
      e.preventDefault();
      const [uid, action] = fr.dataset.friend.split(":");
      fr.classList.add("is-loading");
      try {
        const r = await api(`/api/friends/${uid}/${action}`);
        const box = fr.closest("[data-friend-box]") || fr.parentElement;
        const labels = { friends: "Friends ✓", outgoing: "Requested", none: "Add friend", incoming: "Accept" };
        if (box.dataset.friendBox === "remove-card") { box.closest(".person-card, .nitem, .suggest")?.remove(); }
        else {
          box.innerHTML = r.status === "friends" ? `<button class="btn ghost sm" data-friend="${uid}:remove" title="Remove friend">${labels.friends}</button>`
            : r.status === "outgoing" ? `<button class="btn soft sm" data-friend="${uid}:cancel">Requested</button>`
            : `<button class="btn brand sm" data-friend="${uid}:request">Add friend</button>`;
        }
        const msg = { friends: "You're now friends! 🎉", outgoing: "Friend request sent", none: action === "decline" ? "Request declined" : "Updated" }[r.status];
        if (msg) toast(msg, "success", 2200);
        $$("[data-count-requests]").forEach((el) => refreshCounts());
      } catch (err) { fr.classList.remove("is-loading"); if (err.message !== "login") toast(err.message, "error"); }
      return;
    }
    const more = e.target.closest("[data-more]");
    if (more) { more.previousElementSibling?.classList.remove("clamp"); more.remove(); return; }
    const cp = e.target.closest("[data-copy]");
    if (cp) {
      e.preventDefault();
      try { await navigator.clipboard.writeText(cp.dataset.copy); toast("Copied to clipboard", "success", 1800); }
      catch (err) { prompt("Copy this:", cp.dataset.copy); }
      return;
    }
    const sh = e.target.closest("[data-share]");
    if (sh) {
      e.preventDefault();
      const url = new URL(sh.dataset.share, location.origin).href;
      if (navigator.share) { navigator.share({ title: sh.dataset.title || document.title, url }).catch(() => {}); }
      else { try { await navigator.clipboard.writeText(url); toast("Link copied. Paste it anywhere.", "success"); } catch (err) { prompt("Copy this link:", url); } }
      return;
    }
    const mo = e.target.closest("[data-modal-open]");
    if (mo) { e.preventDefault(); openModal(mo.dataset.modalOpen); return; }
    const mc = e.target.closest("[data-modal-close]");
    if (mc || e.target.classList.contains("modal")) { closeModals(); return; }
    const fl = e.target.closest("[data-flip]");
    if (fl) { fl.classList.toggle("flip"); return; }
  });
  function openModal(id) {
    const m = document.getElementById(id);
    if (!m) return;
    m.hidden = false;
    setTimeout(() => m.querySelector("input, textarea, button")?.focus(), 60);
  }
  function closeModals() { $$(".modal").forEach((m) => { m.hidden = true; }); }
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeModals(); closeStory?.(); } });
  window.EF.openModal = openModal;

  // inline comments
  document.addEventListener("submit", async (e) => {
    const f = e.target.closest("form[data-comment]");
    if (!f) return;
    e.preventDefault();
    const input = f.querySelector('input[name="body"]'), text = input.value.trim();
    if (!text) return;
    const btn = f.querySelector("button");
    btn.disabled = true;
    try {
      const r = await api(`/api/posts/${f.dataset.comment}/comments`, { body: text });
      const list = f.closest("[data-post]").querySelector("[data-comments]");
      if (list) {
        const div = document.createElement("div");
        div.className = "cm";
        div.style.animation = "bubble-in .35s var(--spring)";
        div.innerHTML = `<b>${esc(r.username)}</b>${esc(text)}`;
        list.appendChild(div);
      }
      const cnt = f.closest("[data-post]").querySelector("[data-comment-count]");
      if (cnt) cnt.textContent = `View all ${r.count} comments`;
      input.value = "";
    } catch (err) { if (err.message !== "login") toast(err.message, "error"); }
    btn.disabled = false;
  });
  document.addEventListener("input", (e) => {
    const f = e.target.closest("form[data-comment]");
    if (f) f.querySelector("button").disabled = !e.target.value.trim();
  });

  // share to friends
  document.addEventListener("submit", async (e) => {
    const f = e.target.closest("form[data-share-friends]");
    if (!f) return;
    e.preventDefault();
    const to = $$("input[name=to]:checked", f).map((i) => parseInt(i.value, 10));
    if (!to.length) { toast("Pick at least one friend.", "error"); return; }
    try { const r = await api(`/api/share/${f.dataset.shareFriends}`, { to }); toast(`Sent to ${r.sent} friend${r.sent === 1 ? "" : "s"}`, "success"); closeModals(); }
    catch (err) { toast(err.message, "error"); }
  });

  // ------------------------------------------------------------ infinite feed
  const sentinel = $("[data-load-more]");
  if (sentinel && "IntersectionObserver" in window) {
    let busy = false;
    const lo = new IntersectionObserver(async (es) => {
      if (!es[0].isIntersecting || busy) return;
      busy = true;
      const page = sentinel.dataset.loadMore;
      sentinel.innerHTML = `<div class="skeleton" style="height:420px;border-radius:24px"></div>`;
      try {
        const res = await fetch(`/?page=${page}&partial=1`);
        const html = await res.text();
        const tmp = document.createElement("div");
        tmp.innerHTML = html;
        const next = tmp.querySelector("[data-next-page]");
        $$(".post", tmp).forEach((p) => sentinel.before(p));
        carousels(); watch();
        if (next) { sentinel.dataset.loadMore = next.dataset.nextPage; sentinel.innerHTML = ""; busy = false; }
        else { lo.disconnect(); sentinel.innerHTML = `<p class="center muted small" style="padding:20px">You're all caught up ✨</p>`; }
      } catch (err) { sentinel.innerHTML = ""; busy = false; }
    }, { rootMargin: "600px" });
    lo.observe(sentinel);
  }

  // ------------------------------------------------------------ stories
  let closeStory = null;
  const storyData = $("#story-data");
  if (storyData) {
    const stories = JSON.parse(storyData.textContent || "[]");
    let idx = 0, timer = null, raf = null;
    const viewer = document.createElement("div");
    const render = () => {
      const s = stories[idx];
      if (!s) { closeStory(); return; }
      $$(".story")[idx]?.classList.add("seen");
      viewer.innerHTML = `<div class="story-card">
        <div class="bg" style="background-image:${s.banner ? `url('${s.banner}')` : `linear-gradient(135deg, ${s.color}, #0F1424)`}"></div><div class="shade"></div>
        <div class="story-bars">${stories.map((_, k) => `<i><b style="width:${k < idx ? 100 : 0}%"></b></i>`).join("")}</div>
        <div class="story-top"><a class="story-author" href="${s.profile}" aria-label="Open ${esc(s.college)}'s page"><span class="av av-sm" style="--h:${(s.username.length * 47) % 360}">${s.avatar ? `<img src="${s.avatar}" alt="">` : esc(s.college[0])}</span>
          <span><b>${esc(s.college)}</b><small>@${esc(s.username)} · ${esc(s.ago || "")}</small></span></a><button aria-label="Close" data-x>✕</button></div>
        <button class="story-nav prev" aria-label="Previous"></button><button class="story-nav next" aria-label="Next"></button>
        <div class="story-body"><span class="pill dark">${esc(s.category)} · ${s.fee ? (s.fest ? "from ₹" : "₹") + s.fee : "Free"}</span>
          <h2>${esc(s.title)}</h2><p>${esc(s.tagline)}</p><p class="small">📅 ${esc(s.when)}<br>📍 ${esc(s.venue)}</p>
          <a class="btn brand block lg" href="${s.url}">View event</a></div></div>`;
      viewer.querySelector("[data-x]").onclick = closeStory;
      viewer.querySelector(".prev").onclick = () => { idx = Math.max(0, idx - 1); start(); };
      viewer.querySelector(".next").onclick = () => { idx++; start(); };
    };
    const start = () => {
      cancelAnimationFrame(raf); render();
      const bar = viewer.querySelectorAll(".story-bars b")[idx];
      if (!bar) return;
      const t0 = performance.now(), dur = 5200;
      const tick = (t) => { const p = Math.min(1, (t - t0) / dur); bar.style.width = p * 100 + "%"; if (p < 1) raf = requestAnimationFrame(tick); else { idx++; start(); } };
      raf = requestAnimationFrame(tick);
    };
    closeStory = () => { cancelAnimationFrame(raf); viewer.remove(); document.body.style.overflow = ""; };
    viewer.className = "story-viewer";
    viewer.addEventListener("click", (e) => { if (e.target === viewer) closeStory(); });
    $$(".story").forEach((b, k) => b.addEventListener("click", () => { idx = k; document.body.appendChild(viewer); document.body.style.overflow = "hidden"; start(); }));
  }

  // ------------------------------------------------------------ ticket: next step appears by itself after a scan
  const journeyEl = $("[data-journey-poll]");
  if (journeyEl) {
    let sig = journeyEl.dataset.journeySig, busy = false;
    const poll = async () => {
      if (document.hidden || busy) return;
      busy = true;
      try {
        const r = await api(journeyEl.dataset.journeyPoll, undefined, "GET");
        if (r.sig && r.sig !== sig) {
          sig = r.sig;
          toast("✅ Step done! Loading your next step…", "success", 1800);
          if (navigator.vibrate) navigator.vibrate(80);
          setTimeout(() => location.reload(), 900);
        }
      } catch (e) { /* offline: try again next time */ }
      busy = false;
    };
    setInterval(poll, 4000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
  }

  // ------------------------------------------------------------ live counts (notifications, messages, requests)
  async function refreshCounts() {
    if (!loggedIn || document.hidden) return;
    try {
      const r = await api("/api/counts", undefined, "GET");
      $$("[data-count-notifs]").forEach((el) => { el.dataset.n = r.notifications; el.textContent = r.notifications > 99 ? "99+" : r.notifications; });
      $$("[data-count-msgs]").forEach((el) => { el.dataset.n = r.messages; el.textContent = r.messages; });
      $$("[data-count-requests]").forEach((el) => { el.dataset.n = r.requests; el.textContent = r.requests; });
      const total = r.notifications + r.messages;
      document.title = document.title.replace(/^\(\d+\) /, "") ;
      if (total) document.title = `(${total}) ${document.title}`;
    } catch (e) { /* offline */ }
  }
  if (loggedIn) { setInterval(refreshCounts, 20000); document.addEventListener("visibilitychange", refreshCounts); }

  // ------------------------------------------------------------ search typeahead
  $$("[data-search]").forEach((input) => {
    const wrap = input.closest(".searchbar, .search-wrap") || input.parentElement;
    let pop = null, timer = null, hl = -1;
    const close = () => { pop?.remove(); pop = null; hl = -1; };
    input.addEventListener("input", () => {
      clearTimeout(timer);
      const v = input.value.trim();
      if (!v) { close(); return; }
      timer = setTimeout(async () => {
        try {
          const r = await api(`/api/search?q=${encodeURIComponent(v)}`, undefined, "GET");
          if (!pop) { pop = document.createElement("div"); pop.className = "search-pop"; wrap.style.position = "relative"; wrap.appendChild(pop); }
          pop.innerHTML = r.results.length ? r.results.map((x) => `<a href="${x.url}">
              ${x.type === "event" ? `<span class="emo">${x.emoji}</span>` : `<span class="av av-md" style="--h:${(x.title.length * 53) % 360}">${x.avatar ? `<img src="${x.avatar}" alt="">` : esc(x.title[0])}</span>`}
              <span class="grow"><b>${esc(x.title)}${x.verified ? ` <span class="vtick"><svg viewBox="0 0 24 24" fill="none"><path d="m5 12 5 5 9-10"/></svg></span>` : ""}</b>
              <small>${x.type === "college" ? "College · " : x.type === "event" ? "Event · " : ""}${esc(x.sub)}</small></span></a>`).join("")
            : `<p class="muted small" style="padding:12px">No matches. Press Enter to search everything.</p>`;
        } catch (e) { /* ignore */ }
      }, 160);
    });
    input.addEventListener("keydown", (e) => {
      const items = pop ? $$("a", pop) : [];
      if (e.key === "ArrowDown" && items.length) { e.preventDefault(); hl = (hl + 1) % items.length; }
      else if (e.key === "ArrowUp" && items.length) { e.preventDefault(); hl = (hl - 1 + items.length) % items.length; }
      else if (e.key === "Enter" && hl >= 0 && items[hl]) { e.preventDefault(); location.href = items[hl].href; return; }
      else return;
      items.forEach((a, k) => a.classList.toggle("hl", k === hl));
    });
    document.addEventListener("click", (e) => { if (!wrap.contains(e.target)) close(); });
  });
  document.addEventListener("keydown", (e) => {
    if ((e.key === "/" || (e.key === "k" && (e.metaKey || e.ctrlKey))) && !/input|textarea|select/i.test(document.activeElement.tagName)) {
      e.preventDefault();
      const s = $("[data-search]");
      if (s) s.focus(); else location.href = "/explore";
    }
  });

  // ------------------------------------------------------------ chat thread (polling)
  const thread = $("[data-thread]");
  if (thread) {
    const body = $(".thread-body", thread), form = $(".thread-form", thread), user = thread.dataset.thread;
    let last = parseInt(thread.dataset.last || "0", 10);
    body.scrollTop = body.scrollHeight;
    const add = (m) => {
      if (m.id <= last) return;
      last = m.id;
      body.querySelector(".chat-hello")?.remove();
      const d = document.createElement("div");
      d.className = "bubble " + (m.mine ? "me" : "them");
      d.innerHTML = `${m.body ? esc(m.body) : ""}${m.event_url ? `<a class="ev" href="${m.event_url}">🎟️ ${esc(m.event_title)}</a>` : ""}<time>${m.time}</time>`;
      body.appendChild(d);
      body.scrollTop = body.scrollHeight;
    };
    const poll = async (force) => {
      if (document.hidden && !force) return;
      try { const r = await api(`/api/messages/${user}?after=${last}`, undefined, "GET"); r.messages.forEach(add); } catch (e) { /* ignore */ }
    };
    setInterval(poll, 3500);
    form?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = form.querySelector('input[name="body"]'), btn = form.querySelector("button"), text = input.value.trim();
      if (!text || form._busy) return;
      form._busy = true; btn.disabled = true;
      input.value = "";
      try { await api(`/api/messages/${user}`, { body: text }); await poll(true); }
      catch (err) { toast(err.message, "error"); input.value = text; }
      finally { form._busy = false; btn.disabled = false; input.focus(); }
    });
  }

  // ------------------------------------------------------------ forms: password, strength, dropzone, char count
  $$("[data-pw-toggle]").forEach((b) => b.addEventListener("click", () => {
    const i = b.parentElement.querySelector("input");
    i.type = i.type === "password" ? "text" : "password";
    b.textContent = i.type === "password" ? "Show" : "Hide";
  }));
  $$("[data-strength]").forEach((inp) => {
    const bar = document.querySelector(inp.dataset.strength + " i");
    inp.addEventListener("input", () => {
      const v = inp.value;
      let s = 0;
      if (v.length >= 8) s++; if (/[A-Z]/.test(v) && /[a-z]/.test(v)) s++; if (/\d/.test(v)) s++; if (/[^A-Za-z0-9]/.test(v)) s++; if (v.length >= 12) s++;
      bar.style.width = Math.min(100, s * 20) + "%";
      bar.style.background = ["#F0405E", "#F0405E", "#F5A524", "#F5A524", "#0E9F6E", "#0E9F6E"][s];
    });
  });
  $$("[data-dropzone]").forEach((dz) => {
    const input = dz.querySelector("input[type=file]"), out = document.querySelector(dz.dataset.dropzone);
    const show = () => {
      out.innerHTML = "";
      Array.from(input.files).forEach((f) => {
        const pv = document.createElement("div");
        pv.className = "pv";
        const url = URL.createObjectURL(f);
        if (f.type.startsWith("image/")) pv.innerHTML = `<img src="${url}" alt="">`;
        else if (f.type.startsWith("video/")) pv.innerHTML = `<video src="${url}" muted autoplay loop playsinline></video><b>Video</b>`;
        else pv.innerHTML = `<span>📄<br>${esc(f.name)}</span>`;
        out.appendChild(pv);
      });
    };
    ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("over"); }));
    ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, () => dz.classList.remove("over")));
    dz.addEventListener("drop", (e) => { e.preventDefault(); if (e.dataTransfer.files.length) { input.files = e.dataTransfer.files; show(); } });
    input.addEventListener("change", show);
  });
  $$("[data-autosubmit-name]").forEach((input) => input.addEventListener("change", () => {
    const f = input.files && input.files[0], label = input.closest("label");
    if (f && label) label.dataset.file = "✓ " + f.name.slice(0, 28) + " · save to apply";
  }));
  $$("[data-preview]").forEach((input) => input.addEventListener("change", () => {
    const img = document.querySelector(input.dataset.preview);
    if (img && input.files[0]) { img.src = URL.createObjectURL(input.files[0]); img.hidden = false; }
  }));
  $$("[data-maxlen]").forEach((el) => {
    const out = document.querySelector(el.dataset.maxlen);
    const upd = () => { out.textContent = `${el.value.length}/${el.maxLength}`; };
    el.addEventListener("input", upd); upd();
  });

  // ------------------------------------------------------------ theme quick toggle
  $$("[data-theme-toggle]").forEach((b) => b.addEventListener("click", async () => {
    const root = document.documentElement;
    const cur = root.dataset.theme === "dark" || (root.dataset.theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches) ? "dark" : "light";
    const next = cur === "dark" ? "light" : "dark";
    const apply = () => { root.dataset.theme = next; };
    if (document.startViewTransition && !reduced) document.startViewTransition(apply); else apply();
    if (loggedIn) {
      const fd = new FormData(); fd.append("theme", next); fd.append("csrf_token", csrf);
      fetch("/settings/appearance", { method: "POST", body: fd, redirect: "manual" }).catch(() => {});
    } else { try { localStorage.setItem("ef-theme", next); } catch (e) { /* ignore */ } }
  }));

  // ------------------------------------------------------------ parallax hero
  const hero = $(".ev-hero .bg");
  if (hero && !reduced) addEventListener("scroll", () => { hero.style.transform = `scale(1.08) translateY(${Math.min(scrollY, 500) * 0.25}px)`; }, { passive: true });

  // ------------------------------------------------------------ confetti
  window.EF.confetti = function () {
    if (reduced) return;
    const c = document.createElement("canvas");
    c.id = "confetti"; document.body.appendChild(c);
    const ctx = c.getContext("2d"), W = (c.width = innerWidth), H = (c.height = innerHeight);
    const colors = ["#F5A524", "#F0405E", "#7C5CFF", "#22D3EE", "#0E9F6E", "#FFFFFF"];
    const parts = Array.from({ length: 160 }, () => ({ x: W / 2, y: H * 0.35, vx: (Math.random() - 0.5) * 16, vy: Math.random() * -16 - 4,
      s: 5 + Math.random() * 6, r: Math.random() * 6, vr: (Math.random() - 0.5) * 0.3, c: colors[(Math.random() * colors.length) | 0] }));
    let f = 0;
    const loop = () => {
      ctx.clearRect(0, 0, W, H);
      parts.forEach((p) => { p.vy += 0.42; p.vx *= 0.99; p.x += p.vx; p.y += p.vy; p.r += p.vr;
        ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.r); ctx.fillStyle = p.c; ctx.fillRect(-p.s / 2, -p.s / 4, p.s, p.s / 2); ctx.restore(); });
      if (++f < 200) requestAnimationFrame(loop); else c.remove();
    };
    loop();
  };
  if ($("[data-confetti]")) setTimeout(window.EF.confetti, 350);

  // ------------------------------------------------------------ AI assistant
  const fab = $(".ask-fab"), panel = $(".ask-panel");
  if (fab && panel) {
    const log = $(".ask-log", panel), form = $(".ask-form", panel), input = $("input", form), sugg = $(".ask-sugg", panel);
    const history = [];
    let busy = false;
    const open = () => { panel.hidden = false; fab.hidden = true; setTimeout(() => input.focus(), 80); };
    const close = () => { panel.hidden = true; fab.hidden = false; };
    fab.addEventListener("click", open);
    $(".ask-close", panel).addEventListener("click", close);
    $$("[data-ask]").forEach((b) => b.addEventListener("click", (e) => { e.preventDefault(); open(); if (b.dataset.ask) send(b.dataset.ask); }));
    const bubble = (cls, html) => { const d = document.createElement("div"); d.className = "bubble " + cls; d.innerHTML = html; log.appendChild(d); log.scrollTop = log.scrollHeight; return d; };
    async function send(q) {
      q = (q || "").trim();
      if (!q || busy) return;
      busy = true;
      if (sugg) sugg.hidden = true;
      bubble("me", esc(q));
      input.value = "";
      const t = bubble("them typing", "<span></span><span></span><span></span>");
      try {
        const r = await api("/api/ask", { question: q, history, event_id: document.body.dataset.event || null });
        t.remove();
        bubble("them", fmt(r.answer) + `<span class="src">${r.source === "llm" ? "Claude · live EventFlow data" : "Live EventFlow data"}${r.answered === false ? " · sent to organisers" : ""}</span>`);
        history.push({ role: "user", content: q }, { role: "assistant", content: r.answer });
      } catch (err) { t.remove(); bubble("them", "I couldn't reach the server. Try again in a moment."); }
      busy = false;
    }
    form.addEventListener("submit", (e) => { e.preventDefault(); send(input.value); });
    sugg?.addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) send(b.textContent); });
  }
})();
