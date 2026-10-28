const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { createHash, webcrypto } = require("node:crypto");
const { JSDOM, VirtualConsole } = require("jsdom");

const root = path.resolve(__dirname, "..");
const id = number => `00000000-0000-4000-8000-${String(number).padStart(12, "0")}`;
const wav = Buffer.from("RIFF\x04\x00\x00\x00WAVEtest", "binary");
const wavHash = createHash("sha256").update(wav).digest("hex");
const expiry = () => new Date(Date.now() + 3600000).toISOString();
const levels = ["beginner", "intermediate", "advanced"];
const clone = object => JSON.parse(JSON.stringify(object));

function bundle(jobId = id(10), title = "A real generated course", documentId = id(1)) {
  const variants = {};
  for (const [levelIndex, level] of levels.entries()) {
    variants[level] = {
      job_id: jobId, course_id: "client-course", activity_id: "client-activity", lesson_id: title,
      language: "en", voice: "Joanna", learner_level: level, generation_version: "a3-v2",
      status: "DRAFT", expires_at: expiry(),
      reviewer_report: { approved: false, summary: "Review is required.", issues: [{ message: "Check this explanation." }] },
      chunks: [0, 1].map(index => ({
        chunk_id: id(Number(jobId.slice(-12)) * 100 + levelIndex * 10 + index), index,
        text: `${level} part ${index + 1}\n\nAn explanation from the backend’s source material.`,
        duration_seconds: 120 + index, audio_sha256: wavHash,
        sources: [{ document_id: documentId, pages: [index + 1], source_chunk_ids: [] }],
        reviewer_report: { approved: false, summary: "Please review this part.", issues: [] },
      })),
    };
  }
  return { job_id: jobId, variants, generation_version: "a3-v2", expires_at: expiry() };
}

class Backend {
  constructor() {
    this.mode = "local-test";
    this.documents = [{ document_id: id(1), filename: "Client source.pdf", course_id: "client-course", activity_id: "client-activity", language: "en", status: "DONE", progress: 1, chunk_count: 4 }];
    this.bundles = new Map([[id(10), bundle()]]);
    this.hashes = new Map([[id(10), "a".repeat(64)]]);
    this.jobs = new Map(); this.sessions = new Map(); this.calls = []; this.failures = [];
    this.corruptAudio = false; this.holdQuestion = false; this.pendingQuestion = null;
    this.nextReplyAction = null; this.expiredSession = false;
  }
  failNext(method, path, status = 503, detail = "BACKEND_TIMEOUT") { this.failures.push({ method, path, status, detail }); }
  records(method, path) { return this.calls.filter(call => call.method === method && call.path === path); }
  response(data, status = 200, type = "application/json") {
    const headers = { "Content-Type": type, "X-UPRAISE-Auth-Mode": this.mode };
    return new Response(status === 204 ? null : type === "audio/wav" ? data : JSON.stringify(data), { status, headers });
  }
  catalog() {
    return [...this.bundles.values()].flatMap(value => Object.values(value.variants).map(lesson => ({
      job_id: lesson.job_id, lesson_id: lesson.lesson_id, course_id: lesson.course_id, activity_id: lesson.activity_id,
      language: lesson.language, voice: lesson.voice, learner_level: lesson.learner_level, chunk_count: lesson.chunks.length,
      generation_version: lesson.generation_version, status: "DRAFT", expires_at: lesson.expires_at,
      teaching_version: lesson.versions?.teaching_version || "v1", teaching_policy: lesson.versions?.teaching_policy,
      created_at: new Date().toISOString(), requires_review: true,
    })));
  }
  reviewText(value) {
    const variants = Object.fromEntries(Object.entries(value.variants).map(([level, lesson]) => [level, {
      learner_level: level,
      chunks: lesson.chunks.map(({ chunk_id, index, text, sources, reviewer_report, narration_text }) => ({ chunk_id, index, text, sources, reviewer_report, narration_text })),
    }]));
    const first = Object.values(value.variants)[0];
    const available = !first.expires_at || Date.parse(first.expires_at) > Date.now();
    return { job_id: value.job_id, lesson_id: first.lesson_id, course_id: first.course_id, activity_id: first.activity_id,
      language: first.language, voice: first.voice, teaching_version: first.versions?.teaching_version || "v1", status: "DRAFT", job_status: "DONE", levels: Object.keys(variants),
      chunk_counts: Object.fromEntries(Object.entries(variants).map(([level, lesson]) => [level, lesson.chunks.length])),
      created_at: new Date().toISOString(), audio_expires_at: first.expires_at,
      revision_available: available, revision_unavailable_reason: available ? null : "LESSON_OUTPUT_EXPIRED", variants };
  }
  async fetch(url, options = {}) {
    const parsed = new URL(url, "http://127.0.0.1:8502");
    const route = parsed.pathname.replace(/^\/api/, "");
    const call = { method: options.method || "GET", path: route, query: parsed.searchParams,
      headers: new Headers(options.headers), rawBody: options.body, body: typeof options.body === "string" ? JSON.parse(options.body) : null };
    if (route.endsWith("/interrupts") && options.body?.get) call.body = Object.fromEntries([...options.body.entries()].filter(([key]) => key !== "audio"));
    this.calls.push(call);
    const failed = this.failures.findIndex(item => item.method === call.method && item.path === route);
    if (failed >= 0) { const failure = this.failures.splice(failed, 1)[0]; return this.response({ detail: failure.detail }, failure.status); }
    if (this.intercept) { const intercepted = this.intercept(call); if (intercepted !== undefined) return intercepted; }
    if (route === "/config") return this.response({ ui_nonce: "test-ui-nonce", backend_url: "http://127.0.0.1:8000", activity_id: "client-activity", levels });
    if (call.method === "GET" && route === "/v1/documents") return this.response(clone(this.documents));
    if (call.method === "POST" && route === "/v1/documents") {
      const body = options.body;
      const documentId = id(2);
      this.documents.unshift({ document_id: documentId, filename: body.get("file").name, course_id: body.get("course_id"), activity_id: body.get("activity_id"), language: body.get("language"), status: "QUEUED", progress: 0, chunk_count: 0 });
      return this.response({ document_id: documentId, job_id: id(3) }, 202);
    }
    if (call.method === "DELETE" && route.startsWith("/v1/documents/")) {
      const documentId = route.split("/").pop();
      this.documents = this.documents.filter(item => item.document_id !== documentId);
      for (const [key, value] of this.bundles) if (Object.values(value.variants)[0].chunks.some(chunk => chunk.sources.some(source => source.document_id === documentId))) this.bundles.delete(key);
      return this.response(null, 204);
    }
    if (call.method === "GET" && route === "/v1/lesson-jobs") {
      const offset = Number(parsed.searchParams.get("offset") || 0), limit = Number(parsed.searchParams.get("limit") || 100);
      return this.response(this.catalog().slice(offset, offset + limit));
    }
    if (call.method === "GET" && route === "/v1/lesson-reviews") {
      const offset = Number(parsed.searchParams.get("offset") || 0), limit = Number(parsed.searchParams.get("limit") || 100);
      return this.response([...this.bundles.values()].map(value => {
        const { variants, ...summary } = this.reviewText(value); return summary;
      }).slice(offset, offset + limit));
    }
    const reviewMatch = route.match(/^\/v1\/lesson-reviews\/([^/]+)$/);
    if (call.method === "GET" && reviewMatch) {
      const value = this.bundles.get(reviewMatch[1]);
      return value ? this.response(this.reviewText(value)) : this.response({ detail: "JOB_NOT_FOUND" }, 404);
    }
    if (call.method === "POST" && route === "/v2/lesson-jobs") {
      const jobId = id(20);
      this.jobs.set(jobId, { statusReads: 0, type: "generation", input: call.body });
      return this.response({ job_id: jobId }, 202);
    }
    if (call.method === "POST" && route === "/v1/audio:regenerate") {
      const jobId = id(30);
      this.jobs.set(jobId, { statusReads: 0, type: "revision", input: call.body });
      return this.response({ job_id: jobId }, 202);
    }
    const resultMatch = route.match(/^\/v1\/jobs\/([^/]+)\/result$/);
    if (call.method === "GET" && resultMatch) {
      const value = this.bundles.get(resultMatch[1]);
      if (!value) return this.response({ detail: "RESULT_EXPIRED" }, 410);
      const level = parsed.searchParams.get("learner_level");
      return this.response(clone(level ? value.variants[level] : value));
    }
    if (call.method === "GET" && /\/audio\//.test(route)) return this.response(this.corruptAudio ? Buffer.from("corrupt") : wav, 200, "audio/wav");
    const jobMatch = route.match(/^\/v1\/jobs\/([^/]+)$/);
    if (jobMatch) {
      const jobId = jobMatch[1], job = this.jobs.get(jobId);
      if (call.method === "DELETE") { if (job) job.cancelled = true; return this.response(null, 204); }
      let status = "DONE";
      if (job) {
        job.statusReads++;
        status = job.cancelled ? "CANCELLED" : job.statusReads > 1 ? "DONE" : "RUNNING";
        if (status === "DONE" && !job.published) {
          job.published = true;
          if (job.type === "generation") {
            const value = bundle(jobId, job.input.lesson_id, job.input.document_ids[0]);
            value.variants = Object.fromEntries(Object.entries(value.variants).filter(([level]) => !job.input.levels || job.input.levels.includes(level)));
            for (const lesson of Object.values(value.variants)) Object.assign(lesson, { language: job.input.language, voice: job.input.voice });
            if (["v2", "v2.9", "v3"].includes(job.input.teaching_version)) for (const lesson of Object.values(value.variants)) lesson.versions = { teaching_version: job.input.teaching_version };
            this.bundles.set(jobId, value); this.hashes.set(jobId, "c".repeat(64));
          } else {
            const value = this.bundles.get(job.input.lesson_job_id);
            const selected = Object.values(value.variants).flatMap(lesson => lesson.chunks).find(chunk => chunk.chunk_id === job.input.chunk_id);
            for (const lesson of Object.values(value.variants)) for (const chunk of lesson.chunks) {
              if (job.input.apply_to_all_levels && chunk.index === selected.index || chunk.chunk_id === job.input.chunk_id) chunk.text = "Updated explanation after revision.";
            }
            this.hashes.set(job.input.lesson_job_id, "b".repeat(64));
          }
        }
      }
      return this.response({ job_id: jobId, status, step: status === "DONE" ? "DONE" : "GENERATE", progress: status === "DONE" ? 1 : .35, error: null, result: { result_sha256: this.hashes.get(jobId) || "a".repeat(64) }, acknowledged: false, cleanup_pending: false });
    }
    if (call.method === "POST" && route === "/v1/sessions") {
      const selected = this.bundles.get(call.body.lesson_job_id)?.variants[call.body.learner_level || "intermediate"];
      if (!selected) return this.response({ detail: "NOT_FOUND" }, 404);
      const session = { session_id: call.body.session_id || id(40), lesson_job_id: selected.job_id,
        lesson_id: selected.lesson_id, course_id: selected.course_id, activity_id: selected.activity_id,
        document_ids: [id(1)], language: selected.language, learner_level: selected.learner_level,
        voice: selected.voice, result_sha256: this.hashes.get(selected.job_id),
        preview: this.mode === "local-test", ai_notice: "You are interacting with an AI teacher.",
        expires_at: this.expiredSession ? new Date(Date.now() - 1000).toISOString() : expiry() };
      this.sessions.set(session.session_id, session); return this.response(session, 201);
    }
    if (call.method === "POST" && route.endsWith("/interrupts")) {
      const reply = { request_id: call.body.request_id, chunk_id: call.body.chunk_id, text: "A real teacher response from the backend.", audio: { encoding: "base64", media_type: "audio/wav", data: wav.toString("base64") }, grounded: null, sources: [], warnings: ["GROUNDING_NOT_VERIFIED"], playback_action: this.nextReplyAction };
      if (this.holdQuestion) return new Promise(resolve => { this.pendingQuestion = () => resolve(this.response(reply)); });
      return this.response(reply);
    }
    if (call.method === "POST" && route.endsWith("/end")) return this.response(null, 204);
    throw new Error(`Unexpected request ${call.method} ${route}`);
  }
}

async function harness(configure = () => {}) {
  const backend = new Backend(); configure(backend);
  const errors = [], console = new VirtualConsole();
  console.on("jsdomError", error => errors.push(error.message));
  const dom = new JSDOM(fs.readFileSync(path.join(root, "index.html"), "utf8"), { url: "http://127.0.0.1:8502/", runScripts: "outside-only", pretendToBeVisual: true, virtualConsole: console });
  const { window } = dom, { document } = window;
  window.fetch = (...args) => backend.fetch(...args);
  window.Headers = Headers;
  const microphone = { requests: 0, streams: [], contexts: [], nodes: [], sampleRate: 48000, error: null, hold: false };
  Object.defineProperty(window, "isSecureContext", { value: true, configurable: true });
  Object.defineProperty(window.navigator, "mediaDevices", { value: { getUserMedia: async () => {
    microphone.requests++;
    if (microphone.error) throw microphone.error;
    if (microphone.hold) await new Promise(resolve => { microphone.grant = resolve; });
    const track = { stopped: false, onended: null, stop() { this.stopped = true; }, end() { this.onended?.(); } };
    const stream = { getTracks: () => [track] }; microphone.streams.push(stream); return stream;
  } }, configurable: true });
  window.AudioContext = class {
    constructor() {
      this.sampleRate = microphone.sampleRate; this.state = "suspended"; this.destination = {};
      this.audioWorklet = { addModule: async name => {
        assertWorkletPath(name);
        vm.runInNewContext(fs.readFileSync(path.join(root, name), "utf8"), {
          sampleRate: this.sampleRate, Float32Array,
          AudioWorkletProcessor: class { constructor() { this.port = { postMessage() {} }; } },
          registerProcessor: (name, Processor) => { this.Processor = Processor; },
        });
      } };
      microphone.contexts.push(this);
    }
    async resume() { this.state = "running"; }
    async close() { this.state = "closed"; }
    createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
  };
  function assertWorkletPath(name) { if (name !== "./voice-capture-worklet.js") throw new Error("Unexpected audio module"); }
  window.AudioWorkletNode = class {
    constructor(context, name, options) {
      this.context = context; this.processor = new context.Processor(options); this.disconnected = false;
      this.port = { onmessage: null, close() {}, postMessage: data => queueMicrotask(() => this.processor.port.onmessage({ data })) };
      this.processor.port.postMessage = data => queueMicrotask(() => this.port.onmessage?.({ data }));
      microphone.nodes.push(this);
    }
    connect() {}
    disconnect() { this.disconnected = true; }
  };
  microphone.feed = (seconds, values = [.5]) => {
    const node = microphone.nodes.at(-1), total = Math.round(seconds * node.context.sampleRate);
    for (let offset = 0; offset < total; offset += 128) {
      const channels = values.map(value => new Float32Array(Math.min(128, total - offset)).fill(value));
      if (!node.processor.process([channels])) break;
    }
  };
  Object.defineProperty(window.crypto, "subtle", { value: webcrypto.subtle });
  window.scrollTo = () => {};
  window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  window.HTMLDialogElement.prototype.close = function () { this.open = false; queueMicrotask(() => this.dispatchEvent(new window.Event("close"))); };
  window.addEventListener("error", event => errors.push(event.error?.stack || event.message));
  const urls = new Set(), audio = [], timers = new Map(); let timerId = 0, urlId = 0;
  window.URL.createObjectURL = () => { const url = `blob:http://127.0.0.1:8502/${++urlId}`; urls.add(url); return url; };
  window.URL.revokeObjectURL = value => urls.delete(value);
  window.Audio = class extends window.EventTarget {
    constructor() { super(); this.paused = true; this.ended = false; this.currentTime = 0; this.playbackRate = 1; this.src = ""; audio.push(this); }
    pause() { this.paused = true; this.dispatchEvent(new window.Event("pause")); }
    play() { this.paused = false; this.ended = false; this.dispatchEvent(new window.Event("play")); return Promise.resolve(); }
    load() { this.ended = false; if (this.src) queueMicrotask(() => this.dispatchEvent(new window.Event("loadedmetadata"))); }
    removeAttribute(name) { if (name === "src") this.src = ""; }
    advance(value) { this.currentTime = value; this.dispatchEvent(new window.Event("timeupdate")); }
    finish() { this.paused = true; this.ended = true; this.dispatchEvent(new window.Event("ended")); }
  };
  window.setTimeout = (callback, delay) => { const key = ++timerId; timers.set(key, { callback, delay }); return key; };
  window.clearTimeout = key => timers.delete(key);
  const find = selector => { const element = document.querySelector(selector); if (!element) throw new Error(`Missing ${selector}; page: ${document.querySelector("main").textContent.slice(-800)}`); return element; };
  const click = selector => find(selector).click();
  const input = (selector, value, type = "input") => { const element = find(selector); element.value = value; element.dispatchEvent(new window.Event(type, { bubbles: true })); };
  const submit = selector => find(selector).dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  async function settle() {
    for (let i = 0; i < 20; i++) {
      for (const [key, task] of [...timers]) if (task.delay <= 1 && timers.has(key)) { timers.delete(key); await task.callback(); }
      await new Promise(resolve => setImmediate(resolve));
    }
    if (errors.length) throw new Error(errors.join("\n"));
  }
  async function waitFor(predicate) {
    for (let i = 0; i < 100; i++) { await settle(); if (predicate()) return; await new Promise(resolve => setTimeout(resolve, 5)); }
    throw new Error(`UI did not settle: ${document.querySelector("main").textContent.slice(-1000)}`);
  }
  async function navigate(page) { window.location.hash = page; window.dispatchEvent(new window.HashChangeEvent("hashchange")); await settle(); }
  async function runTimers(delay) { for (const [key, task] of [...timers]) if (task.delay === delay && timers.has(key)) { timers.delete(key); await task.callback(); } await settle(); }
  function chooseFile(name, contents = "source bytes") {
    const file = new window.File([contents], name, { type: "application/pdf" });
    file.arrayBuffer = async () => Uint8Array.from(Buffer.from(contents)).buffer;
    Object.defineProperty(find("#file-input"), "files", { value: [file], configurable: true });
    find("#file-input").dispatchEvent(new window.Event("change", { bubbles: true }));
    return file;
  }
  async function start(jobId = id(10), level = "intermediate", token = "") {
    await navigate("learn"); click(`[data-action="start-course"][data-id="${jobId}"]`);
    await waitFor(() => Boolean(document.querySelector("#start-form")));
    find(`input[name="level"][value="${level}"]`).checked = true;
    if (token) input("#session-jwt", token);
    submit("#start-form");
    await waitFor(() => Boolean(document.querySelector(".lesson-card")) && !find('[data-action="toggle-play"]').disabled);
  }
  const readBlob = blob => new Promise((resolve, reject) => {
    const reader = new window.FileReader(); reader.onload = () => resolve(Buffer.from(reader.result)); reader.onerror = reject; reader.readAsArrayBuffer(blob);
  });
  for (const name of ["api.js", "ui.js", "voice.js", "app.js"]) window.eval(fs.readFileSync(path.join(root, name), "utf8"));
  const style = document.createElement("style"); style.textContent = fs.readFileSync(path.join(root, "styles.css"), "utf8"); document.head.appendChild(style);
  await waitFor(() => Boolean(document.querySelector("#upload-form")));
  return { backend, window, document, errors, audio, urls, timers, microphone, readBlob, find, click, input, submit, navigate, settle, waitFor, runTimers, chooseFile, start, close: () => dom.window.close() };
}

module.exports = { harness, id, wavHash, bundle, Backend };
