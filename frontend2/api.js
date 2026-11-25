/* Same-origin HTTP only. Service credentials belong to serve.py, never this page. */
(() => {
  "use strict";
  let config;
  const messages = {
    BACKEND_UNAVAILABLE: "The backend could not be reached. Check that it is running, then refresh the workspace.",
    BACKEND_TOKEN_UNAVAILABLE: "The backend could not issue a JWT. Check the server's configured client credentials.",
    BACKEND_TIMEOUT: "The backend took too long to respond. Your request may already have been accepted.",
    BACKEND_REDIRECT_BLOCKED: "The backend redirected this request. Check the configured gateway address.",
    BACKEND_RESPONSE_INVALID: "The gateway returned an unexpected response. Check the backend address and authentication.",
    INGRESS_NOT_CONFIGURED: "Backend authentication is not configured. Set up the gateway, or use the documented local development mode.",
    UI_SESSION_REQUIRED: "The UPRAISE server restarted. Reload this page to reconnect.",
    UNTRUSTED_ORIGIN: "Open UPRAISE directly at its local address to continue.",
    INVALID_SESSION: "This learning session is no longer available. Start the course again.",
    SESSION_EXPIRED: "This learning session has expired. Start the course again.",
    INTERRUPT_RETRY_EXPIRED: "This question can no longer be retried. Send it again as a new question.",
    INTERRUPT_IN_PROGRESS: "Your teacher is still working on this question. Use Retry question in a moment.",
    QUESTIONS_CONSENT_REQUIRED: "Questions are not enabled for this session. Start a new session with questions enabled.",
    INTERRUPT_AUDIO_TOO_LARGE: "The voice recording is too large. Record a shorter question.",
    INTERRUPT_AUDIO_TOO_LONG: "The voice recording is too long. Record a question of up to 60 seconds.",
    INTERRUPT_AUDIO_FORMAT_UNSUPPORTED: "This recording format could not be accepted. Record your question again, or type it.",
    INTERRUPT_STT_UNAVAILABLE: "Speech recognition is temporarily unavailable. Retry the same recording, or discard it and type your question.",
    INTERRUPT_STT_LANGUAGE_UNSUPPORTED: "Voice questions are not available in this course language. You can type your question instead.",
    INTERRUPT_TRANSCRIPT_INVALID: "Speech could not be understood. Discard the recording and try again, or type your question.",
    SLIDE_PREVIEW_UNAVAILABLE: "The original slide preview is unavailable. The retained course text is still readable.",
    POWERPOINT_CONVERSION_FAILED: "The PowerPoint could not be rendered. Check the file and try again.",
    POWERPOINT_INVALID_DOCUMENT: "This file is not a valid .pptx presentation.",
    POWERPOINT_INGESTION_REVIEW_REQUIRED: "This PowerPoint has incomplete or unchecked content. Choose Generate from the available content to continue with a draft, or upload it again for fresh checks.",
    POWERPOINT_TOO_MANY_SLIDES: "This presentation exceeds the configured slide limit.",
    POWERPOINT_TOO_LARGE: "The presentation is too large. Upload a smaller PowerPoint file.",
    POWERPOINT_ENCRYPTED_UNSUPPORTED: "Password-protected presentations are not supported. Upload an unprotected .pptx copy.",
    POWERPOINT_MACROS_UNSUPPORTED: "Upload a .pptx copy without macros.",
    POWERPOINT_EXTERNAL_CONTENT_UNSUPPORTED: "This presentation links to external files. Embed its images or data, then upload it again.",
    POWERPOINT_CONVERSION_TIMEOUT: "The presentation took too long to render. Try splitting it into smaller decks.",
  };

  class APIError extends Error {
    constructor(status, body) {
      const detail = typeof body?.detail === "string" ? body.detail : body?.detail?.code || body?.code || "";
      let message = messages[detail];
      if (!message && status === 422) {
        const first = Array.isArray(body?.detail) ? body.detail[0] : null;
        const field = first?.loc?.filter(value => value !== "body").join(" · ");
        message = field ? `Check ${field}: ${first.msg || "the value is invalid"}.` : "Some values could not be accepted. Check the form and try again.";
      }
      if (!message) message = ({
        401: "Your backend connection is not authenticated. Check the server’s gateway credentials.",
        403: "This action is not allowed for the current connection or course.",
        404: "This item is unavailable or is not accessible with the current connection.",
        409: "This item changed or is not ready for this action. Refresh it and try again.",
        410: "This course or session has expired. Choose another available course.",
        413: "The selected document is too large. Choose a file up to 100 MB.",
        429: "The service is busy. Wait a moment before trying again.",
        503: "The service is temporarily unavailable. Refresh the status or retry the same request.",
      })[status] || "The request could not be completed. Please try again.";
      super(message);
      this.name = "APIError";
      this.status = status;
      this.code = detail;
      this.body = body;
      this.uncertain = !status || status >= 500;
    }
  }

  async function request(path, options = {}) {
    if (!path.startsWith("/") || path.startsWith("//")) throw new Error("Invalid API path.");
    const headers = new Headers(options.headers || {});
    if (config) headers.set("X-UPRAISE-UI", config.ui_nonce);
    let body = options.body;
    if (options.json !== undefined) { headers.set("Content-Type", "application/json"); body = JSON.stringify(options.json); }
    const controller = new AbortController();
    const forwardAbort = () => controller.abort();
    options.signal?.addEventListener("abort", forwardAbort, { once: true });
    if (options.signal?.aborted) controller.abort();
    const timer = setTimeout(() => controller.abort(), options.timeout || 195000);
    try {
      const response = await fetch("/api" + path, { method: options.method || "GET", body, headers,
        credentials: "same-origin", cache: "no-store", redirect: "error", signal: controller.signal,
        keepalive: options.keepalive || false });
      const mode = response.headers.get("X-UPRAISE-Auth-Mode");
      if (mode) window.dispatchEvent(new CustomEvent("upraise-auth-mode", { detail: mode }));
      if (!response.ok) {
        let errorBody;
        try { errorBody = await response.json(); } catch { errorBody = {}; }
        if (response.status === 401 && errorBody?.detail === "DEMO_LOGIN_REQUIRED") window.location.replace("/login");
        throw new APIError(response.status, errorBody);
      }
      if (response.status === 204) return null;
      if (options.binary) {
        if (response.headers.get("Content-Type")?.split(";")[0] !== (options.mediaType || "audio/wav")) throw new Error("The media response was invalid.");
        return await response.arrayBuffer();
      }
      return await response.json();
    } catch (error) {
      if (error instanceof APIError) throw error;
      if (options.signal?.aborted) throw new DOMException("Request cancelled", "AbortError");
      throw new APIError(0, { detail: error.name === "AbortError" ? "BACKEND_TIMEOUT" : "BACKEND_UNAVAILABLE" });
    } finally {
      clearTimeout(timer);
      options.signal?.removeEventListener("abort", forwardAbort);
    }
  }

  async function initialize() {
    config = await request("/config", { timeout: 10000 });
    if (!config?.ui_nonce) throw new Error("The UPRAISE connection could not be initialized.");
    return config;
  }

  async function sha256(bytes) {
    if (!crypto.subtle) throw new Error("Open UPRAISE at localhost or over HTTPS to verify files and audio.");
    return [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))].map(byte => byte.toString(16).padStart(2, "0")).join("");
  }

  async function verifiedAudio(jobId, chunk, options = {}) {
    const bytes = await request(`/v1/jobs/${encodeURIComponent(jobId)}/audio/${encodeURIComponent(chunk.chunk_id)}`, { ...options, binary: true });
    if (!/^[a-f0-9]{64}$/i.test(chunk.audio_sha256 || "") || await sha256(bytes) !== chunk.audio_sha256.toLowerCase()) {
      throw new Error("Audio verification failed. Refresh this course before playing it.");
    }
    return new Blob([bytes], { type: "audio/wav" });
  }

  window.UpraiseAPI = Object.freeze({ request, initialize, sha256, verifiedAudio, APIError });
})();
