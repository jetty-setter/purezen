// script.js — UI logic for PureZen.
// Depends on: config.js (window.PUREZEN_CONFIG), api.js (window.PureZenAPI)

(function () {
  // ---------------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------------

  function generateUUID() {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
      var r = Math.random() * 16 | 0;
      return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
    });
  }
  let sessionId = generateUUID();
  let thinkingMessage = null;

  // DOM refs — populated in init()
  let chatForm        = null;
  let chatInput       = null;
  let chatMessages    = null;
  let servicesGrid    = null;
  let mobileMenuToggle = null;
  let mobileNav       = null;
  let startOverButton = null;
  let promptSuggestions = null;

  // ---------------------------------------------------------------------------
  // Constants
  // ---------------------------------------------------------------------------

  const DEFAULT_PROMPTS = [
    "Book a Swedish Massage tomorrow",
    "What facials do you offer?",
    "I need to reschedule my appointment",
    "Cancel my booking",
  ];

  const WELCOME_MESSAGE =
    "Welcome to PureZen. I can help you explore services, check availability, book an appointment, reschedule, or cancel.";

  // ---------------------------------------------------------------------------
  // Session helpers
  // ---------------------------------------------------------------------------

  function getSessionUser() {
    try {
      const token = localStorage.getItem("pz_token") || null;
      const raw   = localStorage.getItem("pz_user");
      const user  = raw ? JSON.parse(raw) : null;
      return { token, user };
    } catch {
      return { token: null, user: null };
    }
  }

  function buildContext() {
    const { token, user } = getSessionUser();
    return {
      user_token: token,
      user_name:  user ? user.name  : null,
      user_email: user ? user.email : null,
    };
  }

  // ---------------------------------------------------------------------------
  // Utility
  // ---------------------------------------------------------------------------

  function escapeHtml(value) {
    return String(value || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function formatMessageText(text) {
    return escapeHtml(text || "").replace(/\n/g, "<br>");
  }

  function formatCurrency(value) {
    if (typeof value === "number") return `$${value}`;
    const num = Number(value);
    if (typeof value === "string" && value.trim() && !Number.isNaN(num)) {
      return `$${num}`;
    }
    return "";
  }

  function normalizeServiceName(name) {
    return String(name || "Service")
      .trim()
      .replace(/\b\w/g, (c) => c.toUpperCase());
  }

  function extractBotText(data) {
    if (!data) return "Sorry, something unexpected happened.";
    if (typeof data === "string") return data;

    for (const key of ["response", "message", "reply"]) {
      if (typeof data[key] === "string" && data[key].trim()) {
        return data[key].trim();
      }
    }

    if (data.response && typeof data.response === "object") {
      for (const key of ["message", "response", "text"]) {
        if (typeof data.response[key] === "string" && data.response[key].trim()) {
          return data.response[key].trim();
        }
      }
      try { return JSON.stringify(data.response, null, 2); } catch { /* fall through */ }
    }

    try { return JSON.stringify(data, null, 2); } catch { /* fall through */ }
    return "Sorry, something unexpected happened.";
  }

  // ---------------------------------------------------------------------------
  // Chat DOM helpers
  // ---------------------------------------------------------------------------

  function scrollToBottom() {
    if (chatMessages) chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  function appendMessage(role, text) {
    if (!chatMessages) return null;

    const normalizedRole = role === "assistant" ? "bot" : role;
    const div = document.createElement("div");
    div.classList.add("chat-message", normalizedRole);

    const body = document.createElement("div");
    body.classList.add("chat-message-body");
    body.innerHTML = formatMessageText(text);

    div.appendChild(body);
    chatMessages.appendChild(div);
    scrollToBottom();
    return div;
  }

  function removeThinkingMessage() {
    thinkingMessage?.parentNode?.removeChild(thinkingMessage);
    thinkingMessage = null;
  }

  function showThinkingMessage() {
    if (!chatMessages) return;
    removeThinkingMessage();

    const div = document.createElement("div");
    div.classList.add("chat-message", "bot", "thinking");

    const body = document.createElement("div");
    body.classList.add("chat-message-body", "thinking-body");
    body.innerHTML = `
      <span class="thinking-label">PureZen Concierge is thinking</span>
      <span class="thinking-dots">
        <span>.</span><span>.</span><span>.</span>
      </span>
    `;

    div.appendChild(body);
    chatMessages.appendChild(div);
    thinkingMessage = div;
    scrollToBottom();
  }

  function setInputDisabled(disabled) {
    if (chatInput) chatInput.disabled = disabled;
    const submitBtn = chatForm?.querySelector('button[type="submit"]');
    if (submitBtn) submitBtn.disabled = disabled;
    if (startOverButton) startOverButton.disabled = disabled;
  }

  // ---------------------------------------------------------------------------
  // Send message — delegates fetch to api.js
  // ---------------------------------------------------------------------------

  async function sendMessage(message) {
    appendMessage("user", message);
    showThinkingMessage();
    setInputDisabled(true);

    try {
      const data = await window.PureZenAPI.sendChatMessage({
        sessionId,
        message,
        context: buildContext(),
      });

      if (data.session_id) sessionId = data.session_id;

      removeThinkingMessage();
      appendMessage("bot", extractBotText(data));
    } catch (error) {
      console.error("PureZen chat error:", error);
      removeThinkingMessage();
      appendMessage("bot", "I'm sorry, something went wrong while connecting to the concierge.");
    } finally {
      setInputDisabled(false);
      chatInput?.focus();
    }
  }

  // ---------------------------------------------------------------------------
  // Services rendering — delegates fetch to api.js
  // ---------------------------------------------------------------------------

  function renderServices(services) {
    if (!servicesGrid) return;

    if (!Array.isArray(services) || services.length === 0) {
      servicesGrid.innerHTML = `<div class="status-message">No services available right now.</div>`;
      return;
    }

    servicesGrid.innerHTML = services.map((service) => {
      const name         = normalizeServiceName(service.name);
      const description  = service.description || "";
      const duration     = service.duration_minutes ? `${service.duration_minutes} min` : "Time varies";
      const price        = formatCurrency(service.price);
      const category     = service.category || "Treatment";
      const roomType     = service.room_type || "";
      const consultation = service.requires_consultation
        ? "Consultation required"
        : "No consultation required";

      return `
        <article class="service-card">
          <div class="scard-cat">${escapeHtml(category)}</div>
          <h3 class="scard-name">${escapeHtml(name)}</h3>
          <p class="scard-desc">${escapeHtml(description)}</p>
          <div class="scard-foot">
            <span class="scard-duration">${escapeHtml(duration)} · ${escapeHtml(roomType || category)}</span>
            <span class="scard-price">${escapeHtml(price)}</span>
          </div>
        </article>
      `;
    }).join("");
  }

  async function loadServices() {
    if (!servicesGrid) return;
    servicesGrid.innerHTML = `<div class="status-message">Loading services...</div>`;

    try {
      const services = await window.PureZenAPI.fetchServices();
      renderServices(services);
    } catch (error) {
      console.error("PureZen services error:", error);
      servicesGrid.innerHTML = `<div class="status-message">We couldn't load the service menu right now.</div>`;
    }
  }

  // ---------------------------------------------------------------------------
  // Prompt suggestions
  // ---------------------------------------------------------------------------

  function renderSuggestedPrompts(prompts = DEFAULT_PROMPTS) {
    if (!promptSuggestions) return;

    if (!Array.isArray(prompts) || prompts.length === 0) {
      promptSuggestions.innerHTML = "";
      return;
    }

    promptSuggestions.innerHTML = prompts.map((prompt) => `
      <button class="prompt-chip" type="button" data-prompt="${escapeHtml(prompt)}">
        ${escapeHtml(prompt)}
      </button>
    `).join("");

    promptSuggestions.querySelectorAll(".prompt-chip").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const prompt = btn.getAttribute("data-prompt");
        if (prompt) await sendMessage(prompt);
      });
    });
  }

  // ---------------------------------------------------------------------------
  // Welcome message — single source of truth (no duplicate in HTML)
  // ---------------------------------------------------------------------------

  function ensureWelcomeMessage() {
    if (!chatMessages) return;
    if (chatMessages.querySelectorAll(".chat-message").length > 0) return;

    const { user } = getSessionUser();
    const greeting = user
      ? `Welcome back, ${user.name.split(" ")[0]}. I can help you explore services, check availability, book an appointment, reschedule, or cancel.`
      : WELCOME_MESSAGE;

    appendMessage("bot", greeting);
  }

  // ---------------------------------------------------------------------------
  // Reset
  // ---------------------------------------------------------------------------

  function resetChat() {
    sessionId = generateUUID();
    removeThinkingMessage();

    if (chatMessages) chatMessages.innerHTML = "";

    ensureWelcomeMessage();
    renderSuggestedPrompts();

    if (chatInput) {
      chatInput.value = "";
      chatInput.focus();
    }
  }

  // ---------------------------------------------------------------------------
  // Event binding
  // ---------------------------------------------------------------------------

  function bindChat() {
    if (!chatForm || !chatInput || !chatMessages) return;

    chatForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const message = chatInput.value.trim();
      if (!message) return;
      chatInput.value = "";
      await sendMessage(message);
    });
  }

  function bindMobileMenu() {
    if (!mobileMenuToggle || !mobileNav) return;

    mobileMenuToggle.addEventListener("click", () => {
      mobileNav.classList.toggle("open");
    });

    mobileNav.querySelectorAll("a").forEach((link) => {
      link.addEventListener("click", () => mobileNav.classList.remove("open"));
    });
  }

  function bindStartOver() {
    startOverButton?.addEventListener("click", resetChat);
  }

  // ---------------------------------------------------------------------------
  // Init — called by app.js (or directly on DOMContentLoaded)
  // ---------------------------------------------------------------------------

  function init() {
    chatForm          = document.getElementById("chat-form");
    chatInput         = document.getElementById("chat-input");
    chatMessages      = document.getElementById("chat-messages");
    servicesGrid      = document.getElementById("services-grid");
    mobileMenuToggle  = document.getElementById("mobileMenuToggle");
    mobileNav         = document.getElementById("mobileNav");
    startOverButton   = document.getElementById("startOverButton");
    promptSuggestions = document.getElementById("promptSuggestions");

    bindChat();
    bindMobileMenu();
    bindStartOver();
    ensureWelcomeMessage();
    renderSuggestedPrompts();
    loadServices();
  }

  // Expose init so app.js can call window.PureZenUI.init()
  window.PureZenUI = { init };

  // Auto-send message from URL param ?msg=...
  document.addEventListener("DOMContentLoaded", () => {
    const params = new URLSearchParams(window.location.search);
    const msg = params.get("msg");
    const goToConcierge = msg || params.get("concierge");
    if (goToConcierge) {
      const section = document.getElementById("concierge");
      if (section) {
        setTimeout(() => {
          const offset = section.getBoundingClientRect().top + window.scrollY - 80;
          window.scrollTo({ top: offset, behavior: "smooth" });
          if (msg) setTimeout(() => sendMessage(decodeURIComponent(msg)), 400);
        }, 200);
      }
    }
  });

  // Also self-init on DOMContentLoaded as a safe fallback
  document.addEventListener("DOMContentLoaded", init);
})();
