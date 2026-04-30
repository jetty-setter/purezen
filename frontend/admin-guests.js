// ============================================================
// admin-guests.js — Guest lookup tab
// ============================================================

let _guestBks = [];
let _guestSortCol = "date", _guestSortDir = 1;

function sortGuestTable(col) {
  if (_guestSortCol===col) { _guestSortDir*=-1; } else { _guestSortCol=col; _guestSortDir=1; }
  document.querySelectorAll(".sortable-th[onclick*='sortGuestTable']").forEach(th=>{
    th.classList.remove("sort-asc","sort-desc");
    if(th.dataset.col===col) th.classList.add(_guestSortDir===1?"sort-asc":"sort-desc");
  });
  renderGuestTable(_guestBks);
}

function renderGuestTable(bks) {
  const sorted = sortByCol(bks, _guestSortCol, _guestSortDir);
  document.getElementById("guestTableBody").innerHTML = sorted.map(b=>`<tr>
    <td class="cs">${b.booking_id||"—"}</td>
    <td class="cp">${b.service_name}</td>
    <td class="cp">${b.date_display}</td>
    <td class="cp">${b.start_time ? (t => { const [h,m]=t.split(':'); const hr=+h; return (hr%12||12)+':'+m+' '+(hr<12?'AM':'PM'); })(b.start_time) : "—"}</td>
    <td class="cp">${b.staff_name||"—"}</td>
    <td class="cs">${b.special_requests||"None"}</td>
    <td><span class="badge badge-${sc(b.status)}">${b.status}</span></td>
  </tr>`).join("");
}
document.getElementById("guestSearchInput").addEventListener("keydown", e => { if (e.key==="Enter") lookupGuest(); });

async function lookupGuest() {
  const query = document.getElementById("guestSearchInput").value.trim();
  if (!query) return;
  document.getElementById("guestNotesPanel").style.display = "block";
  document.getElementById("guestTableWrap").style.display  = "block";
  document.getElementById("guestNotesBody").innerHTML      = spin("Building profile");
  document.getElementById("guestTableBody").innerHTML      = `<tr><td colspan="7" class="empty">Loading…</td></tr>`;

  try {
    const r = await fetch(`${API}/admin/guest/lookup?token=${tok}&query=${encodeURIComponent(query)}`);
    const d = await r.json();

    if (!d.found || !d.bookings.length) {
      document.getElementById("guestNotesBody").innerHTML    = "<em style='color:var(--charcoal-soft)'>No booking history found for this guest.</em>";
      document.getElementById("guestTableBody").innerHTML    = `<tr><td colspan="7" class="empty">No bookings on file.</td></tr>`;
      document.getElementById("guestNotesTitle").textContent = "Guest Profile";
      return;
    }

    document.getElementById("guestNotesTitle").textContent = d.name ? `${d.name}'s Profile` : "Guest Profile";
    document.getElementById("guestNotesBody").innerHTML    = fmtAI(d.summary);
    _guestBks = d.bookings;
    renderGuestTable(_guestBks);
  } catch {
    document.getElementById("guestNotesBody").innerHTML = "<em style='color:var(--charcoal-soft)'>Could not load guest profile.</em>";
    document.getElementById("guestTableBody").innerHTML = `<tr><td colspan="7" class="empty">Could not load.</td></tr>`;
  }
}

// AI Query
document.getElementById("nlBtn").addEventListener("click", runQ);
document.getElementById("nlInput").addEventListener("keydown", e => { if (e.key==="Enter") runQ(); });

function setQ(q) { document.getElementById("nlInput").value=q; runQ(); }

async function runQ() {
  const q = document.getElementById("nlInput").value.trim();
  if (!q) return;
  document.getElementById("nlAnswerPanel").style.display = "block";
  document.getElementById("nlLabel").textContent = q;
  document.getElementById("nlBody").innerHTML = spin("Consulting");
  try {
    const r = await fetch(`${API}/admin/ai/query`, {
      method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({query:q, token:tok})
    });
    const d = await r.json();
    document.getElementById("nlBody").innerHTML = fmtAI(d.answer) || "No answer available.";
  } catch { document.getElementById("nlBody").innerHTML = "<em style='color:var(--charcoal-soft)'>Could not get an answer.</em>"; }
}
