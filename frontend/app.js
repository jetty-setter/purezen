// app.js — entry point.
// Waits for the DOM, then delegates to PureZenUI (defined in script.js).

document.addEventListener("DOMContentLoaded", () => {
  if (!window.PureZenUI || typeof window.PureZenUI.init !== "function") {
    console.error("PureZenUI failed to initialize. Check that script.js loaded correctly.");
    return;
  }

  // script.js also calls init() on DOMContentLoaded as a fallback,
  // so this is a no-op if script.js already ran — init() is idempotent.
  window.PureZenUI.init();
});
