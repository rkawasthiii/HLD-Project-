"use strict";

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------
const $ = (id) => document.getElementById(id);
const box = $("box");
const dropdown = $("suggestions");
const statusEl = $("status");
const resultEl = $("result");
const trendingMode = $("trendingMode");

let items = [];        // current suggestion objects
let activeIndex = -1;  // keyboard-highlighted row
let lastPrefix = "";

const fmt = (n) => Number(n).toLocaleString();

// Debounce: avoid a backend call on every keystroke (FR 4.1).
function debounce(fn, ms) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

function escapeHtml(s) {
  return s.replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

// Bold the matched prefix at the start of a suggestion.
function highlight(query, prefix) {
  const safe = escapeHtml(query);
  if (prefix && query.toLowerCase().startsWith(prefix.toLowerCase())) {
    return `<b>${safe.slice(0, prefix.length)}</b>${safe.slice(prefix.length)}`;
  }
  return safe;
}

// ---------------------------------------------------------------------------
// Suggestions
// ---------------------------------------------------------------------------
const fetchSuggestions = debounce(async (prefix) => {
  lastPrefix = prefix;
  if (!prefix.trim()) {
    closeDropdown();
    statusEl.textContent = "";
    return;
  }
  const mode = trendingMode.checked ? "trending" : "count";
  statusEl.textContent = "Loading…";
  statusEl.classList.remove("error");
  try {
    const t0 = performance.now();
    const res = await fetch(`/suggest?q=${encodeURIComponent(prefix)}&mode=${mode}`);
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    if (lastPrefix !== prefix) return; // a newer keystroke superseded us
    items = data.suggestions || [];
    renderSuggestions(prefix, data.source, (performance.now() - t0).toFixed(1));
  } catch (err) {
    statusEl.textContent = "Error: " + err.message;
    statusEl.classList.add("error");
    closeDropdown();
  }
}, 120);

function renderSuggestions(prefix, source, ms) {
  activeIndex = -1;
  if (!items.length) {
    dropdown.innerHTML =
      `<li class="empty"><span class="q">No matches for “${escapeHtml(prefix)}”</span></li>`;
    dropdown.classList.add("open");
    statusEl.textContent = `0 results · ${ms} ms · ${source}`;
    return;
  }
  dropdown.innerHTML = items
    .map((it, i) => {
      const extra = it.recent && it.recent > 0
        ? `<span class="badge">recent ${it.recent}</span>` : "";
      return `<li role="option" data-i="${i}">
        <span class="q">${highlight(it.query, prefix)}</span>
        <span class="meta">${extra}<span>${fmt(it.count)}</span></span>
      </li>`;
    })
    .join("");
  dropdown.classList.add("open");
  statusEl.textContent =
    `${items.length} results · ${ms} ms · source: ${source}`;

  dropdown.querySelectorAll("li[data-i]").forEach((li) => {
    li.addEventListener("mousedown", (e) => {
      e.preventDefault();
      submitSearch(items[+li.dataset.i].query);
    });
  });
}

function closeDropdown() {
  dropdown.classList.remove("open");
  dropdown.innerHTML = "";
  activeIndex = -1;
}

function moveActive(delta) {
  const rows = dropdown.querySelectorAll("li[data-i]");
  if (!rows.length) return;
  activeIndex = (activeIndex + delta + rows.length) % rows.length;
  rows.forEach((r, i) => r.classList.toggle("active", i === activeIndex));
  const q = items[activeIndex]?.query;
  if (q !== undefined) box.value = q;
}

// ---------------------------------------------------------------------------
// Search submission (POST /search)
// ---------------------------------------------------------------------------
async function submitSearch(query) {
  query = (query || box.value).trim();
  if (!query) return;
  box.value = query;
  closeDropdown();
  resultEl.classList.remove("hidden");
  resultEl.innerHTML = "Searching…";
  try {
    const res = await fetch("/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });
    const data = await res.json();
    resultEl.innerHTML =
      `<span class="ok">${escapeHtml(data.message)}</span> — “${escapeHtml(query)}”.
       <br/><small>Recorded via the batch writer; counts & trending update on the
       next flush.</small>`;
    // give the batch writer a moment to flush, then refresh trending + metrics
    setTimeout(() => { loadTrending(); loadMetrics(); }, 2200);
  } catch (err) {
    resultEl.innerHTML = `<span style="color:#f87171">Error: ${err.message}</span>`;
  }
}

// ---------------------------------------------------------------------------
// Trending section
// ---------------------------------------------------------------------------
async function loadTrending() {
  try {
    const res = await fetch("/trending?limit=10");
    const data = await res.json();
    const list = $("trending");
    list.innerHTML = (data.suggestions || [])
      .map((it) => `<li data-q="${escapeHtml(it.query)}">
          <span class="t-q">${escapeHtml(it.query)}</span>
          <span class="t-meta">count ${fmt(it.count)}${
            it.recent > 0 ? ` · recent ${it.recent}` : ""}</span>
        </li>`)
      .join("");
    list.querySelectorAll("li").forEach((li) =>
      li.addEventListener("click", () => submitSearch(li.dataset.q)));
  } catch (_) { /* non-fatal */ }
}

// ---------------------------------------------------------------------------
// Metrics panel
// ---------------------------------------------------------------------------
async function loadMetrics() {
  try {
    const res = await fetch("/metrics");
    const m = await res.json();
    const sug = m.latency.suggest || {};
    const cards = [
      ["Suggest p95", (sug.p95_ms ?? 0) + " ", "ms"],
      ["Suggest p50", (sug.p50_ms ?? 0) + " ", "ms"],
      ["Cache hit rate", (m.cache.hit_rate * 100).toFixed(1) + " ", "%"],
      ["Cache hits / miss", `${fmt(m.cache.hits)} / ${fmt(m.cache.misses)}`, ""],
      ["Search submissions", fmt(m.batch.submissions), ""],
      ["DB write txns", fmt(m.batch.db_write_ops), ""],
      ["Write reduction", m.batch.write_reduction_x + "×", ""],
      ["Indexed queries", fmt(m.trie_size), ""],
    ];
    $("metrics").innerHTML = cards
      .map(([label, value, unit]) =>
        `<div class="metric"><div class="label">${label}</div>
         <div class="value">${value}<small>${unit}</small></div></div>`)
      .join("");
  } catch (_) {
    $("metrics").textContent = "metrics unavailable";
  }
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------
box.addEventListener("input", () => fetchSuggestions(box.value));
box.addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown") { e.preventDefault(); moveActive(1); }
  else if (e.key === "ArrowUp") { e.preventDefault(); moveActive(-1); }
  else if (e.key === "Enter") { e.preventDefault(); submitSearch(); }
  else if (e.key === "Escape") { closeDropdown(); }
});
box.addEventListener("focus", () => { if (items.length) dropdown.classList.add("open"); });
document.addEventListener("click", (e) => {
  if (!e.target.closest(".search")) closeDropdown();
});
$("go").addEventListener("click", () => submitSearch());
trendingMode.addEventListener("change", () => fetchSuggestions(box.value));

// initial load
loadTrending();
loadMetrics();
setInterval(loadMetrics, 5000);
