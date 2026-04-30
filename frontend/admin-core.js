// ============================================================
// admin-core.js — auth, nav, shared state & utilities
// ============================================================

const API = window.PUREZEN_CONFIG.API_BASE_URL;


let tok = null;
let allBookings  = [];
let todayBookings = [];
let wiSlots = [];

const TODAY = (()=>{
  const d = new Date(new Date().toLocaleString("en-US", {timeZone: "America/Chicago"}));
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
})();

// --- Session helpers ---
const sa = (t,n,r) => { sessionStorage.setItem("pz_at",t); sessionStorage.setItem("pz_an",n); sessionStorage.setItem("pz_ar",r||"admin"); };
const ca = ()      => { sessionStorage.removeItem("pz_at"); sessionStorage.removeItem("pz_an"); sessionStorage.removeItem("pz_ar"); };
const ga = ()      => { const t=sessionStorage.getItem("pz_at"),n=sessionStorage.getItem("pz_an"),r=sessionStorage.getItem("pz_ar"); return t?{t,n,r}:null; };

let sessionRole = "admin";

// --- Formatters ---
function fmtAI(t) {
  if (!t) return "";
  return t
    .replace(/^[\s\-–—]+/gm, '')
    .replace(/\*\*(.*?)\*\*/g,"<strong>$1</strong>")
    .replace(/\*(.*?)\*/g,"<em>$1</em>")
    .replace(/\n\n/g,"<br><br>")
    .replace(/\n(\d+\.)\s/g,"<br><strong>$1</strong> ")
    .replace(/\n-\s/g,"<br>• ")
    .replace(/\n/g,"<br>")
    .trim();
}

function fmtSnapshot(text) {
  if (!text) return "";
  let html = text
    .replace(/Observation\s*1\s*[:\-–]?\s*/gi, '<p style="margin-bottom:0.75rem;"><strong style="color:var(--heading);">Observation 1 —</strong> ')
    .replace(/Observation\s*2\s*[:\-–]?\s*/gi, '<p style="margin-bottom:0.75rem;"><strong style="color:var(--heading);">Observation 2 —</strong> ')
    .replace(/Actionable\s*Recommendation\s*[:\-–]?\s*/gi, '<p style="margin-bottom:0;"><strong style="color:var(--eucalyptus-deep);">Recommendation —</strong> ')
    .replace(/\.\s+(?=<p)/g, '.</p>').replace(/([^>])$/g, '$1</p>');
  if (!html.includes('<strong')) {
    const lines = text.split(/\.\s+/).filter(s => s.trim());
    if (lines.length >= 3) {
      html = `<p style="margin-bottom:0.75rem;"><strong style="color:var(--heading);">Observation 1 —</strong> ${lines[0].trim()}.</p>
              <p style="margin-bottom:0.75rem;"><strong style="color:var(--heading);">Observation 2 —</strong> ${lines[1].trim()}.</p>
              <p style="margin-bottom:0;"><strong style="color:var(--eucalyptus-deep);">Recommendation —</strong> ${lines.slice(2).join(". ").trim()}.</p>`;
    } else { html = fmtAI(text); }
  }
  return html;
}

function spin(msg="Loading") {
  return `<div class="ai-loading">${msg}… <span class="ai-dots"><span>.</span><span>.</span><span>.</span></span></div>`;
}
function fmtLong(iso)  { try { return new Date(iso+"T12:00:00").toLocaleDateString("en-US",{weekday:"long",month:"long",day:"numeric",year:"numeric"}); } catch { return iso; } }
function fmtShort(iso) { try { return new Date(iso+"T12:00:00").toLocaleDateString("en-US",{month:"short",day:"numeric"}); } catch { return iso; } }
function fmtDow(iso)   { try { return new Date(iso+"T12:00:00").toLocaleDateString("en-US",{weekday:"short"}); } catch { return ""; } }
function sc(s) { return s==="Upcoming"?"up":s==="Completed"?"done":"canc"; }

function toMin(t) {
  if (!t) return 9999;
  const m = t.match(/(\d+):(\d+)\s*(AM|PM)/i);
  if (!m) return 9999;
  let h=parseInt(m[1]), min=parseInt(m[2]), ampm=m[3].toUpperCase();
  if(ampm==="PM"&&h!==12)h+=12; if(ampm==="AM"&&h===12)h=0;
  return h*60+min;
}

function sortByCol(arr, col, dir) {
  return [...arr].sort((a,b)=>{
    let av = col==="start_time" ? toMin(a[col]) : (a[col]||"").toString().toLowerCase();
    let bv = col==="start_time" ? toMin(b[col]) : (b[col]||"").toString().toLowerCase();
    return av < bv ? -dir : av > bv ? dir : 0;
  });
}

function bindSortHeaders(tableId, renderFn, dataFn) {
  document.querySelectorAll(`#${tableId} .sortable-th`).forEach(th => {
    th.addEventListener("click", () => {
      const col = th.dataset.col;
      const wasAsc = th.classList.contains("sort-asc");
      document.querySelectorAll(`#${tableId} .sortable-th`).forEach(x => x.classList.remove("sort-asc","sort-desc"));
      th.classList.add(wasAsc ? "sort-desc" : "sort-asc");
      renderFn(sortByCol(dataFn(), col, wasAsc ? -1 : 1));
    });
  });
}

function populateStaff(selId, bks) {
  const sel = document.getElementById(selId);
  if (!sel) return;
  const cur   = sel.value;
  const staff = [...new Set(bks.map(b=>b.staff_name).filter(Boolean))].sort();
  sel.innerHTML = `<option value="">All staff</option>` + staff.map(s=>`<option value="${s}">${s}</option>`).join("");
  if (cur) sel.value = cur;
}

function showToast(msg, error=false) {
  const t = document.createElement("div");
  t.textContent = msg;
  t.style.cssText = `position:fixed;bottom:32px;left:50%;transform:translateX(-50%);background:${error?"#8b4a42":"#5f786c"};color:#fff;padding:12px 24px;border-radius:999px;font-family:'Manrope',sans-serif;font-size:0.9rem;font-weight:600;z-index:2000;box-shadow:0 8px 24px rgba(0,0,0,0.15);animation:modalIn 0.2s ease;`;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3500);
}

// --- App shell ---
function showApp(name, role) {
  sessionRole = role || "admin";
  document.getElementById("loginScreen").style.display = "none";
  document.getElementById("appShell").style.display   = "block";
  document.getElementById("adminName").textContent    = name;
  document.getElementById("overviewTitle").textContent = fmtLong(TODAY);
  document.getElementById("scheduleDate").value = TODAY;
  document.getElementById("wiDate").value       = TODAY;
  document.getElementById("analyticsFrom").value = "";
  document.getElementById("analyticsTo").value   = "";
  // Always reset to Overview tab on login
  document.querySelectorAll(".desktop-nav button").forEach(b => {
    b.classList.remove("active");
    if (b.dataset.tab === "users") b.style.display = sessionRole === "admin" ? "" : "none";
  });
  document.querySelectorAll(".tab-pane").forEach(p => p.classList.remove("active"));
  document.querySelector(".desktop-nav button[data-tab='overview']").classList.add("active");
  document.getElementById("tab-overview").classList.add("active");
  loadOverview();
  loadAllBookings();
  loadTrends();
  loadRoster();
  setTimeout(loadSchedule, 0);
}

function showLogin() {
  document.getElementById("loginScreen").style.display = "flex";
  document.getElementById("appShell").style.display    = "none";
}

// --- Auth ---
document.getElementById("loginBtn").addEventListener("click", async () => {
  const email = document.getElementById("adminEmail").value.trim();
  const pw    = document.getElementById("adminPassword").value;
  const btn   = document.getElementById("loginBtn");
  const err   = document.getElementById("loginError");
  if (!email || !pw) { err.textContent="Please fill in all fields."; err.style.display="block"; return; }
  btn.disabled=true; btn.textContent="Signing in…"; err.style.display="none";
  try {
    // Try admin login first
    let r = await fetch(`${API}/admin/login`, {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({email,password:pw})});
    let d = await r.json();
    if (r.ok) { tok=d.token; sa(d.token,d.name,"admin"); showApp(d.name,"admin"); return; }
    // Fall through to staff login
    r = await fetch(`${API}/admin/staff/login`, {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({email,password:pw})});
    d = await r.json();
    if (r.ok) { tok=d.token; sa(d.token,d.name,"staff"); showApp(d.name,"staff"); return; }
    err.textContent=d.detail||"Login failed."; err.style.display="block";
  } catch { err.textContent="Could not connect."; err.style.display="block"; }
  finally { btn.disabled=false; btn.textContent="Sign In"; }
});

document.getElementById("logoutBtn").addEventListener("click", () => { ca(); tok=null; sessionRole="admin"; showLogin(); });

// --- Nav ---
document.querySelectorAll(".desktop-nav button").forEach(b => b.addEventListener("click", () => {
  document.querySelectorAll(".desktop-nav button").forEach(x => x.classList.remove("active"));
  document.querySelectorAll(".tab-pane").forEach(x => x.classList.remove("active"));
  b.classList.add("active");
  document.getElementById("tab-"+b.dataset.tab).classList.add("active");
  window.scrollTo(0,0);
}));

// --- Session restore — deferred so all JS files are loaded first ---
window.addEventListener("load", () => {
  const ex = ga();
  if (ex) { tok=ex.t; showApp(ex.n, ex.r); }
});
