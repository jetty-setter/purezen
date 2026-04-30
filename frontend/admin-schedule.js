// ============================================================
// admin-schedule.js — Schedule tab, walk-in booking, cancel modal
// ============================================================

let _schBks = [];

// --- Schedule load ---
document.getElementById("loadScheduleBtn").addEventListener("click", loadSchedule);

document.getElementById("wiDate").addEventListener("change", () => {
  const d = document.getElementById("wiDate").value;
  if (d) { document.getElementById("scheduleDate").value=d; loadSchedule(); }
});

async function loadSchedule() {
  const date = document.getElementById("scheduleDate").value;
  if (!date) return;
  document.getElementById("schConflPanel").style.display = "block";
  document.getElementById("schTableWrap").style.display  = "block";
  document.getElementById("schConflBody").innerHTML = spin("Checking");
  document.getElementById("schTableBody").innerHTML = `<tr><td colspan="7" class="empty">Loading…</td></tr>`;
  document.getElementById("wiDate").value = date;

  const [bkR, cfR, wiR] = await Promise.allSettled([
    fetch(`${API}/admin/bookings/by-date?token=${tok}&date=${date}`),
    fetch(`${API}/admin/ai/conflicts?token=${tok}&date=${date}`),
    fetch(`${API}/admin/walkin/slots?token=${tok}&date=${date}`),
  ]);

  if (bkR.status==="fulfilled") {
    try {
      _schBks = await bkR.value.json();
      populateStaff("schStaffFilter", _schBks);
      renderSchTable(_schBks);
    } catch { document.getElementById("schTableBody").innerHTML=`<tr><td colspan="7" class="empty">Could not load.</td></tr>`; }
  }

  if (cfR.status==="fulfilled") {
    try {
      const d = await cfR.value.json();
      document.getElementById("schConflBody").innerHTML = fmtAI(d.conflicts);
      document.getElementById("regenSchConflBtn").style.display = "inline-flex";
    } catch { document.getElementById("schConflBody").innerHTML="<em style='color:var(--charcoal-soft)'>Unavailable.</em>"; }
  } else { document.getElementById("schConflBody").innerHTML="<em style='color:var(--charcoal-soft)'>Unavailable.</em>"; }

  if (wiR.status==="fulfilled") {
    try {
      wiSlots = await wiR.value.json();
      const sel = document.getElementById("wiTimeSlot");
      if (!wiSlots.length) {
        sel.innerHTML = `<option value="">No available slots for this date</option>`;
      } else {
        sel.innerHTML = `<option value="">Select a time…</option>` +
          wiSlots.map((s,i)=>`<option value="${i}">${s.start_time} (${s.available_staff.length} available)</option>`).join("");
      }
      document.getElementById("wiStaff").innerHTML = `<option value="">Select a time first…</option>`;
      document.getElementById("wiStaff").disabled = true;
    } catch {}
  }
}

document.getElementById("schStaffFilter").addEventListener("change", () => {
  const v = document.getElementById("schStaffFilter").value;
  renderSchTable(v ? _schBks.filter(b=>b.staff_name===v) : _schBks);
});

document.getElementById("regenSchConflBtn").addEventListener("click", async () => {
  const date = document.getElementById("scheduleDate").value;
  if (!date) return;
  const btn = document.getElementById("regenSchConflBtn");
  btn.disabled=true; btn.textContent="…";
  document.getElementById("schConflBody").innerHTML = spin("Checking");
  try {
    const r = await fetch(`${API}/admin/ai/conflicts?token=${tok}&date=${date}`);
    const d = await r.json();
    document.getElementById("schConflBody").innerHTML = fmtAI(d.conflicts);
  } catch { document.getElementById("schConflBody").innerHTML="<em style='color:var(--charcoal-soft)'>Unavailable.</em>"; }
  finally { btn.disabled=false; btn.textContent="Recheck"; }
});

let _schSortCol = "start_time", _schSortDir = 1;
function sortSchTable(col) {
  if (_schSortCol===col) { _schSortDir*=-1; } else { _schSortCol=col; _schSortDir=1; }
  document.querySelectorAll(".sortable-th[onclick*='sortSchTable']").forEach(th=>{
    th.classList.remove("sort-asc","sort-desc");
    if(th.dataset.col===col) th.classList.add(_schSortDir===1?"sort-asc":"sort-desc");
  });
  renderSchTable(_schBks);
}

function renderSchTable(bks) {
  const tb = document.getElementById("schTableBody");
  if (!bks.length) { tb.innerHTML=`<tr><td colspan="7" class="empty">No appointments for this date.</td></tr>`; return; }
  const sorted = sortByCol(bks, _schSortCol, _schSortDir);
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

// --- Phone mask ---
document.getElementById("wiPhone").addEventListener("input", function() {
  let v = this.value.replace(/\D/g,"").substring(0,10);
  if (v.length >= 6)      v = `(${v.substring(0,3)}) ${v.substring(3,6)}-${v.substring(6)}`;
  else if (v.length >= 3) v = `(${v.substring(0,3)}) ${v.substring(3)}`;
  else if (v.length > 0)  v = `(${v}`;
  this.value = v;
});

// --- Walk-in form ---
document.getElementById("wiTimeSlot").addEventListener("change", () => {
  const idx = document.getElementById("wiTimeSlot").value;
  const staffSel = document.getElementById("wiStaff");
  if (idx==="") { staffSel.innerHTML=`<option value="">Select a time first…</option>`; staffSel.disabled=true; return; }
  const slot = wiSlots[parseInt(idx)];
  staffSel.innerHTML = `<option value="">Select staff…</option>` +
    slot.available_staff.map(s=>`<option value="${s.slot_id}">${s.staff_name}</option>`).join("");
  staffSel.disabled = false;
});

document.getElementById("wiSubmitBtn").addEventListener("click", async () => {
  const slotId  = document.getElementById("wiStaff").value;
  const service = document.getElementById("wiService").value;
  const name    = document.getElementById("wiName").value.trim();
  const phone   = document.getElementById("wiPhone").value.trim();
  const email   = document.getElementById("wiEmail").value.trim();
  const req     = document.getElementById("wiRequests").value.trim();
  const msgEl   = document.getElementById("wiMsg");
  msgEl.className="walkin-msg"; msgEl.style.display="none";
  if (!slotId||!service||!name||!phone||!email) {
    msgEl.textContent="Please fill in all required fields."; msgEl.className="walkin-msg error"; return;
  }
  const btn = document.getElementById("wiSubmitBtn");
  btn.disabled=true; btn.textContent="Booking…";
  try {
    const r = await fetch(`${API}/admin/walkin/book`, {
      method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({token:tok,slot_id:slotId,service_name:service,customer_name:name,customer_phone:phone,customer_email:email,special_requests:req||null})
    });
    const d = await r.json();
    if (!r.ok) { msgEl.textContent=d.detail||"Could not book."; msgEl.className="walkin-msg error"; return; }
    msgEl.textContent=`✓ ${d.message}`; msgEl.className="walkin-msg success";
    document.getElementById("wiTimeSlot").value="";
    document.getElementById("wiStaff").innerHTML=`<option value="">Select a time first…</option>`;
    document.getElementById("wiStaff").disabled=true;
    ["wiService","wiName","wiPhone","wiEmail","wiRequests"].forEach(id=>document.getElementById(id).value="");
    loadSchedule();
  } catch { msgEl.textContent="Could not connect."; msgEl.className="walkin-msg error"; }
  finally { btn.disabled=false; btn.textContent="Book Appointment"; }
});

// --- Cancel modal ---
let _cancelBookingId = null;

function doCancel(bookingId) {
  const b = [...allBookings, ...todayBookings, ..._schBks].find(x=>x.booking_id===bookingId);
  if (!b) return;
  _cancelBookingId = bookingId;
  document.getElementById("mGuest").textContent     = b.customer_name || "Guest";
  document.getElementById("mService").textContent   = b.service_name  || "—";
  document.getElementById("mDate").textContent      = b.date_display  || b.date || "—";
  document.getElementById("mTime").textContent      = b.start_time    || "—";
  document.getElementById("mBookingId").textContent = b.booking_id    || "—";
  document.getElementById("cancelModal").style.display = "flex";
}

function closeCancelModal() {
  document.getElementById("cancelModal").style.display = "none";
  _cancelBookingId = null;
}

document.getElementById("cancelModal").addEventListener("click", function(e) {
  if (e.target === this) closeCancelModal();
});

async function confirmCancel() {
  if (!_cancelBookingId) return;
  const btn = document.getElementById("confirmCancelBtn");
  btn.disabled=true; btn.textContent="Cancelling…";
  try {
    const r = await fetch(`${API}/admin/bookings/cancel?token=${tok}`, {
      method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({booking_id:_cancelBookingId})
    });
    const d = await r.json();
    if (r.ok) {
      closeCancelModal();
      loadOverview();
      loadAllBookings();
      if (document.getElementById("scheduleDate").value) loadSchedule();
      showToast("Appointment successfully cancelled.");
    } else { showToast(d.detail||"Could not cancel.", true); }
  } catch { showToast("Failed to connect.", true); }
  finally { btn.disabled=false; btn.textContent="Cancel Appointment"; }
}
