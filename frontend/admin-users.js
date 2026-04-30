// ============================================================
// admin-users.js — Admin accounts, Customers, Staff management
// ============================================================

let _allCustomers = [];
let _allAdmins    = [];
let _allStaff     = [];

// --- Load all three panels when Users tab is activated ---
document.querySelectorAll(".desktop-nav button").forEach(b => {
  if (b.dataset.tab === "users") {
    b.addEventListener("click", loadUsersTab);
  }
});

function loadUsersTab() {
  loadAdmins();
  loadCustomers();
  loadStaff();
}

// ============================================================
// ADMINS
// ============================================================

async function loadAdmins() {
  document.getElementById("adminTableBody").innerHTML = `<tr><td colspan="4" class="empty">Loading…</td></tr>`;
  try {
    const r = await fetch(`${API}/admin/users/admins?token=${tok}`);
    _allAdmins = await r.json();
    renderAdminTable(_allAdmins);
  } catch {
    document.getElementById("adminTableBody").innerHTML = `<tr><td colspan="4" class="empty">Could not load.</td></tr>`;
  }
}

function renderAdminTable(admins) {
  const tb = document.getElementById("adminTableBody");
  if (!admins.length) { tb.innerHTML = `<tr><td colspan="4" class="empty">No admin accounts found.</td></tr>`; return; }
  tb.innerHTML = admins.map(a => `<tr>
    <td class="cp">${a.name || "—"}</td>
    <td class="cs">${a.email || "—"}</td>
    <td><span class="badge ${a.active !== false ? 'badge-up' : 'badge-canc'}">${a.active !== false ? "Active" : "Inactive"}</span></td>
    <td>
      <div style="display:flex;gap:6px;flex-wrap:wrap;">
        <button class="btn-cancel-row" style="background:rgba(95,120,108,0.1);color:var(--eucalyptus-deep);" onclick="openResetAdminPasswordModal('${a.admin_id}', '${a.name}')">Reset Password</button>
        ${a.active !== false
          ? `<button class="btn-cancel-row" onclick="toggleAdmin('${a.admin_id}', false)">Deactivate</button>`
          : `<button class="btn-cancel-row" style="background:rgba(95,120,108,0.1);color:var(--eucalyptus-deep);" onclick="toggleAdmin('${a.admin_id}', true)">Reactivate</button>`
        }
      </div>
    </td>
  </tr>`).join("");
}

async function toggleAdmin(adminId, activate) {
  const endpoint = activate ? "reactivate" : "deactivate";
  try {
    const r = await fetch(`${API}/admin/users/admins/${endpoint}`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({token: tok, admin_id: adminId})
    });
    const d = await r.json();
    if (!r.ok) { showToast(d.detail || "Action failed.", true); return; }
    showToast(d.message);
    loadAdmins();
  } catch { showToast("Could not connect.", true); }
}

function openAdminModal() {
  document.getElementById("newAdminName").value = "";
  document.getElementById("newAdminEmail").value = "";
  document.getElementById("newAdminPassword").value = "";
  document.getElementById("adminModalErr").style.display = "none";
  document.getElementById("adminModal").style.display = "flex";
}

function closeAdminModal() {
  document.getElementById("adminModal").style.display = "none";
}

document.getElementById("adminModal").addEventListener("click", function(e) {
  if (e.target === this) closeAdminModal();
});

async function submitNewAdmin() {
  const name  = document.getElementById("newAdminName").value.trim();
  const email = document.getElementById("newAdminEmail").value.trim();
  const pw    = document.getElementById("newAdminPassword").value;
  const err   = document.getElementById("adminModalErr");
  if (!name || !email || !pw) { err.textContent = "All fields are required."; err.style.display = "block"; return; }
  if (pw.length < 8) { err.textContent = "Password must be at least 8 characters."; err.style.display = "block"; return; }
  const btn = document.getElementById("submitAdminBtn");
  btn.disabled = true; btn.textContent = "Creating…";
  try {
    const r = await fetch(`${API}/admin/users/admins/create`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({token: tok, name, email, password: pw})
    });
    const d = await r.json();
    if (!r.ok) { err.textContent = d.detail || "Could not create admin."; err.style.display = "block"; return; }
    closeAdminModal();
    showToast(d.message);
    loadAdmins();
  } catch { err.textContent = "Could not connect."; err.style.display = "block"; }
  finally { btn.disabled = false; btn.textContent = "Create Admin"; }
}

// ============================================================
// CUSTOMERS
// ============================================================

async function loadCustomers() {
  document.getElementById("customerTableBody").innerHTML = `<tr><td colspan="7" class="empty">Loading…</td></tr>`;
  try {
    const r = await fetch(`${API}/admin/users/customers?token=${tok}`);
    _allCustomers = await r.json();
    renderCustomerTable(_allCustomers);
  } catch {
    document.getElementById("customerTableBody").innerHTML = `<tr><td colspan="7" class="empty">Could not load.</td></tr>`;
  }
}

function renderCustomerTable(customers) {
  const tb = document.getElementById("customerTableBody");
  if (!customers.length) { tb.innerHTML = `<tr><td colspan="7" class="empty">No customers found.</td></tr>`; return; }
  tb.innerHTML = customers.map(u => {
    const joined = u.created_at ? new Date(u.created_at).toLocaleDateString("en-US", {month:"short", day:"numeric", year:"numeric"}) : "—";
    return `<tr>
      <td class="cp">${u.name || "—"}</td>
      <td class="cs">${u.email || "—"}</td>
      <td class="cs">${u.phone || "—"}</td>
      <td class="cp">${u.bookings}</td>
      <td class="cs">${joined}</td>
      <td><span class="badge ${u.active !== false ? 'badge-up' : 'badge-canc'}">${u.active !== false ? "Active" : "Inactive"}</span></td>
      <td>
        ${u.active !== false
          ? `<button class="btn-cancel-row" onclick="toggleCustomer('${u.user_id}', false)">Deactivate</button>`
          : `<button class="btn-cancel-row" style="background:rgba(95,120,108,0.1);color:var(--eucalyptus-deep);" onclick="toggleCustomer('${u.user_id}', true)">Reactivate</button>`
        }
      </td>
    </tr>`;
  }).join("");
}

async function toggleCustomer(userId, activate) {
  const endpoint = activate ? "reactivate" : "deactivate";
  try {
    const r = await fetch(`${API}/admin/users/customers/${endpoint}`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({token: tok, user_id: userId})
    });
    const d = await r.json();
    if (!r.ok) { showToast(d.detail || "Action failed.", true); return; }
    showToast(d.message);
    loadCustomers();
  } catch { showToast("Could not connect.", true); }
}

document.getElementById("customerSearch").addEventListener("input", function() {
  const q = this.value.toLowerCase();
  renderCustomerTable(q
    ? _allCustomers.filter(u => (u.name||"").toLowerCase().includes(q) || (u.email||"").toLowerCase().includes(q))
    : _allCustomers
  );
});

// ============================================================
// STAFF
// ============================================================

async function loadStaff() {
  document.getElementById("staffTableBody").innerHTML = `<tr><td colspan="8" class="empty">Loading…</td></tr>`;
  try {
    const r = await fetch(`${API}/admin/users/staff?token=${tok}`);
    _allStaff = await r.json();
    renderStaffTable(_allStaff);
  } catch {
    document.getElementById("staffTableBody").innerHTML = `<tr><td colspan="8" class="empty">Could not load.</td></tr>`;
  }
}

function renderStaffTable(staff) {
  const tb = document.getElementById("staffTableBody");
  if (!staff.length) { tb.innerHTML = `<tr><td colspan="8" class="empty">No staff members found.</td></tr>`; return; }
  tb.innerHTML = staff.map(s => {
    const skills = Array.isArray(s.skills) ? s.skills.map(sk => sk.replace(/_/g, " ")).join(", ") : "—";
    return `<tr>
      <td><div class="cp">${s.first_name} ${s.last_name}</div><div class="cs">${s.display_name || ""}</div></td>
      <td class="cs">${s.role || "—"}</td>
      <td class="cs">${s.email || "—"}</td>
      <td class="cs">${s.employment_type || "—"}</td>
      <td class="cs" style="max-width:180px;">${skills}</td>
      <td class="cp">${s.weekly_hours_limit || "—"}</td>
      <td><span class="badge ${s.is_active !== false ? 'badge-up' : 'badge-canc'}">${s.is_active !== false ? "Active" : "Inactive"}</span></td>
      <td>
        <div style="display:flex;gap:6px;flex-wrap:wrap;">
          <button class="btn-cancel-row" style="background:rgba(95,120,108,0.1);color:var(--eucalyptus-deep);" onclick="openSetPasswordModal('${s.staff_id}', '${s.first_name} ${s.last_name}')">Set Password</button>
          ${s.is_active !== false
            ? `<button class="btn-cancel-row" onclick="toggleStaff('${s.staff_id}', false)">Deactivate</button>`
            : `<button class="btn-cancel-row" style="background:rgba(95,120,108,0.1);color:var(--eucalyptus-deep);" onclick="toggleStaff('${s.staff_id}', true)">Reactivate</button>`
          }
        </div>
      </td>
    </tr>`;
  }).join("");
}

async function toggleStaff(staffId, activate) {
  try {
    const r = await fetch(`${API}/admin/users/staff/toggle`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({token: tok, staff_id: staffId, is_active: activate})
    });
    const d = await r.json();
    if (!r.ok) { showToast(d.detail || "Action failed.", true); return; }
    showToast(d.message);
    loadStaff();
  } catch { showToast("Could not connect.", true); }
}

function openStaffModal() {
  ["sfFirstName","sfLastName","sfRole","sfEmail","sfSkills"].forEach(id => document.getElementById(id).value = "");
  document.getElementById("sfEmpType").value = "Full-Time";
  document.getElementById("sfHours").value = "40";
  document.getElementById("staffModalErr").style.display = "none";
  document.getElementById("staffModal").style.display = "flex";
}

function closeStaffModal() {
  document.getElementById("staffModal").style.display = "none";
}

document.getElementById("staffModal").addEventListener("click", function(e) {
  if (e.target === this) closeStaffModal();
});

async function submitNewStaff() {
  const firstName = document.getElementById("sfFirstName").value.trim();
  const lastName  = document.getElementById("sfLastName").value.trim();
  const role      = document.getElementById("sfRole").value.trim();
  const email     = document.getElementById("sfEmail").value.trim();
  const empType   = document.getElementById("sfEmpType").value;
  const hours     = parseInt(document.getElementById("sfHours").value) || 40;
  const skillsRaw = document.getElementById("sfSkills").value.trim();
  const skills    = skillsRaw ? skillsRaw.split(",").map(s => s.trim().toLowerCase().replace(/\s+/g,"_")).filter(Boolean) : [];
  const err = document.getElementById("staffModalErr");
  if (!firstName || !lastName || !role || !email) {
    err.textContent = "First name, last name, role, and email are required.";
    err.style.display = "block"; return;
  }
  const btn = document.getElementById("submitStaffBtn");
  btn.disabled = true; btn.textContent = "Adding…";
  try {
    const r = await fetch(`${API}/admin/users/staff/create`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({token: tok, first_name: firstName, last_name: lastName, role, email, employment_type: empType, weekly_hours_limit: hours, skills})
    });
    const d = await r.json();
    if (!r.ok) { err.textContent = d.detail || "Could not add staff."; err.style.display = "block"; return; }
    closeStaffModal();
    showToast(d.message);
    loadStaff();
  } catch { err.textContent = "Could not connect."; err.style.display = "block"; }
  finally { btn.disabled = false; btn.textContent = "Add Staff Member"; }
}

// ============================================================
// SET STAFF PASSWORD MODAL
// ============================================================

let _setPasswordStaffId = null;

function openSetPasswordModal(staffId, staffName) {
  _setPasswordStaffId = staffId;
  document.getElementById("setPasswordStaffName").textContent = staffName;
  document.getElementById("setPasswordInput").value = "";
  document.getElementById("setPasswordErr").style.display = "none";
  document.getElementById("setPasswordModal").style.display = "flex";
}

function closeSetPasswordModal() {
  document.getElementById("setPasswordModal").style.display = "none";
  _setPasswordStaffId = null;
}

document.addEventListener("click", function(e) {
  if (e.target && e.target.id === "setPasswordModal") closeSetPasswordModal();
});

async function submitSetPassword() {
  const pw  = document.getElementById("setPasswordInput").value;
  const err = document.getElementById("setPasswordErr");
  if (!pw || pw.length < 8) { err.textContent = "Password must be at least 8 characters."; err.style.display = "block"; return; }
  const btn = document.getElementById("submitSetPasswordBtn");
  btn.disabled = true; btn.textContent = "Saving…";
  try {
    const r = await fetch(`${API}/admin/staff/set-password`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({token: tok, staff_id: _setPasswordStaffId, password: pw})
    });
    const d = await r.json();
    if (!r.ok) { err.textContent = d.detail || "Could not set password."; err.style.display = "block"; return; }
    closeSetPasswordModal();
    showToast("Password set. Staff member can now log in.");
  } catch { err.textContent = "Could not connect."; err.style.display = "block"; }
  finally { btn.disabled = false; btn.textContent = "Set Password"; }
}

// ============================================================
// RESET ADMIN PASSWORD MODAL
// ============================================================

let _resetAdminId = null;

function openResetAdminPasswordModal(adminId, adminName) {
  _resetAdminId = adminId;
  document.getElementById("resetAdminPasswordName").textContent = adminName;
  document.getElementById("resetAdminPasswordInput").value = "";
  document.getElementById("resetAdminPasswordErr").style.display = "none";
  document.getElementById("resetAdminPasswordModal").style.display = "flex";
}

function closeResetAdminPasswordModal() {
  document.getElementById("resetAdminPasswordModal").style.display = "none";
  _resetAdminId = null;
}

document.addEventListener("click", function(e) {
  if (e.target && e.target.id === "resetAdminPasswordModal") closeResetAdminPasswordModal();
});

async function submitResetAdminPassword() {
  const pw  = document.getElementById("resetAdminPasswordInput").value;
  const err = document.getElementById("resetAdminPasswordErr");
  if (!pw || pw.length < 8) { err.textContent = "Password must be at least 8 characters."; err.style.display = "block"; return; }
  const btn = document.getElementById("submitResetAdminPasswordBtn");
  btn.disabled = true; btn.textContent = "Saving…";
  try {
    const r = await fetch(`${API}/admin/users/admins/reset-password`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({token: tok, admin_id: _resetAdminId, password: pw})
    });
    const d = await r.json();
    if (!r.ok) { err.textContent = d.detail || "Could not reset password."; err.style.display = "block"; return; }
    closeResetAdminPasswordModal();
    showToast("Admin password reset successfully.");
  } catch { err.textContent = "Could not connect."; err.style.display = "block"; }
  finally { btn.disabled = false; btn.textContent = "Reset Password"; }
}
