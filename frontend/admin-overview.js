// ============================================================
// admin-overview.js — Overview tab
// ============================================================

async function loadOverview() {
  try {
    const r = await fetch(`${API}/admin/trends?token=${tok}`);
    const d = await r.json();
    document.getElementById("statPeak").textContent = d.peak_hour || "—";
  } catch {}

  const [bkR, smR, cfR, upR] = await Promise.allSettled([
    fetch(`${API}/admin/bookings/by-date?token=${tok}&date=${TODAY}`),
    fetch(`${API}/admin/ai/schedule-summary?token=${tok}&date=${TODAY}`),
    fetch(`${API}/admin/ai/conflicts?token=${tok}&date=${TODAY}`),
    fetch(`${API}/admin/bookings/upcoming?token=${tok}&limit=5`),
  ]);

  if (bkR.status==="fulfilled") {
    try {
      todayBookings = await bkR.value.json();
      document.getElementById("statToday").textContent = todayBookings.filter(b=>b.status==="Upcoming").length;
      populateStaff("ovStaffFilter", todayBookings);
      renderOvTable(todayBookings);
    } catch { document.getElementById("ovTableBody").innerHTML=`<tr><td colspan="7" class="empty">Could not load.</td></tr>`; }
  }

  if (smR.status==="fulfilled") {
    try {
      const d = await smR.value.json();
      document.getElementById("ovSummBody").innerHTML = fmtAI(d.summary);
      document.getElementById("regenOvSummBtn").style.display = "inline-flex";
    } catch { document.getElementById("ovSummBody").innerHTML="<em style='color:var(--charcoal-soft)'>Unavailable.</em>"; }
  } else { document.getElementById("ovSummBody").innerHTML="<em style='color:var(--charcoal-soft)'>Unavailable.</em>"; }

  if (cfR.status==="fulfilled") {
    try {
      const d = await cfR.value.json();
      document.getElementById("ovConflBody").innerHTML = fmtAI(d.conflicts);
      document.getElementById("regenOvConflBtn").style.display = "inline-flex";
    } catch { document.getElementById("ovConflBody").innerHTML="<em style='color:var(--charcoal-soft)'>Unavailable.</em>"; }
  } else { document.getElementById("ovConflBody").innerHTML="<em style='color:var(--charcoal-soft)'>Unavailable.</em>"; }

  if (upR.status==="fulfilled") {
    try { renderUpcoming(await upR.value.json()); }
    catch { document.getElementById("upcomingBody").innerHTML="<em style='color:var(--charcoal-soft)'>Unavailable.</em>"; }
  } else { document.getElementById("upcomingBody").innerHTML="<em style='color:var(--charcoal-soft)'>Unavailable.</em>"; }
}

function renderUpcoming(bks) {
  const el = document.getElementById("upcomingBody");
  if (!bks.length) { el.innerHTML=`<div class="empty" style="padding:16px;">No upcoming appointments.</div>`; return; }
  bks = [...bks].sort((a,b) => (a.date||"") !== (b.date||"") ? (a.date||"").localeCompare(b.date||"") : toMin(a.start_time) - toMin(b.start_time));
  el.innerHTML = `<div class="upcoming-list">` +
    bks.map(b=>`<div class="upcoming-item">
      <div class="upcoming-time">${b.start_time ? (t => { const [h,m]=t.split(':'); const hr=+h; return (hr%12||12)+':'+m+' '+(hr<12?'AM':'PM'); })(b.start_time) : "—"}</div>
      <div class="upcoming-info">
        <div class="upcoming-svc">${b.service_name}</div>
        <div class="upcoming-meta">${b.customer_name||"Guest"} · ${b.staff_name||"Unassigned"}</div>
      </div>
      <div class="upcoming-dt">${(b.date||"").substring(0,10)===TODAY?"Today":fmtShort(b.date)}</div>
    </div>`).join("") + `</div>`;
}

// Sort state
let _ovSortCol = "start_time", _ovSortDir = 1;

function sortOvTable(col) {
  if (_ovSortCol === col) { _ovSortDir *= -1; } else { _ovSortCol=col; _ovSortDir=1; }
  document.querySelectorAll("[onclick^='sortOvTable']").forEach(th => {
    th.classList.remove("sort-asc","sort-desc");
    if (th.dataset.col === col) th.classList.add(_ovSortDir===1?"sort-asc":"sort-desc");
  });
  const v = document.getElementById("ovStaffFilter").value;
  renderOvTable(v ? todayBookings.filter(b=>b.staff_name===v) : todayBookings);
}


function renderOvTable(bks) {
  const tb = document.getElementById("ovTableBody");
  if (!bks.length) { tb.innerHTML=`<tr><td colspan="7" class="empty">No appointments today.</td></tr>`; return; }
  const sorted = sortByCol(bks, _ovSortCol, _ovSortDir);
  tb.innerHTML = sorted.map(b=>`<tr>
    <td><span class="cp">${b.start_time ? (t => { const [h,m]=t.split(':'); const hr=+h; return (hr%12||12)+':'+m+' '+(hr<12?'AM':'PM'); })(b.start_time) : "—"}</span></td>
    <td><div class="cp">${b.customer_name||"Guest"}</div><div class="cs">${b.customer_phone||""}</div></td>
    <td class="cp">${b.service_name}</td>
    <td class="cp">${b.staff_name||"—"}</td>
    <td class="cs">${b.special_requests||"None"}</td>
    <td><span class="badge badge-${sc(b.status)}">${b.status}</span></td>
    <td>${b.status==="Upcoming"?`<button class="btn-cancel-row" onclick="doCancel('${b.booking_id}')">Cancel</button>`:""}</td>
  </tr>`).join("");
}

// Event listeners
document.getElementById("ovStaffFilter").addEventListener("change", () => {
  const v = document.getElementById("ovStaffFilter").value;
  renderOvTable(v ? todayBookings.filter(b=>b.staff_name===v) : todayBookings);
});

document.getElementById("regenOvSummBtn").addEventListener("click", async () => {
  const btn = document.getElementById("regenOvSummBtn");
  btn.disabled=true; btn.textContent="…";
  document.getElementById("ovSummBody").innerHTML = spin("Reading");
  try {
    const r = await fetch(`${API}/admin/ai/schedule-summary?token=${tok}&date=${TODAY}`);
    const d = await r.json();
    document.getElementById("ovSummBody").innerHTML = fmtAI(d.summary);
  } catch { document.getElementById("ovSummBody").innerHTML="<em style='color:var(--charcoal-soft)'>Unavailable.</em>"; }
  finally { btn.disabled=false; btn.textContent="Regenerate"; }
});

document.getElementById("regenOvConflBtn").addEventListener("click", async () => {
  const btn = document.getElementById("regenOvConflBtn");
  btn.disabled=true; btn.textContent="…";
  document.getElementById("ovConflBody").innerHTML = spin("Checking");
  try {
    const r = await fetch(`${API}/admin/ai/conflicts?token=${tok}&date=${TODAY}`);
    const d = await r.json();
    document.getElementById("ovConflBody").innerHTML = fmtAI(d.conflicts);
  } catch { document.getElementById("ovConflBody").innerHTML="<em style='color:var(--charcoal-soft)'>Unavailable.</em>"; }
  finally { btn.disabled=false; btn.textContent="Recheck"; }
});
