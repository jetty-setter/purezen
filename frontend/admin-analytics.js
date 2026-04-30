// ============================================================
// admin-analytics.js — Analytics tab, sortable All Reservations
// ============================================================

// --- Trends / charts ---
async function loadTrends() {
  const from = document.getElementById("analyticsFrom").value;
  const to   = document.getElementById("analyticsTo").value;
  try {
    let url = `${API}/admin/trends?token=${tok}`;
    if (from) url += `&date_from=${from}`;
    if (to)   url += `&date_to=${to}`;
    const r = await fetch(url);
    const d = await r.json();
    renderChart("serviceChart", d.by_service);
    renderChart("staffChart",   d.by_staff);
    renderTimeline(d.weekly_forward || {});
  } catch {}

  try {
    let url = `${API}/admin/ai/trends-narrative?token=${tok}`;
    if (from) url += `&date_from=${from}`;
    if (to)   url += `&date_to=${to}`;
    const r = await fetch(url);
    const d = await r.json();
    document.getElementById("trendsBody").innerHTML = fmtSnapshot(d.narrative) || "No insights available.";
    document.getElementById("regenTrendsBtn").style.display = "inline-flex";
  } catch { document.getElementById("trendsBody").innerHTML = "<em style='color:var(--charcoal-soft)'>Unavailable.</em>"; }
}

async function applyAnalyticsFilter() { await loadTrends(); filterBooks(); }

function resetAnalyticsFilter() {
  document.getElementById("analyticsFrom").value = "";
  document.getElementById("analyticsTo").value   = "";
  loadTrends();
  filterBooks();
}

document.getElementById("regenTrendsBtn").addEventListener("click", async () => {
  const btn = document.getElementById("regenTrendsBtn");
  btn.disabled=true; btn.textContent="…";
  document.getElementById("trendsBody").innerHTML = spin("Analyzing");
  const from = document.getElementById("analyticsFrom").value;
  const to   = document.getElementById("analyticsTo").value;
  try {
    let url = `${API}/admin/ai/trends-narrative?token=${tok}`;
    if (from) url += `&date_from=${from}`;
    if (to)   url += `&date_to=${to}`;
    const r = await fetch(url);
    const d = await r.json();
    document.getElementById("trendsBody").innerHTML = fmtSnapshot(d.narrative) || "No insights available.";
  } catch { document.getElementById("trendsBody").innerHTML = "<em style='color:var(--charcoal-soft)'>Unavailable.</em>"; }
  finally { btn.disabled=false; btn.textContent="Regenerate"; }
});

function renderTimeline(w) {
  const el = document.getElementById("timelineRow");
  const entries = Object.entries(w);
  const max = Math.max(...entries.map(([,v])=>v), 1);
  el.innerHTML = entries.map(([date,count])=>`
    <div class="tl-day${date===TODAY?" today":""}">
      <div class="tl-dow">${fmtDow(date)}</div>
      <div class="tl-date">${fmtShort(date)}</div>
      <div class="tl-count">${count}</div>
      <div class="tl-bar-wrap"><div class="tl-bar" style="width:${Math.round(count/max*100)}%"></div></div>
    </div>`).join("");
}

function renderChart(id, data) {
  const el  = document.getElementById(id);
  const max = Math.max(...Object.values(data), 1);
  el.innerHTML = !Object.keys(data).length
    ? `<div class="empty" style="padding:8px;">No data yet.</div>`
    : Object.entries(data).map(([l,c])=>`
        <div class="chart-row">
          <div class="chart-lbl">${l}</div>
          <div class="chart-bar-wrap"><div class="chart-bar" style="width:${Math.round(c/max*100)}%"></div></div>
          <div class="chart-cnt">${c}</div>
        </div>`).join("");
}

// --- Staff roster ---
async function loadRoster() {
  try {
    const r    = await fetch(`${API}/admin/staff/roster?token=${tok}`);
    const data = await r.json();
    const tb   = document.getElementById("rosterBody");
    if (!data.length) { tb.innerHTML=`<tr><td colspan="5" class="empty">No staff data.</td></tr>`; return; }
    tb.innerHTML = data.map(s=>`<tr>
      <td class="cp">${s.staff_name}</td>
      <td class="cp">${s.total_bookings}</td>
      <td class="cp">${s.this_week}</td>
      <td><span class="badge badge-up">${s.upcoming} upcoming</span></td>
      <td class="cs">${s.top_service}</td>
    </tr>`).join("");
  } catch { document.getElementById("rosterBody").innerHTML=`<tr><td colspan="5" class="empty">Could not load.</td></tr>`; }
}

// --- All Reservations ---
async function loadAllBookings() {
  try {
    const r = await fetch(`${API}/admin/bookings?token=${tok}`);
    allBookings = await r.json();
    renderBookTable(allBookings);
  } catch { document.getElementById("bookTableBody").innerHTML=`<tr><td colspan="8" class="empty">Could not load.</td></tr>`; }
}

// Sort state
let _sortCol = "date", _sortDir = 1;

function sortBooks(col) {
  if (_sortCol === col) { _sortDir *= -1; } else { _sortCol=col; _sortDir=1; }
  // Update header arrows
  document.querySelectorAll(".sortable-th").forEach(th => {
    th.classList.remove("sort-asc","sort-desc");
    if (th.dataset.col === col) th.classList.add(_sortDir===1?"sort-asc":"sort-desc");
  });
  filterBooks();
}

function filterBooks() {
  const s    = document.getElementById("bookSearch").value.toLowerCase();
  const from = document.getElementById("analyticsFrom").value;
  const to   = document.getElementById("analyticsTo").value;

  let filtered = allBookings.filter(b => {
    const ms = !s || [b.booking_id, b.customer_name, b.customer_email, b.service_name, b.staff_name, b.status, b.customer_phone]
      .some(v => (v||"").toLowerCase().includes(s));
    const dateOk = (!from || (b.date||"") >= from) && (!to || (b.date||"") <= to);
    return ms && dateOk;
  });

  // Sort
  filtered.sort((a,b) => {
    let av = a[_sortCol]||"", bv = b[_sortCol]||"";
    if (typeof av === "string") av = av.toLowerCase();
    if (typeof bv === "string") bv = bv.toLowerCase();
    return av < bv ? -_sortDir : av > bv ? _sortDir : 0;
  });

  renderBookTable(filtered);
}

function resetBookFilters() {
  document.getElementById("bookSearch").value = "";
  renderBookTable(allBookings);
}

function renderBookTable(bks) {
  const tb = document.getElementById("bookTableBody");
  if (!bks.length) { tb.innerHTML=`<tr><td colspan="8" class="empty">No bookings found.</td></tr>`; return; }
  tb.innerHTML = bks.map(b=>`<tr>
    <td class="cs">${b.booking_id||"—"}</td>
    <td><div class="cp">${b.customer_name||"Guest"}</div><div class="cs">${b.customer_email||""}</div></td>
    <td class="cp">${b.service_name}</td>
    <td class="cp">${b.date_display}</td>
    <td class="cp">${b.start_time ? (t => { const [h,m]=t.split(':'); const hr=+h; return (hr%12||12)+':'+m+' '+(hr<12?'AM':'PM'); })(b.start_time) : "—"}</td>
    <td class="cp">${b.staff_name||"—"}</td>
    <td><span class="badge badge-${sc(b.status)}">${b.status}</span></td>
    <td>${b.status==="Upcoming"?`<button class="btn-cancel-row" onclick="doCancel('${b.booking_id}')">Cancel</button>`:""}</td>
  </tr>`).join("");
}

document.getElementById("bookSearch").addEventListener("input", filterBooks);
document.getElementById("exportBtn").addEventListener("click", () => {
  window.location.href = `${API}/admin/bookings/export?token=${tok}`;
});

// --- Sticky analytics date bar ---
(function initStickyBar() {
  const bar      = document.getElementById("analyticsSticky");
  const analytics = document.getElementById("tab-analytics");
  window.addEventListener("scroll", () => {
    if (!analytics.classList.contains("active")) return;
    const top = analytics.getBoundingClientRect().top;
    bar.style.opacity    = top < -80 ? "1" : "0";
    bar.style.pointerEvents = top < -80 ? "auto" : "none";
    bar.style.transform  = top < -80 ? "translateY(0)" : "translateY(-10px)";
  });
})();
