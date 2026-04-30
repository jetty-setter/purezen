// api.js — low-level fetch helpers for the PureZen backend.
// Reads config from window.PUREZEN_CONFIG (set by config.js).
// No ES module imports — loaded as a plain <script> tag.

(function () {
  const CONFIG = window.PUREZEN_CONFIG || {};

  function buildUrl(path) {
    const base = CONFIG.API_BASE_URL || "";
    return base ? `${base}${path}` : path;
  }

  function withTimeout(promise, ms) {
    return Promise.race([
      promise,
      new Promise((_, reject) =>
        setTimeout(() => reject(new Error("Request timed out")), ms)
      ),
    ]);
  }

  /**
   * Send a chat message to the backend.
   * @param {{ sessionId?: string, message: string, context?: object }} options
   * @returns {Promise<object>}
   */
  async function sendChatMessage({ sessionId, message, context = {} }) {
    const url = buildUrl(CONFIG.CHAT_ENDPOINT || "/chat");
    const timeoutMs = CONFIG.REQUEST_TIMEOUT_MS || 30000;

    const payload = { message, context };
    if (sessionId) payload.session_id = sessionId;

    const response = await withTimeout(
      fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }),
      timeoutMs
    );

    if (!response.ok) {
      const errorText = await response.text().catch(() => "");
      throw new Error(`Chat request failed (${response.status}): ${errorText}`);
    }

    return response.json();
  }

  /**
   * Fetch the list of active services.
   * @returns {Promise<Array>}
   */
  async function fetchServices() {
    const url = buildUrl(CONFIG.SERVICES_ENDPOINT || "/services");
    const timeoutMs = CONFIG.REQUEST_TIMEOUT_MS || 30000;

    const response = await withTimeout(
      fetch(url, { method: "GET", headers: { Accept: "application/json" } }),
      timeoutMs
    );

    if (!response.ok) {
      throw new Error(`Services request failed (${response.status})`);
    }

    const data = await response.json();
    return Array.isArray(data) ? data : data.services || [];
  }

  // Expose on window so script.js (and any future modules) can use these.
  window.PureZenAPI = { sendChatMessage, fetchServices };
})();
