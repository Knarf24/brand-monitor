// Page code: search box, fetching the free sources, drawing the report. Reading the replies, mood scoring
// and the summary are done in Python (monitor.py with VADER) running in Pyodide.
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const PY_FILES = ["monitor.py", "vaderSentiment/__init__.py", "vaderSentiment/vaderSentiment.py",
  "vaderSentiment/vader_lexicon.txt", "vaderSentiment/emoji_utf8_lexicon.txt"];
const TIMEOUT_MS = 15000;

let py = null, mon = null, busy = false;
let current = null;           // the report on screen
let tracked = { brands: [] }; // data/index.json from the scheduled job
let shown = 0;

// ---------- start-up ----------
async function start() {
  try {
    py = await loadPyodide({ indexURL: new URL("pyodide/", location.href).href });
    $("loadText").textContent = "Loading the mood model (VADER)…";
    py.FS.mkdirTree("/home/pyodide/vaderSentiment");
    await Promise.all(PY_FILES.map(async (f) => {
      const r = await fetch("py/" + f);
      if (!r.ok) throw new Error(f + " missing");
      py.FS.writeFile("/home/pyodide/" + f, new Uint8Array(await r.arrayBuffer()));
    }));
    py.runPython("import monitor; monitor.analyzer()");
    mon = py.pyimport("monitor");
    $("loading").hidden = true;
    document.querySelectorAll("#form input, #form select, #form button, #paste, #pasteName, #pasteGo")
      .forEach((el) => { el.disabled = false; });
    const p = new URLSearchParams(location.search);
    if (p.get("days")) $("days").value = p.get("days");
    if (p.get("q")) { $("name").value = p.get("q"); check(); }
    else if (p.get("tracked")) openTracked(p.get("tracked"));
  } catch (e) {
    console.error(e);
    $("loading").classList.add("error");
    $("loadText").textContent = "Sorry, the app could not start: " + e.message;
    document.querySelector("#loading .spinner").style.display = "none";
  }
}

async function loadTracked() {
  try {
    const r = await fetch("data/index.json", { cache: "no-cache" });
    if (!r.ok) throw new Error();
    tracked = await r.json();
  } catch { tracked = { brands: [] }; }
  const box = $("tracked");
  if (!tracked.brands.length) { box.innerHTML = '<span class="small" style="grid-column:1/-1">The first scheduled check has not run yet.</span>'; }
  else {
    box.innerHTML = tracked.brands.map((b) => `
      <button class="tcard" data-slug="${esc(b.slug)}" type="button">
        <div class="tname">${esc(b.name)}</div>
        <div class="tmood ${moodClass(b.mood)}">${b.mood ?? "–"}<small>/100</small></div>
        <div class="small">${esc(b.verdict)} · ${b.mentions} mentions</div>
        ${sparkline(b.trend)}
        <div class="small">checked ${ago(b.updated)}</div>
      </button>`).join("");
    box.querySelectorAll(".tcard").forEach((el) => el.addEventListener("click", () => openTracked(el.dataset.slug)));
  }
  const names = tracked.brands.length ? tracked.brands.map((b) => b.name) : ["Spotify", "Netflix", "Tesla", "Nike"];
  $("examples").innerHTML = names.map((n) => `<button type="button" class="chip link">${esc(n)}</button>`).join(" ");
  $("examples").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    if (!mon) return;
    $("name").value = b.textContent; check();
  }));
}

// ---------- live check ----------
async function fetchText(url) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), TIMEOUT_MS);
  try {
    const r = await fetch(url, { signal: ctl.signal, headers: { Accept: "application/json" } });
    if (!r.ok) return `ERROR: the site answered ${r.status}${r.status === 429 ? " (too many requests, try again in a minute)" : ""}`;
    return await r.text();
  } catch (e) {
    return e.name === "AbortError" ? "ERROR: no answer within 15 seconds"
      : "ERROR: the site did not allow this page to read it (blocked or offline)";
  } finally { clearTimeout(t); }
}

async function check() {
  const name = $("name").value.trim();
  if (!name || busy || !mon) return;
  busy = true; $("go").disabled = true;
  const days = +$("days").value;
  const strict = $("strict").checked;
  history.replaceState(null, "", `?q=${encodeURIComponent(name)}&days=${days}`);
  say("");
  const plan = JSON.parse(mon.plan_json(name, days, "browser"));
  const st = $("statuses");
  st.hidden = false;
  st.innerHTML = plan.map((s) => `<span class="status wait" id="st-${s.id}">${s.icon} ${esc(s.label)} <i>…</i></span>`).join("");
  const raw = {};
  await Promise.all(plan.map(async (s) => {
    raw[s.id] = await fetchText(s.url);
    const el = $("st-" + s.id);
    el.className = "status " + (raw[s.id].startsWith("ERROR:") ? "fail" : "done");
    el.querySelector("i").textContent = raw[s.id].startsWith("ERROR:") ? "✗" : "✓";
  }));
  let rep;
  try { rep = JSON.parse(mon.analyze_json(name, JSON.stringify(raw), days, strict)); }
  catch (e) { console.error(e); say("Something went wrong while scoring: " + e.message, "warn"); busy = false; $("go").disabled = false; return; }
  for (const [id, s] of Object.entries(rep.statuses)) {
    const el = $("st-" + id);
    if (!el) continue;
    el.className = "status " + (s.ok ? "done" : "fail");
    el.title = s.ok ? "" : s.note;
    el.querySelector("i").textContent = s.ok ? s.found : "✗";
  }
  const failed = Object.values(rep.statuses).filter((s) => !s.ok).length;
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  const snap = tracked.brands.find((b) => b.slug === slug);
  if (rep.mentions_total === 0 && snap) {
    say(`The live sources did not answer, so this is the last scheduled check of ${esc(snap.name)} (${ago(snap.updated)}).`, "info");
    await openTracked(slug, true);
  } else {
    show(rep);
    if (failed) say(`${failed} source${failed > 1 ? "s" : ""} could not be read right now (hover the ✗ to see why). The report uses the others.`, "info");
    if (rep.mentions_total === 0) say(`No mentions of “${esc(name)}” found in this period. Try a longer period, check the spelling, or untick “Only posts that actually name it”.`, "warn");
  }
  busy = false; $("go").disabled = false;
}

async function openTracked(slug, keepMessage) {
  try {
    const r = await fetch(`data/${encodeURIComponent(slug)}.json`, { cache: "no-cache" });
    if (!r.ok) throw new Error();
    const d = await r.json();
    if (!keepMessage) say(`Last scheduled check of ${esc(d.report.name)}, ${ago(d.report.generated)}, including Google News. Use the search box for a live check.`, "info");
    $("statuses").hidden = true;
    show(d.report, d.history);
    history.replaceState(null, "", `?tracked=${encodeURIComponent(slug)}`);
  } catch { say("That tracked brand could not be loaded.", "warn"); }
}

function scorePasted() {
  const text = $("paste").value.trim();
  if (!text || !mon) return;
  const name = $("pasteName").value.trim() || "Your text";
  const rep = JSON.parse(mon.analyze_json(name, JSON.stringify({ pasted: text }), 1, false));
  $("statuses").hidden = true;
  say(`Scored ${rep.mentions_total} lines of your own text. Nothing was sent anywhere.`, "info");
  show(rep);
}

// ---------- drawing the report ----------
function show(rep, hist) {
  current = rep;
  $("results").hidden = false;
  $("rName").textContent = rep.name;
  $("moodNum").textContent = rep.mood ?? "–";
  $("gauge").className = "gauge " + moodClass(rep.mood);
  $("gauge").style.setProperty("--p", rep.mood ?? 50);
  $("verdict").textContent = rep.verdict;
  $("verdict").className = "verdict " + moodClass(rep.mood);
  const net = rep.net == null ? "" : ` · net ${rep.net > 0 ? "+" : ""}${Math.round(rep.net)}`;
  $("rMeta").textContent = `${rep.mentions_total} mentions · ${rep.days === 1 ? "last 24 hours" : "last " + rep.days + " days"}${net}`;
  const s = rep.shares;
  $("shareBar").innerHTML = ["positive", "neutral", "negative"].map((k) =>
    s[k] ? `<span class="${k}" style="width:${s[k]}%">${s[k] >= 9 ? Math.round(s[k]) + "%" : ""}</span>` : "").join("");
  $("shareLegend").innerHTML = `<span><i class="dot positive"></i>Positive ${Math.round(s.positive)}%</span>
    <span><i class="dot neutral"></i>Neutral ${Math.round(s.neutral)}%</span>
    <span><i class="dot negative"></i>Negative ${Math.round(s.negative)}%</span>`;
  $("alerts").innerHTML = rep.alerts.map((a) => `<div class="alert ${a.startsWith("Good") ? "good" : ""}">${a.startsWith("Good") ? "✅" : "⚠️"} ${esc(a)}</div>`).join("");

  if (hist && hist.length > 1) {
    $("chartTitle").textContent = "Mood over time (scheduled checks)";
    $("chartNote").textContent = "Each point is one scheduled check of the last 7 days. Bars: mentions found.";
    $("chart").innerHTML = chart(hist.map((h) => ({ date: h.date.slice(5, 10), mentions: h.mentions, mood: h.mood,
      positive: Math.round(h.mentions * h.positive / 100), negative: Math.round(h.mentions * h.negative / 100) })));
  } else {
    $("chartTitle").textContent = "Mentions and mood per day";
    $("chartNote").textContent = "Bars: number of mentions (green praise, red complaints, grey neutral). Line: mood score that day.";
    $("chart").innerHTML = chart(rep.per_day.map((d) => ({ ...d, date: d.date.slice(5) })));
  }

  $("sourceTable").innerHTML = `<tr><th>Source</th><th>Mentions</th><th>Mood</th><th>Positive / negative</th></tr>` +
    (rep.per_source.length ? rep.per_source.map((p) => `<tr><td>${p.icon} ${esc(p.label)}</td><td>${p.mentions}</td>
      <td><b class="${moodClass(p.mood)}">${p.mood}</b></td>
      <td><div class="bar mini"><span class="positive" style="width:${p.positive}%"></span><span class="neutral" style="width:${p.neutral}%"></span><span class="negative" style="width:${p.negative}%"></span></div></td></tr>`).join("")
      : `<tr><td colspan="4" class="small">No mentions found.</td></tr>`) +
    Object.entries(rep.statuses).filter(([, v]) => !v.ok).map(([k, v]) =>
      `<tr class="small"><td colspan="4">✗ ${esc(k)}: ${esc(v.note)}</td></tr>`).join("");

  $("praiseThemes").innerHTML = chips(rep.praise_themes, "positive");
  $("complaintThemes").innerHTML = chips(rep.complaint_themes, "negative");
  $("praise").innerHTML = rep.praise.map(item).join("") || '<li class="small">No clearly positive mentions.</li>';
  $("critical").innerHTML = rep.critical.map(item).join("") || '<li class="small">No clearly negative mentions. 🎉</li>';

  const sources = [...new Set(rep.mentions.map((m) => m.source))];
  $("fSource").innerHTML = '<option value="">Every source</option>' +
    sources.map((s) => `<option value="${s}">${esc(rep.per_source.find((p) => p.id === s)?.label || s)}</option>`).join("");
  $("fLabel").value = "";
  renderAll(true);
  $("results").scrollIntoView({ behavior: "smooth", block: "start" });
}

function filtered() {
  const l = $("fLabel").value, s = $("fSource").value;
  return current.mentions.filter((m) => (!l || m.label === l) && (!s || m.source === s));
}

function renderAll(reset) {
  if (reset) shown = 0;
  const list = filtered();
  const next = list.slice(shown, shown + 40);
  if (reset) $("all").innerHTML = "";
  $("all").insertAdjacentHTML("beforeend", next.map(item).join("") || (reset ? '<li class="small">Nothing here.</li>' : ""));
  shown += next.length;
  $("more").hidden = shown >= list.length;
  $("allCount").textContent = `(${list.length})`;
}

const ICONS = { news: "📰", gnews: "🗞️", reddit: "👽", hn: "🟧", bluesky: "🦋", mastodon: "🐘", pasted: "📋" };
function item(m) {
  const text = m.title || m.text;
  const extra = m.title && m.text ? `<div class="mtext">${esc(m.text.slice(0, 220))}${m.text.length > 220 ? "…" : ""}</div>` : "";
  const head = m.url ? `<a href="${esc(m.url)}" target="_blank" rel="noopener nofollow">${esc(text.slice(0, 260))}</a>` : esc(text.slice(0, 400));
  const who = [m.author, m.where].filter(Boolean).join(" · ");
  return `<li class="m ${m.label}"><span class="score ${m.label}" title="Mood −1 to +1">${m.compound > 0 ? "+" : ""}${m.compound.toFixed(2)}</span>
    <div><div>${head}</div>${extra}<div class="small">${ICONS[m.source] || ""} ${esc(who)}${m.ts ? " · " + ago(m.ts) : ""}${m.engagement ? " · " + m.engagement + " likes/votes" : ""}</div></div></li>`;
}

function chips(list, cls) {
  return list.length ? list.map((t) => `<span class="chip ${cls}">${esc(t.word)} <b>${t.mentions}</b></span>`).join("")
    : '<span class="small">Not enough mentions to see a pattern.</span>';
}

function chart(points) {
  const W = 560, H = 220, L = 34, R = 34, T = 12, B = 26;
  const n = points.length, bw = (W - L - R) / Math.max(1, n);
  const maxM = Math.max(1, ...points.map((p) => p.mentions));
  const y = (v) => T + (H - T - B) * (1 - v / maxM);
  const ym = (v) => T + (H - T - B) * (1 - v / 100);
  let bars = "", line = "", dots = "", labels = "";
  const every = Math.ceil(n / 10);
  points.forEach((p, i) => {
    const x = L + i * bw + bw * 0.15, w = bw * 0.7;
    const neu = Math.max(0, p.mentions - p.positive - p.negative);
    let top = H - B;
    for (const [v, c] of [[p.negative, "negative"], [neu, "neutral"], [p.positive, "positive"]]) {
      if (!v) continue;
      const h = (H - T - B) * v / maxM;
      top -= h;
      bars += `<rect class="${c}" x="${x.toFixed(1)}" y="${top.toFixed(1)}" width="${w.toFixed(1)}" height="${h.toFixed(1)}" rx="2"><title>${p.date}: ${v} ${c}</title></rect>`;
    }
    if (p.mood != null) {
      const cx = L + i * bw + bw / 2;
      line += `${line ? "L" : "M"}${cx.toFixed(1)},${ym(p.mood).toFixed(1)}`;
      dots += `<circle cx="${cx.toFixed(1)}" cy="${ym(p.mood).toFixed(1)}" r="3.5"><title>${p.date}: mood ${p.mood}</title></circle>`;
    }
    if (i % every === 0) labels += `<text x="${(L + i * bw + bw / 2).toFixed(1)}" y="${H - 8}" text-anchor="middle">${p.date}</text>`;
  });
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Mentions and mood chart">
    <line class="axis" x1="${L}" x2="${W - R}" y1="${ym(50)}" y2="${ym(50)}" stroke-dasharray="4 4"/>
    <text x="${W - R + 4}" y="${ym(50) + 4}">50</text><text x="${W - R + 4}" y="${ym(100) + 8}">100</text><text x="${W - R + 4}" y="${ym(0)}">0</text>
    <text x="${L - 6}" y="${y(maxM) + 8}" text-anchor="end">${maxM}</text><text x="${L - 6}" y="${H - B}" text-anchor="end">0</text>
    ${bars}<path class="moodline" d="${line}"/>${dots}${labels}</svg>`;
}

function sparkline(vals) {
  const v = (vals || []).filter((x) => x != null);
  if (v.length < 2) return '<div class="spark small">trend after 2+ checks</div>';
  const W = 140, H = 34, lo = Math.min(...v, 40), hi = Math.max(...v, 60);
  const pts = v.map((x, i) => `${(i * W / (v.length - 1)).toFixed(1)},${(H - 3 - (H - 6) * (x - lo) / (hi - lo)).toFixed(1)}`).join(" ");
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" aria-hidden="true"><polyline points="${pts}"/></svg>`;
}

function moodClass(m) { return m == null ? "" : m >= 55 ? "positive" : m <= 45 ? "negative" : "neutral"; }

function ago(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 3600) return Math.max(1, Math.round(s / 60)) + " min ago";
  if (s < 86400) return Math.round(s / 3600) + " h ago";
  return Math.round(s / 86400) + " days ago";
}

function say(html, kind) {
  const el = $("message");
  el.hidden = !html;
  el.className = "message " + (kind || "");
  el.innerHTML = html;
}

function downloadCsv() {
  if (!current) return;
  const cols = ["source", "when", "mood", "label", "title", "text", "author", "where", "engagement", "url"];
  const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const rows = current.mentions.map((m) => [m.source, m.ts ? new Date(m.ts * 1000).toISOString() : "", m.compound, m.label,
    m.title, m.text, m.author, m.where, m.engagement, m.url].map(q).join(","));
  const blob = new Blob(["﻿" + [cols.join(","), ...rows].join("\r\n")], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${current.name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}-mentions.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

$("form").addEventListener("submit", (e) => { e.preventDefault(); check(); });
$("pasteGo").addEventListener("click", scorePasted);
$("fLabel").addEventListener("change", () => renderAll(true));
$("fSource").addEventListener("change", () => renderAll(true));
$("more").addEventListener("click", () => renderAll(false));
$("csv").addEventListener("click", downloadCsv);
$("share").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(location.href); $("share").textContent = "✓ Link copied"; }
  catch { $("share").textContent = location.href; }
  setTimeout(() => { $("share").textContent = "🔗 Copy link"; }, 2500);
});

loadTracked();
start();
