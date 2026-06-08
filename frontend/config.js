window.PUREZEN_CONFIG = {
  // FastAPI backend on AWS Lambda (API Gateway). Paths match the backend
  // routes directly (no /api proxy prefix like the old Vercel/Hetzner setup).
  API_BASE_URL: "https://aldr43obo5.execute-api.us-east-1.amazonaws.com/prod",
  CHAT_ENDPOINT:             "/chat",
  SERVICES_ENDPOINT:         "/services",
  HEALTH_ENDPOINT:           "/health",
  BOOKINGS_HISTORY_ENDPOINT: "/bookings/history",
  REQUEST_TIMEOUT_MS: 120000,
};
