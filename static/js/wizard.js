/* EventFlow · create / edit wizard: one step at a time, the events inside a fest, time slots and the event-day order.
   Everything typed lives in `S`; before submitting it's written to three hidden JSON fields the server reads. */
(() => {
  const form = document.querySelector("form.wizard");
  if (!form) return;
  const $ = (s, el = form) => el.querySelector(s);
  const $$ = (s, el = form) => Array.from(el.querySelectorAll(s));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const CFG = window.EF_WIZ || { categories: [], upi: true };
  const editing = form.dataset.mode === "edit";
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let kind = form.dataset.kind === "fest" ? "fest" : "event";
  let seq = 0;
  const uid = (p) => `${p}${Date.now().toString(36).slice(-4)}${(seq++).toString(36)}`;
  const parse = (v, d) => { try { const x = JSON.parse(v); return x && typeof x === typeof d ? x : d; } catch (e) { return d; } };
  const pad = (n) => String(n).padStart(2, "0");
  const fmtLocal = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const toDate = (s) => { if (!s) return null; const d = new Date(s); return isNaN(d) ? null : d; };
  const tm = (s) => { const d = toDate(s); return d ? d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : ""; };
  const val = (n) => (form.elements[n] && form.elements[n].value ? String(form.elements[n].value) : "").trim();
  const CULTURAL = /dance|sing|music|drama|ramp|fashion|art|tune|treasure|squid|connexion|ball/i;
  const defaultCat = (title) => (/gam(e|ing)|bgmi|free fire|valorant/i.test(title) ? "Gaming" : CULTURAL.test(title) ? "Cultural"
    : /cricket|football|volley|kabaddi|kho|throw|chess|carrom|sport/i.test(title) ? "Sports" : "Technical");
  const blankEvent = (title = "") => ({ key: uid("e"), title, category: defaultCat(title), fee: 0, fee_type: "person", team_size: 1, capacity: 100, label: "" });

  // ------------------------------------------------------------------ state (restored after a server-side error)
  const festIn = parse($("[name=fest_json]").value, {}), slotsIn = parse($("[name=slots_json]").value, {});
  const flowIn = parse($("[name=flow_json]").value, []);
  const S = {
    tracks: Array.isArray(festIn.tracks) && festIn.tracks.length ? festIn.tracks
      : [{ key: uid("t"), name: "Events", emoji: "✨", pricing: "event", pass_fee: 0, events: [] }],
    rounds: Array.isArray(slotsIn.rounds) ? slotsIn.rounds : [],
    use: slotsIn.use && typeof slotsIn.use === "object" && !Array.isArray(slotsIn.use) ? slotsIn.use : {},
    flow: Array.isArray(flowIn) ? flowIn.filter((x) => x && (x.type === "event" || x.type === "food")) : [],
  };
  S.many = S.rounds.length > 0;
  S.tracks.forEach((t) => {
    t.key = t.key || uid("t");
    t.events = (Array.isArray(t.events) ? t.events : []).map((ev) => ({ ...blankEvent(), ...ev, key: ev.key || uid("e") }));
  });
  S.rounds.forEach((r) => { r.key = r.key || uid("r"); });
  S.flow.forEach((x) => { if (x.type === "food") x.ref = x.ref || uid("f"); });
  const ensureOneEvent = () => { if (kind === "fest" && !S.tracks.some((t) => t.events.length)) S.tracks[0].events.push(blankEvent()); };
  ensureOneEvent();

  // ------------------------------------------------------------------ steps
  const steps = $$(".wiz-step");
  const visible = () => steps.filter((s) => !s.dataset.only || s.dataset.only === kind);
  const bar = document.querySelector("[data-wiz-bar]"), dots = document.querySelector("[data-wiz-dots]");
  const errBox = $("[data-wiz-err]");
  let idx = 0, maxSeen = 0;

  function renderDots() {
    const v = visible();
    dots.innerHTML = v.map((s, i) => `<li class="${i < idx ? "done" : ""}${i === idx ? " on" : ""}${i <= maxSeen ? " reach" : ""}" data-go="${i}">
      <span>${i < idx ? "✓" : i + 1}</span><b>${esc(s.dataset.label)}</b></li>`).join("");
    if (bar) bar.style.width = `${((idx + 1) / v.length) * 100}%`;
    $("[data-wiz-count]").textContent = `Step ${idx + 1} of ${v.length}`;
    const last = idx === v.length - 1;
    $("[data-wiz-next]").hidden = last;
    $("[data-wiz-submit]").hidden = !last;
    $("[data-wiz-back]").style.visibility = idx === 0 ? "hidden" : "visible";
  }
  function showErr(list) {
    errBox.innerHTML = list.map((m) => `<span>⚠️ ${esc(m)}</span>`).join("");
    errBox.hidden = false;
    errBox.classList.remove("shake"); void errBox.offsetWidth; errBox.classList.add("shake");
    errBox.scrollIntoView({ block: "center", behavior: reduced ? "auto" : "smooth" });
  }
  const hideErr = () => { errBox.hidden = true; };
  function show(to, dir = 1) {
    const v = visible();
    to = Math.max(0, Math.min(v.length - 1, to));
    const from = v.includes(steps.find((s) => !s.hidden)) ? steps.find((s) => !s.hidden) : null;
    const next = v[to];
    steps.forEach((s) => { if (s !== from && s !== next) { s.hidden = true; s.classList.remove("enter-l", "enter-r", "leave-l", "leave-r"); } });
    if (from && from !== next) {
      if (reduced) from.hidden = true;
      else {
        from.classList.add(dir > 0 ? "leave-l" : "leave-r");
        setTimeout(() => { from.hidden = true; from.classList.remove("leave-l", "leave-r"); }, 280);
      }
    }
    next.hidden = false;
    next.classList.remove("enter-l", "enter-r", "leave-l", "leave-r");
    if (!reduced && from !== next) { void next.offsetWidth; next.classList.add(dir > 0 ? "enter-r" : "enter-l"); }
    idx = to;
    maxSeen = Math.max(maxSeen, idx);
    enter(next.dataset.step);
    renderDots();
    hideErr();
    window.scrollTo({ top: 0, behavior: reduced ? "auto" : "smooth" });
  }

  function validate(step) {
    const errs = [];
    if (step === "basics" && !val("title")) errs.push(`Give the ${kind === "fest" ? "fest" : "event"} a name.`);
    if (step === "when") {
      const s = val("start_dt"), e = val("end_dt"), d = val("reg_deadline"), m = val("map_url");
      if (!s || !e) errs.push("Add when it starts and when it ends.");
      else if (e <= s) errs.push("It must end after it starts.");
      if (!val("venue")) errs.push("Add a venue.");
      if (m && !/^https?:\/\//i.test(m)) errs.push("The map link must start with https://");
      if (d && e && d > e) errs.push("Registration should close before it ends.");
    }
    if (step === "tickets" && !(+val("capacity") >= 1)) errs.push("Seats must be at least 1.");
    if (step === "events") {
      const all = S.tracks.flatMap((t) => t.events.filter((ev) => ev.title.trim()));
      if (!all.length) errs.push("Add at least one event to the fest.");
      const seen = new Set();
      all.forEach((ev) => { const k = ev.title.trim().toLowerCase(); if (seen.has(k)) errs.push(`Two events are called “${ev.title.trim()}”.`); seen.add(k); });
      const paid = S.tracks.some((t) => (t.pricing === "pass" && +t.pass_fee > 0) || (t.pricing !== "pass" && t.events.some((ev) => ev.title.trim() && +ev.fee > 0)));
      if (paid && !CFG.upi) errs.push("Add your UPI ID in Settings → Payments before adding paid events (or set the fees to 0 for now).");
    }
    if (step === "slots" && (kind === "fest" || S.many)) {
      S.rounds.forEach((r, i) => {
        const name = r.label || `Slot ${i + 1}`;
        if (!r.start || !r.end) errs.push(`${name} needs a start and an end.`);
        else if (r.end <= r.start) errs.push(`${name} must end after it starts.`);
      });
      if (kind !== "fest" && S.many && !S.rounds.length) errs.push("Make at least one slot, or pick “One slot”.");
    }
    if (step === "flow") S.flow.filter((x) => x.type === "food").forEach((f) => {
      if (f.start && f.end && f.end <= f.start) errs.push(`${f.title || "A meal"} must end after it starts.`);
    });
    return [...new Set(errs)];
  }
  const firstBad = () => {
    const v = visible();
    for (let i = 0; i < v.length; i++) { const errs = validate(v[i].dataset.step); if (errs.length) return [i, errs]; }
    return null;
  };

  $("[data-wiz-next]").addEventListener("click", () => {
    const errs = validate(visible()[idx].dataset.step);
    if (errs.length) { showErr(errs); return; }
    show(idx + 1, 1);
  });
  $("[data-wiz-back]").addEventListener("click", () => show(idx - 1, -1));
  dots.addEventListener("click", (e) => {
    const li = e.target.closest("[data-go]");
    if (!li) return;
    const to = +li.dataset.go;
    if (to <= idx) { show(to, -1); return; }
    for (let i = idx; i < to; i++) {
      const errs = validate(visible()[i].dataset.step);
      if (errs.length) { if (i !== idx) show(i, 1); showErr(errs); return; }
    }
    show(to, 1);
  });
  form.addEventListener("keydown", (e) => {   // Enter moves on instead of submitting half-way through
    if (e.key !== "Enter" || e.target.tagName === "TEXTAREA" || e.target.type === "submit") return;
    e.preventDefault();
    if (!$("[data-wiz-next]").hidden) $("[data-wiz-next]").click();
  });
  form.addEventListener("submit", (e) => {
    serialize();
    const bad = firstBad();
    if (bad) { e.preventDefault(); if (bad[0] !== idx) show(bad[0], bad[0] < idx ? -1 : 1); showErr(bad[1]); }
  });

  // ------------------------------------------------------------------ kind (single event / fest)
  const syncKind = () => {
    form.dataset.kind = kind;
    $$("[data-fest-word]").forEach((el) => { el.textContent = kind === "fest" ? "Fest" : "Event"; });
    const t = form.elements.title;
    if (t) t.placeholder = kind === "fest" ? "N-CORE 2026" : "HackArunai 24H";
    if (kind === "fest") { const c = form.querySelector("input[name=category][value=Fest]"); if (c && !editing) c.checked = true; }
    ensureOneEvent();
    renderDots();
  };
  $$("input[name=kind]").forEach((r) => r.addEventListener("change", () => {
    kind = r.value === "fest" ? "fest" : "event";
    if (kind === "event") { const c = form.querySelector("input[name=category]:checked"); if (c && c.value === "Fest") form.querySelector("input[name=category][value=Technical]").checked = true; }
    syncKind();
  }));
  const heading = document.querySelector("[data-wiz-heading]");
  form.elements.title?.addEventListener("input", () => { if (heading && !editing) heading.textContent = val("title") || "Let's set it up"; });

  // ------------------------------------------------------------------ events inside the fest
  const fb = $("[data-fest-builder]");
  let lastTrack = 0;
  function evRow(t, ev, ei) {
    const cats = CFG.categories.filter((c) => c !== "Fest");
    const fee = t.pricing === "pass" ? `<span class="fb-in-pass" title="Covered by the group's pass">🎫 In pass</span>`
      : `<label class="fb-fee" title="Fee">₹<input type="number" min="0" inputmode="numeric" data-e-f="fee" value="${+ev.fee || 0}" aria-label="Fee for ${esc(ev.title || "this event")}"></label>
         <select class="fb-ftype" data-e-f="fee_type" aria-label="Fee is" ${+ev.team_size > 1 ? "" : "disabled"}><option value="person" ${ev.fee_type !== "team" ? "selected" : ""}>per person</option><option value="team" ${ev.fee_type === "team" ? "selected" : ""}>per team</option></select>`;
    return `<div class="fb-ev" data-e="${ei}">
      <input class="fb-title" data-e-f="title" value="${esc(ev.title)}" maxlength="120" placeholder="Event name" aria-label="Event name">
      <select class="fb-cat" data-e-f="category" aria-label="Type">${cats.map((c) => `<option ${c === ev.category ? "selected" : ""}>${c}</option>`).join("")}</select>
      ${fee}
      <label class="fb-num fb-team" title="Team size">👥<select data-e-f="team_size" aria-label="Team size">${Array.from({ length: 15 }, (_, i) => `<option value="${i + 1}" ${+ev.team_size === i + 1 ? "selected" : ""}>${i ? "up to " + (i + 1) : "solo"}</option>`).join("")}</select></label>
      <label class="fb-num fb-seats" title="Seats">🎟️<input type="number" min="1" inputmode="numeric" data-e-f="capacity" value="${+ev.capacity || 100}" aria-label="Seats"></label>
      <input class="fb-tag" data-e-f="label" value="${esc(ev.label || "")}" maxlength="40" placeholder="Tag, e.g. Boys only" aria-label="Tag">
      <button type="button" class="iconbtn fb-del" data-del-event aria-label="Remove ${esc(ev.title || "event")}">✕</button>
    </div>`;
  }
  function renderFest(focusNew) {
    if (!fb) return;
    fb.innerHTML = S.tracks.map((t, ti) => `
      <div class="fb-track" data-t="${ti}">
        <div class="fb-head">
          <input class="fb-emoji" data-f="emoji" value="${esc(t.emoji)}" maxlength="4" aria-label="Group emoji">
          <input class="fb-name" data-f="name" value="${esc(t.name)}" maxlength="60" placeholder="Group, e.g. Technical events" aria-label="Group name">
          <div class="seg sm"><label><input type="radio" name="pr-${t.key}" value="event" data-f="pricing" ${t.pricing !== "pass" ? "checked" : ""}>Fee per event</label><label><input type="radio" name="pr-${t.key}" value="pass" data-f="pricing" ${t.pricing === "pass" ? "checked" : ""}>One pass for all</label></div>
          ${t.pricing === "pass" ? `<label class="fb-pass">Pass ₹<input type="number" min="0" inputmode="numeric" data-f="pass_fee" value="${+t.pass_fee || 0}" aria-label="Pass fee"></label>` : ""}
          ${S.tracks.length > 1 ? `<button type="button" class="iconbtn" data-del-track aria-label="Remove group ${esc(t.name)}">✕</button>` : ""}
        </div>
        <div class="fb-events">${t.events.map((ev, ei) => evRow(t, ev, ei)).join("") || `<p class="muted small fb-empty">No events in this group yet.</p>`}</div>
        <button type="button" class="btn soft sm" data-add-event>＋ Add an event</button>
      </div>`).join("");
    if (focusNew) {
      const t = fb.querySelector(`.fb-track[data-t="${focusNew[0]}"] .fb-ev[data-e="${focusNew[1]}"]`);
      if (t) { t.classList.add("pop"); if (focusNew[2]) t.querySelector(".fb-title")?.focus(); }
    }
    serialize();
  }
  if (fb) {
    const where = (el) => { const tr = el.closest("[data-t]"), ev = el.closest("[data-e]"); return [tr ? +tr.dataset.t : null, ev ? +ev.dataset.e : null]; };
    fb.addEventListener("input", (e) => {
      const [ti, ei] = where(e.target);
      if (ti === null) return;
      lastTrack = ti;
      const t = S.tracks[ti];
      if (e.target.dataset.f && e.target.type !== "radio") t[e.target.dataset.f] = e.target.value;
      if (e.target.dataset.eF && ei !== null && e.target.tagName !== "SELECT") t.events[ei][e.target.dataset.eF] = e.target.value;
      serialize();
    });
    fb.addEventListener("change", (e) => {
      const [ti, ei] = where(e.target);
      if (ti === null) return;
      const t = S.tracks[ti];
      if (e.target.dataset.f === "pricing") { t.pricing = e.target.value; renderFest(); return; }
      if (e.target.dataset.eF && ei !== null && e.target.tagName === "SELECT") {
        const ev = t.events[ei];
        ev[e.target.dataset.eF] = e.target.value;
        if (e.target.dataset.eF === "team_size" && +e.target.value <= 1) ev.fee_type = "person";
        if (e.target.dataset.eF === "team_size") renderFest();
      }
      if (e.target.dataset.eF === "title" && ei !== null) {     // a name hints at the type when it's still the default
        const ev = t.events[ei];
        if (ev.category === "Technical") { ev.category = defaultCat(ev.title); const sel = e.target.closest(".fb-ev").querySelector(".fb-cat"); if (sel) sel.value = ev.category; }
      }
      serialize();
    });
    fb.addEventListener("focusin", (e) => { const [ti] = where(e.target); if (ti !== null) lastTrack = ti; });
    fb.addEventListener("click", (e) => {
      const [ti, ei] = where(e.target);
      if (e.target.closest("[data-add-event]")) {
        S.tracks[ti].events.push(blankEvent());
        lastTrack = ti;
        renderFest([ti, S.tracks[ti].events.length - 1, true]);
      } else if (e.target.closest("[data-del-event]")) {
        S.tracks[ti].events.splice(ei, 1);
        renderFest();
      } else if (e.target.closest("[data-del-track]")) {
        const t = S.tracks[ti];
        if (t.events.some((x) => x.title.trim()) && !confirm(`Remove the group “${t.name}” and its events?`)) return;
        S.tracks.splice(ti, 1);
        lastTrack = 0;
        renderFest();
      }
    });
  }
  document.querySelector("[data-add-track]")?.addEventListener("click", () => {
    S.tracks.push({ key: uid("t"), name: "", emoji: "🎭", pricing: "event", pass_fee: 0, events: [blankEvent()] });
    lastTrack = S.tracks.length - 1;
    renderFest();
    fb.querySelector(`.fb-track[data-t="${lastTrack}"] .fb-name`)?.focus();
  });
  $$("[data-quick]").forEach((b) => b.addEventListener("click", () => {
    const ti = Math.min(lastTrack, S.tracks.length - 1), t = S.tracks[ti], name = b.dataset.quick;
    if (S.tracks.some((x) => x.events.some((ev) => ev.title.trim().toLowerCase() === name.toLowerCase()))) {
      b.classList.remove("nope"); void b.offsetWidth; b.classList.add("nope"); return;
    }
    const blank = t.events.findIndex((ev) => !ev.title.trim());
    if (blank >= 0) { t.events[blank].title = name; t.events[blank].category = defaultCat(name); renderFest([ti, blank]); }
    else { t.events.push(blankEvent(name)); renderFest([ti, t.events.length - 1]); }
    if (/hackathon/i.test(name)) { const ev = t.events.find((x) => x.title === name); if (ev && +ev.team_size === 1) { ev.team_size = 4; renderFest(); } }
  }));

  // ------------------------------------------------------------------ time slots
  const sb = $("[data-slot-builder]");
  const evList = () => (kind === "fest"
    ? S.tracks.flatMap((t) => t.events.filter((ev) => ev.title.trim()).map((ev) => ({ key: ev.key, title: ev.title.trim(), capacity: +ev.capacity || 100 })))
    : [{ key: "self", title: val("title") || "Your event", capacity: +val("capacity") || 100 }]);
  function useOf(k) {
    if (!S.use[k]) S.use[k] = { rounds: S.rounds.map((r) => r.key), capacity: 20 };
    S.use[k].rounds = (S.use[k].rounds || []).filter((x) => S.rounds.some((r) => r.key === x));
    return S.use[k];
  }
  const genStart = () => (S.rounds.length ? S.rounds[S.rounds.length - 1].end : val("start_dt")) || "";
  function makeRounds(start, minutes, gap, count) {
    const d0 = toDate(start);
    if (!d0) return false;
    S.rounds = [];
    let t = d0.getTime();
    for (let i = 0; i < count; i++) {
      S.rounds.push({ key: uid("r"), label: "", start: fmtLocal(new Date(t)), end: fmtLocal(new Date(t + minutes * 60000)), venue: "" });
      t += (minutes + gap) * 60000;
    }
    evList().forEach((ev) => {
      const u = useOf(ev.key);
      u.rounds = S.rounds.map((r) => r.key);
      if (kind !== "fest") u.capacity = Math.max(1, Math.ceil(ev.capacity / count));
    });
    return true;
  }
  function capText(ev) {
    const u = useOf(ev.key), places = u.rounds.length * (+u.capacity || 0);
    if (!u.rounds.length) return ["muted", "One slot for its whole time"];
    return [places < ev.capacity ? "warn-text" : "muted", `${u.rounds.length} slot${u.rounds.length === 1 ? "" : "s"} × ${+u.capacity || 0} = ${places} places for ${ev.capacity} seats${places < ev.capacity ? ": add slots or raise members per slot" : ""}`];
  }
  function renderSlots() {
    if (!sb) return;
    const evs = evList();
    const intro = document.querySelector("[data-slots-intro]");
    if (intro) intro.textContent = kind === "fest"
      ? "Split the day into slots. Tick which slots each event runs in and how many members each slot holds. Participants are then given a slot automatically: every event's first slot fills first, and nobody's slots ever clash. Leave it empty and each event is one slot for its whole time."
      : "Run it all at once, or in turns: several time slots, each holding a set number of members. Participants are given a slot automatically when their ticket is confirmed.";
    const many = kind === "fest" || S.many;
    let html = "";
    if (kind !== "fest") html += `<div class="kind-pick"><label class="kind-tile"><input type="radio" name="slotmode" value="one" ${S.many ? "" : "checked"}><span class="kt-emoji">🕘</span><b>One slot</b><small>Everyone at the event's own time.</small></label>
      <label class="kind-tile"><input type="radio" name="slotmode" value="many" ${S.many ? "checked" : ""}><span class="kt-emoji">⏱️</span><b>Several time slots</b><small>People come in turns, e.g. 20 every hour.</small></label></div>`;
    if (many) {
      html += `<div class="gen-row">
        <div class="gen-fields">
          <label class="field"><span>First slot starts</span><input type="datetime-local" data-g="start" value="${esc(S.rounds.length ? S.rounds[0].start : val("start_dt"))}"></label>
          <label class="field"><span>Minutes each</span><input type="number" min="5" max="1440" data-g="minutes" value="${S.rounds.length ? Math.max(5, Math.round((toDate(S.rounds[0].end) - toDate(S.rounds[0].start)) / 60000) || 60) : 60}"></label>
          <label class="field"><span>Break between</span><input type="number" min="0" max="600" data-g="gap" value="0"></label>
          <label class="field"><span>How many slots</span><input type="number" min="1" max="48" data-g="count" value="${S.rounds.length || 4}"></label>
        </div>
        <button type="button" class="btn brand sm" data-gen>⚡ ${S.rounds.length ? "Remake the slots" : "Make the slots"}</button>
      </div>`;
      html += `<div class="round-list">${S.rounds.map((r, i) => `<div class="round" data-r="${i}"><span class="r-n">${i + 1}</span>
          <input class="r-label" data-r-f="label" value="${esc(r.label || "")}" placeholder="Slot ${i + 1}" maxlength="40" aria-label="Slot name">
          <input type="datetime-local" data-r-f="start" value="${esc(r.start || "")}" aria-label="Slot ${i + 1} starts">
          <input type="datetime-local" data-r-f="end" value="${esc(r.end || "")}" aria-label="Slot ${i + 1} ends">
          <input class="r-venue" data-r-f="venue" value="${esc(r.venue || "")}" placeholder="Room (optional)" maxlength="80" aria-label="Room">
          <button type="button" class="iconbtn" data-del-round aria-label="Remove slot ${i + 1}">✕</button></div>`).join("")}</div>`;
      html += `<button type="button" class="btn ghost sm" data-add-round>＋ Add a slot</button>`;
      if (S.rounds.length && kind !== "fest") {
        const u = useOf("self"), [cls, txt] = capText(evs[0]);
        html += `<div class="cap-row"><label class="field"><span>Members per slot</span><input type="number" min="1" inputmode="numeric" data-cap="self" value="${+u.capacity || 20}"></label><p class="small ${cls}" data-cap-text="self">${esc(txt)}</p></div>`;
      }
      if (S.rounds.length && kind === "fest") {
        html += evs.length ? `<div class="matrix-wrap"><table class="matrix"><thead><tr><th>Event</th>${S.rounds.map((r, i) => `<th><b>${esc(r.label || "Slot " + (i + 1))}</b><small>${esc(tm(r.start))}</small></th>`).join("")}<th>Members / slot</th></tr></thead>
          <tbody>${evs.map((ev) => { const u = useOf(ev.key), [cls, txt] = capText(ev); return `<tr data-ev="${esc(ev.key)}"><td><b>${esc(ev.title)}</b><small class="${cls}" data-cap-text="${esc(ev.key)}">${esc(txt)}</small></td>
            ${S.rounds.map((r) => `<td><label class="tick-cell"><input type="checkbox" data-pick="${esc(r.key)}" ${u.rounds.includes(r.key) ? "checked" : ""} aria-label="${esc(ev.title)} in ${esc(r.label || "this slot")}"><i></i></label></td>`).join("")}
            <td><input type="number" min="1" inputmode="numeric" data-cap="${esc(ev.key)}" value="${+u.capacity || 20}" aria-label="Members per slot for ${esc(ev.title)}"></td></tr>`; }).join("")}</tbody></table></div>
          <div class="row"><button type="button" class="btn ghost sm" data-all-rounds>Every event in every slot</button><button type="button" class="btn ghost sm" data-no-rounds>Clear the ticks</button></div>`
          : `<p class="muted">Add events in the step before first.</p>`;
      }
    }
    sb.innerHTML = html;
    serialize();
  }
  function refreshCaps() {
    evList().forEach((ev) => {
      const el = sb.querySelector(`[data-cap-text="${CSS.escape(ev.key)}"]`);
      if (!el) return;
      const [cls, txt] = capText(ev);
      el.className = "small " + cls;
      el.textContent = txt;
    });
  }
  if (sb) {
    sb.addEventListener("change", (e) => {
      const t = e.target;
      if (t.name === "slotmode") {
        S.many = t.value === "many";
        if (S.many && !S.rounds.length) {
          const s = toDate(val("start_dt")), en = toDate(val("end_dt"));
          const hours = s && en ? Math.max(1, Math.min(6, Math.round((en - s) / 3600000))) : 4;
          makeRounds(val("start_dt"), 60, 0, hours);
        }
        renderSlots();
        return;
      }
      if (t.dataset.pick) {
        const ev = t.closest("[data-ev]").dataset.ev, u = useOf(ev);
        u.rounds = t.checked ? [...new Set([...u.rounds, t.dataset.pick])] : u.rounds.filter((x) => x !== t.dataset.pick);
        u.rounds.sort((a, b) => S.rounds.findIndex((r) => r.key === a) - S.rounds.findIndex((r) => r.key === b));
        refreshCaps(); serialize();
      }
      if (t.dataset.rF) { renderSlotsSoon(); }
    });
    sb.addEventListener("input", (e) => {
      const t = e.target;
      if (t.dataset.rF) { const i = +t.closest("[data-r]").dataset.r; S.rounds[i][t.dataset.rF] = t.value; serialize(); }
      if (t.dataset.cap) { useOf(t.dataset.cap).capacity = Math.max(1, +t.value || 1); refreshCaps(); serialize(); }
    });
    sb.addEventListener("click", (e) => {
      if (e.target.closest("[data-gen]")) {
        const g = (n) => sb.querySelector(`[data-g="${n}"]`).value;
        const minutes = Math.max(5, +g("minutes") || 60), gap = Math.max(0, +g("gap") || 0), count = Math.min(48, Math.max(1, +g("count") || 1));
        if (S.rounds.length && !confirm(`Replace the ${S.rounds.length} slots with ${count} new ones?`)) return;
        if (!makeRounds(g("start"), minutes, gap, count)) { showErr(["Pick when the first slot starts."]); return; }
        renderSlots();
      } else if (e.target.closest("[data-add-round]")) {
        const last = S.rounds[S.rounds.length - 1];
        const s = toDate(last ? last.end : val("start_dt")) || new Date();
        const len = last ? (toDate(last.end) - toDate(last.start)) || 3600000 : 3600000;
        const r = { key: uid("r"), label: "", start: fmtLocal(s), end: fmtLocal(new Date(s.getTime() + len)), venue: "" };
        S.rounds.push(r);
        evList().forEach((ev) => { const u = useOf(ev.key); if (u.rounds.length === S.rounds.length - 1) u.rounds.push(r.key); });
        renderSlots();
      } else if (e.target.closest("[data-del-round]")) {
        S.rounds.splice(+e.target.closest("[data-r]").dataset.r, 1);
        if (kind !== "fest" && !S.rounds.length) S.many = false;
        renderSlots();
      } else if (e.target.closest("[data-all-rounds]")) {
        evList().forEach((ev) => { useOf(ev.key).rounds = S.rounds.map((r) => r.key); });
        renderSlots();
      } else if (e.target.closest("[data-no-rounds]")) {
        evList().forEach((ev) => { useOf(ev.key).rounds = []; });
        renderSlots();
      }
    });
  }
  let soon = null;
  const renderSlotsSoon = () => { clearTimeout(soon); soon = setTimeout(() => { if (!sb.contains(document.activeElement) || document.activeElement.type !== "datetime-local") renderSlots(); }, 400); };

  // ------------------------------------------------------------------ the event-day order
  const fl = $("[data-flow-builder]");
  function reconcileFlow() {
    const evs = evList(), keys = new Set(evs.map((e) => e.key));
    S.flow = S.flow.filter((x) => x.type === "food" || keys.has(x.ref));
    const have = new Set(S.flow.filter((x) => x.type === "event").map((x) => x.ref));
    evs.forEach((ev) => { if (!have.has(ev.key)) S.flow.push({ type: "event", ref: ev.key }); });
  }
  function slotHint(ref) {
    const u = (kind === "fest" || S.many) && S.rounds.length ? useOf(ref) : null;
    if (!u || !u.rounds.length) return "One slot for its whole time";
    return `${u.rounds.length} time slot${u.rounds.length === 1 ? "" : "s"} · each person gets one`;
  }
  function renderFlow() {
    if (!fl) return;
    reconcileFlow();
    const names = Object.fromEntries(evList().map((e) => [e.key, e.title]));
    const move = `<span class="fi-move"><button type="button" class="iconbtn" data-up aria-label="Move up">↑</button><button type="button" class="iconbtn" data-down aria-label="Move down">↓</button></span>`;
    fl.innerHTML = `<li class="flow-item fixed"><span class="fi-icon">🚪</span><span class="grow"><b>Entry</b><small>Always first: check in at the gate</small></span><span class="fi-pin" aria-hidden="true">📌</span></li>`
      + S.flow.map((x, i) => (x.type === "event"
        ? `<li class="flow-item event" data-i="${i}"><button type="button" class="drag" aria-label="Drag to reorder">⋮⋮</button><span class="fi-icon">🎯</span>
            <span class="grow"><b>${esc(names[x.ref] || "Event")}</b><small>${esc(slotHint(x.ref))}</small></span>${move}</li>`
        : `<li class="flow-item food" data-i="${i}"><button type="button" class="drag" aria-label="Drag to reorder">⋮⋮</button><span class="fi-icon">🍽️</span>
            <span class="grow meal-edit"><input data-m="title" value="${esc(x.title || "")}" placeholder="Lunch" maxlength="60" aria-label="Meal">
              <span class="meal-times"><input type="datetime-local" data-m="start" value="${esc(x.start || "")}" aria-label="From (optional)" title="From (optional)"><input type="datetime-local" data-m="end" value="${esc(x.end || "")}" aria-label="Until (optional)" title="Until (optional)"></span>
              <input data-m="venue" value="${esc(x.venue || "")}" placeholder="Where (optional)" maxlength="80" aria-label="Where"></span>
            ${move}<button type="button" class="iconbtn" data-del-meal aria-label="Remove meal">✕</button></li>`)).join("");
    serialize();
  }
  if (fl) {
    const reorder = () => {
      const order = $$("li[data-i]", fl).map((li) => S.flow[+li.dataset.i]);
      S.flow = order;
      renderFlow();
    };
    window.EF?.sortable?.(fl, { onChange: reorder });
    fl.addEventListener("click", (e) => {
      const li = e.target.closest("li[data-i]");
      if (!li) return;
      const i = +li.dataset.i;
      if (e.target.closest("[data-up]") && i > 0) { [S.flow[i - 1], S.flow[i]] = [S.flow[i], S.flow[i - 1]]; renderFlow(); fl.querySelector(`li[data-i="${i - 1}"]`)?.classList.add("pop"); }
      else if (e.target.closest("[data-down]") && i < S.flow.length - 1) { [S.flow[i + 1], S.flow[i]] = [S.flow[i], S.flow[i + 1]]; renderFlow(); fl.querySelector(`li[data-i="${i + 1}"]`)?.classList.add("pop"); }
      else if (e.target.closest("[data-del-meal]")) { S.flow.splice(i, 1); renderFlow(); }
    });
    fl.addEventListener("input", (e) => {
      const li = e.target.closest("li[data-i]");
      if (li && e.target.dataset.m) { S.flow[+li.dataset.i][e.target.dataset.m] = e.target.value; serialize(); }
    });
  }
  document.querySelector("[data-add-meal-wiz]")?.addEventListener("click", () => {
    S.flow.push({ type: "food", ref: uid("f"), title: S.flow.some((x) => x.type === "food") ? "Snacks" : "Lunch", start: "", end: "", venue: "" });
    renderFlow();
    fl.querySelector("li.food:last-child [data-m=title]")?.focus();
  });

  // ------------------------------------------------------------------ write the hidden fields
  function serialize() {
    if (editing) return;
    $("[name=fest_json]").value = kind === "fest"
      ? JSON.stringify({ tracks: S.tracks.map((t) => ({ ...t, events: t.events.filter((ev) => ev.title.trim()) })) }) : "";
    const evs = evList(), rounds = kind === "fest" || S.many ? S.rounds : [], use = {};
    evs.forEach((ev) => { const u = rounds.length ? useOf(ev.key) : { rounds: [], capacity: 20 }; use[ev.key] = { rounds: u.rounds, capacity: Math.max(1, +u.capacity || 20) }; });
    $("[name=slots_json]").value = JSON.stringify({ rounds, use });
    reconcileFlow();
    $("[name=flow_json]").value = JSON.stringify(S.flow.map((x) => (x.type === "event" ? { type: "event", ref: x.ref } : x)));
    const meals = $("[data-meals-count]");
    if (meals) meals.value = S.flow.filter((x) => x.type === "food").length;
  }

  // ------------------------------------------------------------------ the last step: a summary
  function renderSummary() {
    const box = $("[data-wiz-summary]");
    if (!box) return;
    const s = toDate(val("start_dt")), e = toDate(val("end_dt"));
    const when = s ? s.toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" })
      + (e ? " → " + (e.toDateString() === s.toDateString() ? e.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : e.toLocaleString([], { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" })) : "") : "—";
    const rows = [["Name", val("title") || "—"], ["When", when], ["Where", [val("venue"), val("city")].filter(Boolean).join(", ") || "—"]];
    if (!editing) {
      const evs = evList();
      if (kind === "fest") rows.push(["Events", `${evs.length}: ${evs.slice(0, 8).map((x) => x.title).join(", ")}${evs.length > 8 ? "…" : ""}`]);
      else rows.push(["Tickets", `${+val("fee") ? "₹" + val("fee") : "Free"} · ${val("capacity") || 0} seats${+val("team_size") > 1 ? " · teams up to " + val("team_size") : ""}`]);
      const rounds = kind === "fest" || S.many ? S.rounds.length : 0;
      rows.push(["Time slots", rounds ? `${rounds} slot${rounds === 1 ? "" : "s"}${kind === "fest" ? "" : ", " + useOf("self").capacity + " members each"}` : (kind === "fest" ? "Each event is one slot" : "One slot")]);
      reconcileFlow();
      const names = Object.fromEntries(evList().map((x) => [x.key, x.title]));
      rows.push(["Event day", ["Entry", ...S.flow.map((x) => (x.type === "event" ? names[x.ref] : (x.title || "Meal")))].join(" → ")]);
    }
    box.innerHTML = `<div class="sum-card">${rows.map(([k, v]) => `<div><small>${esc(k)}</small><b>${esc(v)}</b></div>`).join("")}</div>`;
  }

  function enter(step) {
    if (step === "events") renderFest();
    if (step === "slots") renderSlots();
    if (step === "flow") renderFlow();
    if (step === "publish") { serialize(); renderSummary(); }
  }

  // start
  steps.forEach((s, i) => { s.hidden = i !== 0; });
  syncKind();
  serialize();
  show(0, 0);
  form.classList.add("ready");
})();
