/* UPRAISE: real authoring and learning through the existing backend contracts. */
(() => {
  "use strict";
  const api = window.UpraiseAPI;
  const { icon, escape, art } = window.UpraiseUI;
  const main = document.querySelector("#main");
  const modal = document.querySelector("#modal");
  const lectureAudio = new Audio();
  const replyAudio = new Audio();
  const languages = { en: "English", it: "Italian" };
  const levelDescriptions = {
    beginner: "Start with the essentials. Clear language and step-by-step explanations.",
    intermediate: "Build on what you know. A balance of practical ideas and detail.",
    advanced: "Go deeper with precise concepts and technical detail.",
  };
  const terminal = new Set(["DONE", "FAILED", "CANCELLED"]);
  const uuidPattern = /^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/i;
  const state = {
    config: null, local: false, connected: false, loading: true, refreshing: false,
    materials: [], materialError: "", catalogItems: [], courses: [], catalogError: "", more: false,
    selectedFile: null, selectedMaterial: null, uploadName: "", uploadLanguage: "en", activityId: "",
    uploadBusy: false, uploadError: "", uploadAttempt: null, uploadJobs: new Map(),
    draft: { title: "", language: "en", voice: "Joanna", words: 200, window: 5, maximum: 500, levels: null, teachingVersion: "v1" },
    generationBusy: false, generationError: "", generationAttempt: null, activeJobId: null,
    jobs: new Map(), bundles: new Map(), generatedId: null, resultLevel: "", resultPart: 0,
    search: "", courseSearch: "", page: "materials", player: null, playerBusy: false,
    question: "", questionOpen: false, questionPending: false, questionOffset: 0, questionHistory: [], turn: null,
    startAttempt: null, startPending: null, sessionToken: "", questionsConsent: true,
    deletion: null, revision: null, revisionUncertain: new Set(),
    reviewCourses: [], reviewError: "", reviewMore: false, reviewSearch: "", reviewEditor: null,
    reviewDrafts: new Map(),
  };
  let toastTimer;
  let pollTimer;
  let polling = false;
  let modalTrigger;
  let modalEpoch = 0;
  let questionController;
  let playerEpoch = 0;
  let lectureUrl;
  let replyUrl;
  let replyPlaybackEpoch = 0;
  let expiryTimer;
  let reviewAudioUrl = null, reviewAudioEpoch = 0;
  let workspaceEpoch = 0;

  function badge(text, type = "", symbol = "") { return `<span class="badge ${type}">${symbol ? icon(symbol) : ""}${escape(text)}</span>`; }
  function levelLabel(level) { return String(level).replace(/_/g, " ").replace(/^./, char => char.toUpperCase()); }
  function languageLabel(code) { return languages[code] || languages[String(code).split("-")[0]] || code; }
  function time(seconds = 0) { return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`; }
  function percent(progress) { return Math.max(0, Math.min(100, Number(progress || 0) * 100)); }
  function expired(value) { return Boolean(value) && (!Number.isFinite(Date.parse(value)) || Date.parse(value) <= Date.now()); }
  function getMaterial(id = state.selectedMaterial) { return state.materials.find(item => item.document_id === id); }
  function getCourse(id) { return state.courses.find(item => item.id === id) || (state.bundles.has(id) ? summaryFromBundle(state.bundles.get(id)) : null); }
  function levelsFor(course) { return Object.keys(course?.variants || {}); }
  function teachingVersionLabel(course) {
    const records = Object.values(course.variants || {});
    if (!records.length) records.push(course);
    const version = records[0].teaching_version;
    if (!["v1", "v2", "v3"].includes(version) || records.some(item => item.teaching_version !== version)) return "Version unknown";
    const policy = records[0].teaching_policy;
    const revision = records.every(item => item.teaching_policy === policy) && /^teaching-narration-v(2\.\d+)$/.exec(policy || "");
    return `Generation ${version === "v2" && revision ? `V${revision[1]}` : version.toUpperCase()}`;
  }
  function variantsOf(bundle) { return bundle?.variants || (bundle?.learner_level ? { [bundle.learner_level]: bundle } : {}); }
  function configuredLevels() { return state.config?.levels || []; }
  function selectedGenerationLevels() { return state.draft.levels ?? configuredLevels(); }
  function levelChips(levels = configuredLevels()) { return `<div class="level-chips">${levels.map(level => `<span class="level-chip">${icon("check")}${escape(levelLabel(level))}</span>`).join("")}</div>`; }
  function languageOptions(selected) {
    const choices = { ...languages };
    if (selected && !choices[selected]) choices[selected] = languageLabel(selected);
    return Object.entries(choices).map(([value, label]) => `<option value="${escape(value)}" ${value === selected ? "selected" : ""}>${escape(label)}</option>`).join("");
  }
  function fileIcon(material) {
    const format = String(material.filename || "").split(".").pop().toUpperCase();
    const safeFormat = ["PDF", "DOCX", "DOC", "CSV"].includes(format) ? format : "FILE";
    return `<span class="file-icon ${safeFormat.toLowerCase()}">${safeFormat}</span>`;
  }
  function errorBox(message, action = "", label = "Try again") {
    return `<div class="error-banner" role="alert">${icon("info")}<div><p>${escape(message)}</p>${action ? `<button class="text-button" data-action="${action}">${escape(label)}</button>` : ""}</div></div>`;
  }
  function toast(message) {
    clearTimeout(toastTimer);
    const element = document.querySelector("#toast"); element.textContent = message; element.hidden = false;
    toastTimer = setTimeout(() => { element.hidden = true; }, 6500);
  }
  function openModal(content) {
    ++modalEpoch;
    if (!modal.open) {
      modalTrigger = document.activeElement; modal.innerHTML = content; modal.showModal();
    } else {
      modal.innerHTML = content;
      modal.querySelector("button, input, select, textarea")?.focus();
    }
  }
  function modalHeader(title, eyebrow = "UPRAISE") {
    return `<div class="modal-top"><div><p class="eyebrow">${escape(eyebrow)}</p><h2 id="modal-title">${escape(title)}</h2></div><button class="icon-button" data-action="close-modal" aria-label="Close dialog">${icon("close")}</button></div>`;
  }
  function intro(step, label, title, copy, counts = false) {
    const levels = new Set(state.catalogItems.map(item => item.learner_level));
    return `<div class="page-intro"><div><p class="eyebrow">${step} <span>/</span> ${label}</p><h1>${title}</h1><p class="intro-copy">${copy}</p></div>${counts ? `<dl class="intro-count"><div><dd>${state.materials.length}</dd><dt>Materials</dt></div><div><dd>${state.courses.length}</dd><dt>Available courses</dt></div><div><dd>${levels.size || configuredLevels().length}</dd><dt>Learning levels</dt></div></dl>` : ""}</div>`;
  }
  function navigate(page) { if (location.hash === `#${page}`) render(); else location.hash = page; }
  function selectedSource(id) {
    const material = getMaterial(id);
    if (!material) return;
    if (state.selectedMaterial !== id) {
      state.selectedMaterial = id;
      state.draft.title = material.filename.replace(/\.[^.]+$/, "").replace(/[_-]/g, " ").slice(0, 120);
      const language = material.language.split("-")[0];
      state.draft.language = language in languages ? language : "en";
      state.draft.voice = language === "it" ? "Bianca" : "Joanna";
      state.generationAttempt = null; state.generationError = "";
    }
  }
  function updateConnection() {
    document.querySelector("#connection-label").textContent = state.loading ? "Connecting…" : state.connected ? "Backend connected" : "Connection needs attention";
    document.querySelector("#connection-description").textContent = state.local ? "Development draft preview · Your real material and courses." : "Your material, courses, and learning sessions in one place.";
    document.querySelector("#connection-dot").classList.toggle("offline", !state.connected);
  }
  function render() {
    releaseReviewAudio();
    const page = location.hash.slice(1);
    state.page = ["materials", "generate", "review", "learn"].includes(page) ? page : "materials";
    if (state.page !== "learn") pausePlayback();
    for (const link of document.querySelectorAll("[data-nav]")) {
      const active = link.dataset.nav === state.page;
      link.classList.toggle("active", active);
      if (active) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
    }
    if (state.loading) { main.innerHTML = `<div class="card empty-state">${icon("loader", "spinner")}<h3>Opening your learning studio…</h3><p>Loading material and available courses.</p></div>`; return; }
    if (state.page === "materials") renderMaterials();
    if (state.page === "generate") renderGenerate();
    if (state.page === "review") renderReview();
    if (state.page === "learn") renderLearn();
    document.title = `UPRAISE · ${{ materials: "Your material", generate: "Generate course", review: "Review courses", learn: "Start learning" }[state.page]}`;
  }

  async function loadMaterials() {
    try {
      const data = await api.request("/v1/documents", { timeout: 45000 });
      if (!Array.isArray(data)) throw new Error("The material list could not be read.");
      state.materials = data; state.materialError = "";
      if (!getMaterial() && !state.activeJobId) {
        const ready = data.find(item => item.status === "DONE");
        if (ready) selectedSource(ready.document_id); else state.selectedMaterial = null;
      }
      return true;
    } catch (error) { state.materialError = error.message; return false; }
  }
  function groupCatalog() {
    const grouped = new Map();
    for (const item of state.catalogItems) {
      if (expired(item.expires_at)) continue;
      if (!grouped.has(item.job_id)) grouped.set(item.job_id, { ...item, id: item.job_id, title: item.lesson_id.replace(/[_-]/g, " "), variants: {} });
      grouped.get(item.job_id).variants[item.learner_level] = item;
    }
    state.courses = [...grouped.values()];
  }
  async function loadCourses(more = false) {
    const offset = more ? state.catalogItems.length : 0;
    try {
      const data = await api.request(`/v1/lesson-jobs?limit=100&offset=${offset}`, { timeout: 45000 });
      if (!Array.isArray(data)) throw new Error("The course collection could not be read.");
      const combined = more ? [...state.catalogItems, ...data] : data;
      state.catalogItems = [...new Map(combined.map(item => [`${item.job_id}:${item.learner_level}`, item])).values()];
      state.more = data.length === 100; state.catalogError = ""; groupCatalog();
      return true;
    } catch (error) { state.catalogError = error.message; return false; }
  }
  async function refreshWorkspace() {
    if (state.refreshing) return;
    state.refreshing = true;
    const epoch = ++workspaceEpoch;
    try {
      if (!state.config) { state.config = await api.initialize(); state.activityId = state.config.activity_id; }
      const results = await Promise.allSettled([loadMaterials(), loadCourses(), loadReviews()]);
      if (epoch !== workspaceEpoch) return;
      state.connected = results.every(result => result.status === "fulfilled" && result.value);
      if (state.page === "review" && state.reviewEditor && !state.reviewEditor.busy) {
        const editor = state.reviewEditor;
        await openReview(editor.id, editor.level, editor.index);
      }
    } catch (error) { state.materialError = error.message; state.catalogError = error.message; state.reviewError = error.message; state.connected = false; }
    finally {
      if (epoch === workspaceEpoch) {
        state.loading = false; state.refreshing = false; updateConnection();
        // Keep an active player and editable forms stable while refreshing collections.
        if (state.page === "materials" && document.querySelector("#upload-form")) updateMaterialList();
        else if (!(state.page === "learn" && state.player)) render();
        schedulePoll();
      }
    }
  }
  function schedulePoll() {
    clearTimeout(pollTimer);
    const hasJobs = [...state.jobs.values()].some(job => !terminal.has(job.status) && !job.trackingError);
    const hasMaterials = !state.materialError && state.materials.some(item => ["QUEUED", "RUNNING"].includes(item.status));
    if (hasJobs || hasMaterials) pollTimer = setTimeout(poll, 5000);
  }
  async function poll() {
    if (polling) return;
    polling = true;
    try {
      for (const job of [...state.jobs.values()]) if (!terminal.has(job.status) && !job.trackingError) await refreshJob(job);
      if (!state.materialError && state.materials.some(item => ["QUEUED", "RUNNING"].includes(item.status))) {
        await loadMaterials();
        if (state.page === "materials") updateMaterialList();
        if (state.page === "generate" && !document.querySelector("#generation-form") && !state.activeJobId && !state.generatedId) renderGenerate();
      }
    } finally { polling = false; schedulePoll(); }
  }

  function renderMaterials() {
    main.innerHTML = `${intro("01", "YOUR MATERIAL", "Great learning starts<br>with <em>your material.</em>", "Bring your documents. We’ll help turn them into clear, engaging learning<br class=\"desktop-break\"> experiences, at a level that feels right.", true)}
      <div class="grid-upload"><section class="card card-padding" aria-labelledby="upload-title"><div class="card-title-row"><h2 id="upload-title">Add your teaching material</h2>${icon("upload")}</div>
      <form id="upload-form"><fieldset class="plain-fieldset" ${state.uploadBusy ? "disabled" : ""}><div class="drop-zone" id="drop-zone"><span class="upload-symbol">${icon("upload")}</span><strong id="file-heading">${state.selectedFile ? escape(state.selectedFile.name) : "Drag your document here"}</strong><p>or <label class="file-label" for="file-input" tabindex="0">browse files</label> to get started</p><span class="file-note" id="file-note">${state.selectedFile ? `${escape(formatSize(state.selectedFile.size))} · Ready to upload` : "PDF, Word or CSV · Up to 100 MB"}</span><input type="file" class="sr-only" id="file-input" accept=".pdf,.docx,.doc,.csv" aria-label="Choose a PDF, Word or CSV document"></div>
      <div class="form-grid"><div class="field"><label for="upload-course">Course name or reference</label><input id="upload-course" name="course" maxlength="120" placeholder="e.g. Introduction to artificial intelligence" value="${escape(state.uploadName)}" required></div><div class="field"><label for="upload-language">Document language</label><select id="upload-language" name="language">${languageOptions(state.uploadLanguage)}</select></div></div>
      <details class="advanced upload-advanced"><summary>Course settings</summary><div class="field"><label for="upload-activity">Activity reference</label><input id="upload-activity" name="activity" maxlength="120" value="${escape(state.activityId)}" required><small>For an existing Moodle course, enter its authorized course reference above and the matching activity here.</small></div></details>
      <div class="form-bottom"><span>${icon("lock")}Securely sent through your UPRAISE connection</span><button class="button" type="submit">${icon(state.uploadBusy ? "loader" : "upload", state.uploadBusy ? "spinner" : "")}${state.uploadBusy ? "Uploading…" : state.uploadAttempt?.failed ? "Retry upload" : "Upload material"}</button></div></fieldset><p class="inline-error" id="upload-error" role="alert" ${state.uploadError ? "" : "hidden"}>${escape(state.uploadError)}</p></form></section>
      <aside class="journey-card" aria-label="How UPRAISE works"><p class="eyebrow">SMALL STEPS. NEW POSSIBILITIES.</p><h2>From something you have,<br>to something you know.</h2><div class="journey-step"><span class="journey-icon">${icon("document")}</span><div><h3>Bring your knowledge</h3><p>Start with material you already trust.</p></div></div><div class="journey-step"><span class="journey-icon">${icon("sparkles")}</span><div><h3>Make it a learning experience</h3><p>Clear explanations, at different levels.</p></div></div><div class="journey-step"><span class="journey-icon">${icon("headphones")}</span><div><h3>Learn your way</h3><p>Read, listen, and ask along the way.</p></div></div><p class="journey-footnote">${icon("leaf")}Built around people. Supported by AI.</p></aside></div>
      <section class="library" aria-labelledby="library-title"><div class="section-heading"><div><h2 id="library-title">Your material <span class="count-pill" id="material-count">${state.materials.length}</span></h2><p>A home for the knowledge your courses are built on.</p></div><label class="search-field">${icon("search")}<span class="sr-only">Search material</span><input id="material-search" type="search" placeholder="Search material…" value="${escape(state.search)}"></label></div><div id="material-results">${materialTable()}</div><p class="tip-line">${icon("info")}PDF, Word and UTF-8 CSV are supported. Processing completion does not establish source accuracy.</p></section>`;
    const fileInput = document.querySelector("#file-input");
    fileInput.addEventListener("change", () => chooseFile(fileInput.files[0]));
    const zone = document.querySelector("#drop-zone");
    zone.addEventListener("dragover", event => { event.preventDefault(); if (!state.uploadBusy) zone.classList.add("dragging"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("dragging"));
    zone.addEventListener("drop", event => { event.preventDefault(); zone.classList.remove("dragging"); if (state.uploadBusy) return; if (event.dataTransfer.files.length > 1) showUploadError("Choose one document at a time."); else chooseFile(event.dataTransfer.files[0]); });
    document.querySelector(".file-label").addEventListener("keydown", event => { if (["Enter", " "].includes(event.key) && !state.uploadBusy) { event.preventDefault(); fileInput.click(); } });
  }
  function formatSize(bytes) { return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`; }
  function showUploadError(message) { state.uploadError = message; const error = document.querySelector("#upload-error"); if (error) { error.textContent = message; error.hidden = false; } }
  function chooseFile(file) {
    if (!file || state.uploadBusy) return;
    let error = "";
    if (!/\.(pdf|docx|doc|csv)$/i.test(file.name)) error = "Choose a PDF, Word (.docx or .doc), or CSV document.";
    else if (!file.size) error = "This file is empty. Please choose another document.";
    else if (file.size > 100 * 1024 * 1024) error = "Please choose a document up to 100 MB.";
    state.selectedFile = error ? null : file;
    document.querySelector("#file-heading").textContent = error ? "Drag your document here" : file.name;
    document.querySelector("#file-note").textContent = error ? "PDF, Word or CSV · Up to 100 MB" : `${formatSize(file.size)} · Ready to upload`;
    if (error) { document.querySelector("#file-input").value = ""; showUploadError(error); return; }
    if (!state.uploadName) {
      state.uploadName = file.name.replace(/\.[^.]+$/, "").replace(/[_-]/g, " ").slice(0, 120);
      document.querySelector("#upload-course").value = state.uploadName;
    }
    state.uploadError = ""; document.querySelector("#upload-error").hidden = true;
  }
  function materialTable() {
    const alert = state.materialError ? errorBox(state.materialError, "refresh-workspace", "Refresh material") : "";
    const deletion = state.deletion?.error ? errorBox(`Removal pending for ${state.deletion.name}. ${state.deletion.error}`, "retry-removal", "Retry this removal") : "";
    const materials = state.materials.filter(item => `${item.filename} ${item.course_id}`.toLowerCase().includes(state.search.toLowerCase()));
    if (!materials.length) return `${alert}${deletion}<div class="card empty-state">${icon("document")}<h3>${state.search ? "No matching material" : "A fresh start for your next course"}</h3><p>${state.search ? "Try another filename or course name." : "Upload a document above to add your first piece of material."}</p></div>`;
    return `${alert}${deletion}<div class="table-shell"><table><thead><tr><th scope="col">Document</th><th class="table-course" scope="col">Course</th><th class="table-language" scope="col">Language</th><th scope="col">Status</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead><tbody>${materials.map(item => {
      const ready = item.status === "DONE";
      const pending = ["QUEUED", "RUNNING"].includes(item.status);
      const status = ready ? "Ready to use" : pending ? `Processing · ${percent(item.progress).toFixed(2)}%` : item.status === "FAILED" ? "Couldn’t process" : "Cancelled";
      return `<tr><td><div class="material-name">${fileIcon(item)}<div><strong>${escape(item.filename)}</strong><small>${Number(item.chunk_count || 0)} source sections${state.uploadJobs.has(item.document_id) ? " · Recently uploaded" : ""}</small></div></div></td><td class="muted table-course">${escape(item.course_id)}</td><td class="muted table-language">${escape(languageLabel(item.language))}</td><td>${badge(status, ready ? "" : "badge-amber", ready ? "check" : "clock")}</td><td><div class="row-actions"><button class="text-button" data-action="create-from-material" data-id="${escape(item.document_id)}" ${ready ? "" : "disabled"} aria-label="Create course from ${escape(item.filename)}">Create course ${icon("arrow")}</button><button class="icon-button" data-action="remove-material" data-id="${escape(item.document_id)}" aria-label="Remove ${escape(item.filename)}">${icon("trash")}</button></div></td></tr>`;
    }).join("")}</tbody></table></div>`;
  }
  function updateMaterialList() {
    const target = document.querySelector("#material-results");
    if (target) { target.innerHTML = materialTable(); document.querySelector("#material-count").textContent = state.materials.length; }
  }
  async function uploadMaterial(form) {
    if (state.uploadBusy) return;
    if (!state.selectedFile) { showUploadError("Choose a document before uploading."); return; }
    const fields = { course_id: form.elements.course.value.trim(), activity_id: form.elements.activity.value.trim(), language: form.elements.language.value, source_ref: state.selectedFile.name.slice(0, 200) };
    if (!fields.course_id || !fields.activity_id) { showUploadError("Enter the course name and activity reference."); return; }
    const file = state.selectedFile;
    state.uploadBusy = true; state.uploadError = ""; renderMaterials();
    try {
      fields.content_hash = await api.sha256(await file.arrayBuffer());
      const signature = JSON.stringify({ ...fields, filename: file.name, size: file.size });
      if (state.uploadAttempt?.signature !== signature) state.uploadAttempt = { signature, key: crypto.randomUUID() };
      const body = new FormData(); Object.entries(fields).forEach(([key, value]) => body.append(key, value)); body.append("file", file, file.name);
      const accepted = await api.request("/v1/documents", { method: "POST", body, headers: { "Idempotency-Key": state.uploadAttempt.key } });
      if (!uuidPattern.test(accepted?.document_id) || !uuidPattern.test(accepted?.job_id)) throw new Error("The backend accepted an unexpected upload response. Refresh material before retrying.");
      state.uploadJobs.set(accepted.document_id, accepted.job_id);
      state.selectedFile = null; state.uploadName = ""; state.uploadAttempt = null;
      await loadMaterials(); schedulePoll(); toast("Material uploaded. You can follow its processing in your library.");
    } catch (error) {
      if (state.uploadAttempt) state.uploadAttempt.failed = true;
      state.uploadError = `${error.message}${error.uncertain ? " Retry with the same file and settings to reuse this upload request." : ""}`;
    } finally { state.uploadBusy = false; if (state.page === "materials") renderMaterials(); }
  }
  function removeMaterialDialog(id) {
    if (state.deletion?.error && state.deletion.id !== id) { toast("Finish retrying the pending removal before removing another document."); return; }
    const material = getMaterial(id);
    if (!material) return;
    state.deletion = { id, name: material.filename, error: "", busy: false };
    openModal(`${modalHeader("Remove this material?", "YOUR MATERIAL")}<p class="modal-copy"><strong>${escape(material.filename)}</strong><br>Every generated course that uses this document will also be permanently removed, across all levels and languages. This cannot be undone.</p><div id="deletion-error"></div><div class="modal-actions"><button class="button button-secondary" data-action="close-modal">Keep material</button><button class="button button-danger" data-action="confirm-remove">Remove material and courses</button></div>`);
  }
  async function confirmRemove() {
    const target = state.deletion;
    if (!target || target.busy) return;
    target.busy = true;
    const button = modal.querySelector('[data-action="confirm-remove"]'); if (button) button.disabled = true;
    try {
      await api.request(`/v1/documents/${target.id}`, { method: "DELETE" });
      if (state.player?.lesson.chunks.some(chunk => chunk.sources.some(source => source.document_id === target.id))) await endLearning("ERROR", true);
      for (const [id, job] of state.jobs) if (job.materialId === target.id) { state.jobs.delete(id); if (state.activeJobId === id) state.activeJobId = null; }
      state.bundles.clear(); state.generatedId = null; state.deletion = null;
      state.reviewEditor = null; state.reviewDrafts.clear();
      modal.close(); await Promise.allSettled([loadMaterials(), loadCourses(), loadReviews()]); render(); toast("The material and its dependent courses were removed.");
    } catch (error) {
      target.error = error.message;
      const message = document.querySelector("#deletion-error"); if (message) message.innerHTML = errorBox(error.message);
      if (button) { button.disabled = false; button.textContent = "Retry this removal"; }
      if (state.page === "materials") updateMaterialList();
    } finally { target.busy = false; }
  }

  function summaryFromBundle(bundle) {
    const variants = variantsOf(bundle);
    const first = Object.values(variants)[0];
    return { ...first, id: bundle.job_id, title: first.lesson_id.replace(/[_-]/g, " "), variants };
  }
  function validateLesson(lesson, jobId) {
    if (!lesson || lesson.job_id !== jobId || !uuidPattern.test(jobId) || !lesson.learner_level
      || !Array.isArray(lesson.chunks) || !lesson.chunks.length || expired(lesson.expires_at)
      || !["course_id", "activity_id", "lesson_id", "language"].every(key => typeof lesson[key] === "string")) throw new Error("This course is unavailable or its content could not be verified.");
    const ids = new Set();
    for (const [index, chunk] of lesson.chunks.entries()) {
      if (chunk.index !== index || !uuidPattern.test(chunk.chunk_id) || ids.has(chunk.chunk_id)
        || typeof chunk.text !== "string" || !chunk.text.trim() || !Array.isArray(chunk.sources)
        || !Number.isFinite(chunk.duration_seconds) || chunk.duration_seconds <= 0
        || !/^[a-f0-9]{64}$/i.test(chunk.audio_sha256 || "")) throw new Error("This course’s text or audio metadata could not be verified.");
      ids.add(chunk.chunk_id);
    }
    return lesson;
  }
  async function fetchBundle(id) {
    const bundle = await api.request(`/v1/jobs/${encodeURIComponent(id)}/result`);
    const variants = variantsOf(bundle);
    if (!Object.keys(variants).length) throw new Error("This course has no available learning levels.");
    for (const lesson of Object.values(variants)) validateLesson(lesson, id);
    state.bundles.set(id, bundle);
    return bundle;
  }
  function partTitle(chunk) {
    const line = chunk.text.split(/\r?\n/).map(value => value.trim()).find(Boolean) || `Part ${chunk.index + 1}`;
    const plain = line.replace(/^#+\s*/, "").replace(/\*\*/g, "");
    return plain.length > 75 ? plain.slice(0, 72) + "…" : plain;
  }
  function textParagraphs(text, className = "lesson-paragraph") {
    return String(text).split(/\n\s*\n/).filter(Boolean).map(paragraph => `<p class="${className}">${escape(paragraph.trim()).replace(/\n/g, "<br>")}</p>`).join("");
  }
  function sourceReferences(sources) {
    return sources.map(source => {
      if (source.source_format === "csv") return (source.row_ranges || []).map(span => `CSV rows ${Number(span.start)}–${Number(span.end)}`).join(", ");
      if (["doc", "docx"].includes(source.source_format)) return source.source_blocks?.length ? `Word blocks ${source.source_blocks.map(Number).join(", ")}` : "Word source";
      return source.pages?.length ? `Pages ${source.pages.map(Number).join(", ")}` : "Course material";
    }).filter(Boolean).join(" · ");
  }
  function unresolvedReview(report) {
    return report?.issues?.some(issue => issue.message?.startsWith("Human review required: ")) || false;
  }
  function reviewDetails(report) {
    if (!report) return "";
    const flagged = unresolvedReview(report);
    const labels = { accuracy: "Accuracy", citations: "Citations", clarity: "Clarity", bias: "Bias", coverage: "Coverage", source: "Source", speech: "Narration" };
    const tags = [...new Set((report.issues || []).filter(issue => issue.message?.startsWith("Human review required: ")).map(issue => {
      if (/REVIEW_UNAVAILABLE|MODEL_OUTPUT_INVALID|ALIGNMENT_MODEL_INVALID|PROVIDER_ERROR/.test(issue.message)) return "Review unavailable";
      if (/TEXT_INVALID/.test(issue.message)) return "Text formatting";
      if (/UNIT_TOO_LARGE|TEXT_TOO_LONG/.test(issue.message)) return "Length";
      return labels[issue.category] || "Human review";
    }))];
    return `${flagged ? `<div class="review-flag-banner" role="note"><strong>This part needs human review</strong><div class="review-tags" aria-label="Review tags">${tags.map(tag => `<span class="review-tag">${escape(tag)}</span>`).join("")}</div><p>Generation continued with unresolved automated checks. Review the text, source and audio before teaching.</p></div>` : ""}<details class="advanced" ${flagged ? "open" : ""}><summary>Automated review and source evidence</summary><p class="small-copy">${escape(report.summary || "Automated checks are supporting evidence; human review remains required.")}</p>${report.issues?.length ? `<ul class="review-findings">${report.issues.map(issue => `<li>${escape(issue.message)}</li>`).join("")}</ul>` : ""}<p class="small-copy">AI checks do not constitute human approval.</p></details>`;
  }
  function renderGenerate() {
    const ready = state.materials.filter(item => item.status === "DONE");
    if (!getMaterial() && ready.length) selectedSource(ready[0].document_id);
    const job = state.jobs.get(state.activeJobId);
    const generated = getCourse(state.generatedId);
    main.innerHTML = `${intro("02", "CREATE SOMETHING WORTH LEARNING", "Your knowledge.<br><em>A whole new learning experience.</em>", "Choose your material and a few preferences. We’ll take it from there.")}
      ${generated && !job ? `<div class="success-banner" role="status">${icon("checkCircle")}<div><strong>Your course is ready for review.</strong><p>Explore the generated text and narration. Human approval remains separate.</p></div></div>` : ""}
      <div class="two-column"><section class="card card-padding" aria-label="Course settings">${job ? generationView(job) : generated ? generatedView(generated) : ready.length ? generationForm(ready) : `${state.materialError ? errorBox(state.materialError, "refresh-workspace") : ""}<div class="empty-state">${icon("document")}<h3>Let’s start with your material</h3><p>Upload a document in the first step.<br>It will appear here when processing is complete.</p><a class="button" href="#materials">${icon("upload")}Upload material</a></div>`}</section><aside class="card course-summary" id="course-summary" aria-label="Course summary">${courseSummary(generated)}</aside></div>
      <section class="library" id="recent-jobs">${recentJobs()}</section>`;
  }
  function generationForm(ready) {
    return `<form id="generation-form"><fieldset class="plain-fieldset" ${state.generationBusy ? "disabled" : ""}><div class="form-section"><div class="step-label"><span>1</span>Choose your source</div><div class="source-options">${ready.map(item => `<label class="source-option">${fileIcon(item)}<span><strong>${escape(item.filename)}</strong><small>${escape(item.course_id)} · ${escape(languageLabel(item.language))}</small></span><input type="radio" name="source" value="${escape(item.document_id)}" ${item.document_id === state.selectedMaterial ? "checked" : ""} required></label>`).join("")}</div></div>
      <div class="form-section"><div class="step-label"><span>2</span>Make it yours</div><div class="field"><label for="course-title">Course title</label><input id="course-title" name="title" maxlength="120" value="${escape(state.draft.title)}" required></div><div class="form-grid"><div class="field"><label for="course-language">Output language</label><select id="course-language" name="language">${languageOptions(state.draft.language)}</select></div><div class="field"><label for="course-voice">Narration voice</label><select id="course-voice" name="voice">${voiceOptions(state.draft.language)}</select></div></div></div>
      <div class="form-section"><div class="field"><label for="teaching-version">Teaching and narration version</label><select id="teaching-version" name="teachingVersion" aria-describedby="teaching-version-help"><option value="v1" ${state.draft.teachingVersion === "v1" ? "selected" : ""}>Version 1 · Current</option><option value="v2" ${state.draft.teachingVersion === "v2" ? "selected" : ""}>Version 2 · Natural formula explanations</option><option value="v3" ${state.draft.teachingVersion === "v3" ? "selected" : ""}>Version 3 · Continuous course teaching</option></select><p class="small-copy" id="teaching-version-help">Version 3 plans a connected course in document order, shares context between parts and reviews text and narration together. Version 2 provides natural formula explanations. Each generation creates a new course; earlier versions remain available.</p></div></div>
      <div class="form-section"><fieldset class="plain-fieldset" aria-describedby="generation-level-help"><legend class="step-label"><span>3</span>Choose difficulty levels</legend><div class="generation-level-options">${configuredLevels().map(level => `<label class="level-option"><input type="checkbox" name="levels" value="${escape(level)}" ${selectedGenerationLevels().includes(level) ? "checked" : ""}><span><strong>${escape(levelLabel(level))}</strong>${levelDescriptions[level] ? `<small>${escape(levelDescriptions[level])}</small>` : ""}</span></label>`).join("")}</div><p class="small-copy" id="generation-level-help">Choose one or more levels. We’ll create text and narration only for your selection.</p><p class="inline-error" id="generation-level-error" ${selectedGenerationLevels().length ? "hidden" : ""}>Select at least one difficulty level.</p></fieldset></div>
      <details class="advanced"><summary>Advanced options</summary><div class="form-grid"><div class="field"><label for="target-words">Target words per part</label><input id="target-words" name="words" type="number" min="30" max="1000" value="${state.draft.words}" required></div><div class="field"><label for="source-window">Source window size</label><input id="source-window" name="window" type="number" min="1" max="50" value="${state.draft.window}" required><small>PDF pages, CSV row groups or Word blocks</small></div></div><div class="field" style="margin-top:16px"><label for="max-words">Maximum words per part</label><input id="max-words" name="maximum" type="number" min="50" max="2000" value="${state.draft.maximum}" required></div></details>
      <button class="button button-wide generate-submit" type="submit" ${selectedGenerationLevels().length ? "" : "disabled"}>${icon(state.generationBusy ? "loader" : "sparkles", state.generationBusy ? "spinner" : "")}${state.generationBusy ? "Submitting course…" : state.generationAttempt?.failed ? "Retry generation request" : "Generate course"}${icon("arrow")}</button></fieldset><p class="preview-note">${icon("info")}Generation uses your selected material and creates real text and narration. Processing continues if you change sections.</p><p class="inline-error" id="generation-error" role="alert" ${state.generationError ? "" : "hidden"}>${escape(state.generationError)}</p></form>`;
  }
  function voiceOptions(language) {
    const voices = language.split("-")[0] === "it" ? [["Bianca", "Bianca · Italian"], ["Adriano", "Adriano · Italian"]] : [["Joanna", "Joanna · US English"], ["Matthew", "Matthew · US English"], ["Amy", "Amy · British English"], ["Brian", "Brian · British English"]];
    return voices.map(([value, label]) => `<option value="${value}" ${value === state.draft.voice ? "selected" : ""}>${label}</option>`).join("");
  }
  function courseSummary(course) {
    const bundle = course && state.bundles.get(course.id);
    const variants = variantsOf(bundle);
    const lesson = Object.values(variants)[0];
    const material = getMaterial(state.jobs.get(state.activeJobId)?.materialId || state.selectedMaterial);
    const title = course?.title || state.jobs.get(state.activeJobId)?.title || state.draft.title || "Your next learning experience";
    return `<div class="card-title-row"><h2>${course ? "Your learning experience" : "A little look ahead"}</h2>${icon("book")}</div>${art("teal")}<div class="summary-body"><span class="sample-label">${course ? "AI-GENERATED DRAFT" : "BUILT FROM YOUR MATERIAL"}</span><h3 id="summary-title">${escape(title)}</h3><p class="small-copy">${material ? `Source: ${escape(material.filename)}` : "A guided path through your material, with text, voice, and room for questions."}</p>${lesson ? `<div class="summary-meta"><span>${icon("book")}${lesson.chunks.length} parts</span><span>${icon("layers")}${Object.keys(variants).length} levels</span></div><hr class="summary-divider"><span class="field-label">Your learning path</span><ol class="outline-list">${lesson.chunks.slice(0, 5).map((chunk, index) => `<li><span>${String(index + 1).padStart(2, "0")}</span><span>${escape(partTitle(chunk))}</span></li>`).join("")}</ol>` : `<hr class="summary-divider"><div class="journey-step"><span class="journey-icon">${icon("document")}</span><div><h3>Your source comes first</h3><p>The learning path will be based on the selected material.</p></div></div><div class="journey-step"><span class="journey-icon">${icon("headphones")}</span><div><h3>Read, listen, and ask</h3><p>Text and narration are prepared for each learning level.</p></div></div>`}<p class="preview-note" style="margin-top:22px">${icon("info")}Courses remain AI-generated drafts until reviewed through your approval process.</p></div>`;
  }
  async function startGeneration(form) {
    if (state.generationBusy) return;
    const material = getMaterial(form.elements.source.value);
    const title = form.elements.title.value.trim();
    const words = Number(form.elements.words.value), maximum = Number(form.elements.maximum.value), windowSize = Number(form.elements.window.value);
    const levels = [...form.querySelectorAll('input[name="levels"]:checked')].map(input => input.value);
    if (!material || material.status !== "DONE" || !title) { state.generationError = "Choose ready material and enter a course title."; renderGenerate(); return; }
    if (!levels.length) { state.generationError = "Select at least one difficulty level."; renderGenerate(); return; }
    if (words > maximum) { state.generationError = "Target words must be at or below the maximum words per part."; renderGenerate(); return; }
    state.draft = { title, language: form.elements.language.value, voice: form.elements.voice.value, words, maximum, window: windowSize, levels, teachingVersion: form.elements.teachingVersion.value };
    const body = { lesson_id: title, course_id: material.course_id, activity_id: material.activity_id, document_ids: [material.document_id], language: state.draft.language, voice: state.draft.voice, levels, options: { source_window_pages: windowSize, target_chunk_words: words, max_chunk_words: maximum } };
    if (["v2", "v3"].includes(state.draft.teachingVersion)) body.teaching_version = state.draft.teachingVersion;
    const signature = JSON.stringify(body);
    if (state.generationAttempt?.signature !== signature) state.generationAttempt = { signature, key: crypto.randomUUID() };
    state.generationBusy = true; state.generationError = ""; renderGenerate();
    try {
      const accepted = await api.request("/v2/lesson-jobs", { method: "POST", json: body, headers: { "Idempotency-Key": state.generationAttempt.key } });
      if (!uuidPattern.test(accepted?.job_id)) throw new Error("The generation response could not be read. Refresh processing before retrying.");
      const job = { job_id: accepted.job_id, title, materialId: material.document_id, status: "QUEUED", step: "QUEUED", progress: 0, type: "generation", trackingError: "" };
      state.jobs.set(job.job_id, job); state.activeJobId = job.job_id; state.generatedId = null; state.generationAttempt = null;
      await refreshJob(job); schedulePoll();
    } catch (error) {
      if (state.generationAttempt) state.generationAttempt.failed = true;
      state.generationError = `${error.message}${error.uncertain ? " Retry unchanged settings to reuse the same generation request." : ""}`;
    } finally { state.generationBusy = false; if (state.page === "generate") renderGenerate(); }
  }
  function stageLabel(step) {
    const labels = { QUEUED: "Waiting to begin", SOURCE: "Understanding your material", GENERATE: "Preparing the learning content", REVIEW: "Checking the explanations", FINALIZE: "Refining the course", TRANSLATE: "Preparing your selected language", SPEECH: "Creating the narration", PUBLISH: "Saving the complete course", DONE: "Ready for review", FAILED: "Processing stopped", CANCELLED: "Cancelled" };
    return labels[step] || "Processing your course";
  }
  function generationView(job) {
    const done = job.status === "DONE", failed = ["FAILED", "CANCELLED"].includes(job.status);
    const details = job.error;
    return `<div class="progress-card"><div class="progress-symbol">${icon(done ? "checkCircle" : failed ? "info" : "sparkles")}</div><span class="sample-label">${job.type === "revision" ? "COURSE REVISION" : "COURSE GENERATION"}</span><h2 style="margin-top:12px">${done ? "Ready for your next learning moment." : failed ? "This request couldn’t be completed." : "Good learning takes a little care."}</h2><p class="small-copy">${escape(job.title)}</p><div class="progress-track" role="progressbar" aria-label="Course processing progress" aria-valuenow="${percent(job.progress)}" aria-valuemin="0" aria-valuemax="100"><div class="progress-fill" style="width:${percent(job.progress)}%"></div></div><div class="progress-description"><span>${escape(stageLabel(job.step))}</span><span>${percent(job.progress).toFixed(2)}%</span></div>
      ${job.trackingError ? errorBox(`Status updates paused. ${job.trackingError}`, "retry-tracking", "Refresh status") : ""}${details ? `<div class="error-banner"><div><p>${escape(details.reason || details.code || "The backend reported a processing failure.")}</p>${details.review?.issues?.length ? `<ul class="review-findings">${details.review.issues.map(issue => `<li>${escape(issue)}</li>`).join("")}</ul>` : ""}${details.review?.revision_instructions?.length ? `<p>${escape(details.review.revision_instructions.join(" "))}</p>` : ""}</div></div>` : ""}
      ${done ? `<button class="button button-wide" data-action="open-job-result" data-id="${escape(job.job_id)}">Open updated course ${icon("arrow")}</button>` : !failed ? `<p class="preview-note" style="margin-top:25px;text-align:left">${icon("info")}Updates refresh every five seconds. Generation continues on the backend while you browse.</p><button class="text-button" data-action="cancel-generation" data-id="${escape(job.job_id)}" ${job.cancelling ? "disabled" : ""}>${job.cancelling ? "Cancelling…" : "Cancel this request"}</button>` : ""}
      <button class="text-button" data-action="new-generation" style="display:flex;margin:18px auto 0">${icon("plus")}${terminal.has(job.status) ? "Prepare a new course" : "Prepare another course"}</button></div>`;
  }
  function recentJobs() {
    if (!state.jobs.size) return "";
    return `<div class="section-heading"><div><h2>Recent processing</h2><p>Requests submitted in this tab. Accepted work continues after navigation.</p></div></div><div class="card activity-list">${[...state.jobs.values()].reverse().map(job => `<div class="activity-row"><div><strong>${escape(job.title)}</strong><small>${job.type === "revision" ? "Revision" : "Course generation"} · ${escape(stageLabel(job.step))}</small></div>${badge(job.status === "DONE" ? "Ready for review" : job.trackingError ? "Tracking paused" : `${percent(job.progress).toFixed(2)}%`, job.status === "DONE" ? "" : "badge-amber")}<button class="text-button" data-action="open-tracking" data-id="${escape(job.job_id)}">View ${icon("arrow")}</button></div>`).join("")}</div>`;
  }
  async function refreshJob(job) {
    try {
      const status = await api.request(`/v1/jobs/${job.job_id}`, { timeout: 30000 });
      if (status.job_id !== job.job_id) throw new Error("The processing status did not match this request.");
      Object.assign(job, status, { trackingError: "" });
      if (job.status === "DONE" && !job.resultLoaded) {
        const targetId = job.type === "revision" ? job.targetId : job.job_id;
        await fetchBundle(targetId);
        await Promise.allSettled([loadCourses(), loadReviews()]);
        if (job.type === "revision" && state.reviewEditor?.id === targetId) {
          const editor = state.reviewEditor;
          const updated = validateReview(await api.request(`/v1/lesson-reviews/${encodeURIComponent(targetId)}`), targetId);
          if (state.reviewEditor === editor) {
            rememberReviewDraft(editor);
            if (job.reviewDraftKey && state.reviewDrafts.get(job.reviewDraftKey)?.text === job.reviewText) state.reviewDrafts.delete(job.reviewDraftKey);
            editor.bundle = updated;
            editor.level = updated.levels.includes(editor.level) ? editor.level : updated.levels[0];
            editor.index = Math.min(editor.index, updated.variants[editor.level].chunks.length - 1);
            const draft = state.reviewDrafts.get(reviewDraftKey(editor));
            editor.text = draft?.text || ""; editor.all = draft?.all || false;
          }
        }
        if (job.type === "revision" && state.player?.lesson.job_id === targetId) await invalidatePlayer("This course was revised. Start a fresh session to use the updated content.");
        if (job.reviewDraftKey && state.reviewDrafts.get(job.reviewDraftKey)?.text === job.reviewText) state.reviewDrafts.delete(job.reviewDraftKey);
        if (state.activeJobId === job.job_id) { state.generatedId = targetId; state.activeJobId = null; state.resultLevel = job.level || ""; state.resultPart = job.partIndex || 0; }
        job.resultLoaded = true;
        toast(job.type === "revision" ? "The course revision is ready for review." : "Your generated course is ready for review.");
      }
    } catch (error) { job.trackingError = error.message; }
    if (state.page === "generate") {
      if (state.activeJobId === job.job_id || state.generatedId && job.resultLoaded) renderGenerate();
      else { const list = document.querySelector("#recent-jobs"); if (list) list.innerHTML = recentJobs(); }
    }
    if (state.page === "review" && state.reviewEditor?.id === job.targetId) renderReview();
  }
  async function cancelGeneration(id) {
    const job = state.jobs.get(id);
    if (!job || terminal.has(job.status) || job.cancelling) return;
    job.cancelling = true; render();
    try { await api.request(`/v1/jobs/${job.job_id}?reason=CANCEL`, { method: "DELETE" }); await refreshJob(job); }
    catch (error) { job.trackingError = error.message; toast(`${error.message} Refresh status before retrying cancellation.`); }
    finally { job.cancelling = false; if (["generate", "review"].includes(state.page)) render(); schedulePoll(); }
  }
  function generatedView(course) {
    const variants = variantsOf(state.bundles.get(course.id));
    if (!variants[state.resultLevel]) state.resultLevel = Object.keys(variants).find(level => level === "intermediate") || Object.keys(variants)[0];
    const lesson = variants[state.resultLevel];
    state.resultPart = Math.min(state.resultPart, lesson.chunks.length - 1);
    const chunk = lesson.chunks[state.resultPart];
    return `<div class="card-title-row"><h2>Ready for a first look</h2>${badge("AI-generated draft", "badge-amber")}</div><h3 class="result-course-title">${escape(course.title)}</h3><p class="small-copy">Choose a level and part to review the generated text and source references.</p><div class="form-grid"><div class="field"><label for="result-level">Learning level</label><select id="result-level">${Object.keys(variants).map(level => `<option value="${escape(level)}" ${state.resultLevel === level ? "selected" : ""}>${escape(levelLabel(level))}</option>`).join("")}</select></div><div class="field"><label for="result-part">Course part</label><select id="result-part">${lesson.chunks.map((part, index) => `<option value="${index}" ${index === state.resultPart ? "selected" : ""}>Part ${index + 1}</option>`).join("")}</select></div></div><div class="result-text">${textParagraphs(chunk.text, "preview-text")}</div><p class="tip-line">${icon("document")}${escape(sourceReferences(chunk.sources))}</p>${reviewDetails(chunk.reviewer_report)}<div class="summary-meta"><span>${icon("clock")}${time(chunk.duration_seconds)}</span><span>${icon("globe")}${escape(languageLabel(lesson.language))}</span><span>${icon("headphones")}${escape(lesson.voice)}</span></div><p class="small-copy">Human review is required. Audio is available in the learning player while this course’s temporary output is retained.</p><button class="button button-wide" data-action="start-course" data-id="${escape(course.id)}">${icon("play")}Start learning</button><div class="form-grid" style="grid-template-columns:1fr 1fr;gap:10px;margin:12px 0 0"><button class="button button-secondary" data-action="review-course" data-id="${escape(course.id)}" data-level="${escape(state.resultLevel)}" data-part="${state.resultPart}">${icon("eye")}Review course</button><button class="button button-secondary" data-action="revise-course" data-id="${escape(course.id)}">${icon("edit")}Request changes</button></div><button class="text-button" data-action="new-generation" style="margin-top:25px">${icon("plus")}Create another course</button>`;
  }
  async function loadReviews(more = false) {
    const offset = more ? state.reviewCourses.length : 0;
    try {
      const data = await api.request(`/v1/lesson-reviews?limit=100&offset=${offset}`, { timeout: 45000 });
      if (!Array.isArray(data) || data.some(course => !uuidPattern.test(course.job_id) || !Array.isArray(course.levels))) throw new Error("The review collection could not be read.");
      state.reviewCourses = [...new Map((more ? [...state.reviewCourses, ...data] : data).map(course => [course.job_id, course])).values()];
      state.reviewMore = data.length === 100; state.reviewError = "";
      return true;
    } catch (error) { state.reviewError = error.message; return false; }
  }
  function validateReview(bundle, id) {
    if (!bundle || bundle.job_id !== id || !uuidPattern.test(id) || !Array.isArray(bundle.levels) || !bundle.levels.length
      || typeof bundle.revision_available !== "boolean") throw new Error("This course’s teaching text could not be verified.");
    const ids = new Set();
    for (const level of bundle.levels) {
      const variant = bundle.variants?.[level];
      if (!variant || !Array.isArray(variant.chunks) || !variant.chunks.length) throw new Error("This course has no teaching text for that level.");
      for (const [index, chunk] of variant.chunks.entries()) {
        if (chunk.index !== index || !uuidPattern.test(chunk.chunk_id) || ids.has(chunk.chunk_id)
          || typeof chunk.text !== "string" || !chunk.text.trim() || !Array.isArray(chunk.sources)) throw new Error("This course’s teaching text could not be verified.");
        ids.add(chunk.chunk_id);
      }
    }
    return bundle;
  }
  function reviewDraftKey(editor) {
    const chunk = editor.bundle?.variants[editor.level]?.chunks[editor.index];
    return chunk ? `${editor.id}:${chunk.chunk_id}` : "";
  }
  function rememberReviewDraft(editor) {
    const key = reviewDraftKey(editor);
    if (key) state.reviewDrafts.set(key, { text: editor.text, all: editor.all });
  }
  function selectReviewPart(editor, level, index) {
    rememberReviewDraft(editor);
    editor.level = level; editor.index = index;
    const draft = state.reviewDrafts.get(reviewDraftKey(editor));
    editor.text = draft?.text || ""; editor.all = draft?.all || false; editor.error = "";
  }
  async function openReview(id, level = "", index = 0) {
    if (state.reviewEditor?.busy) return;
    const previous = state.reviewEditor;
    if (previous?.bundle) rememberReviewDraft(previous);
    const editor = { id, bundle: null, level, index, text: "", all: false, busy: false, loading: true, error: "", uncertain: state.revisionUncertain.has(id), origin: "review" };
    state.reviewEditor = editor;
    navigate("review");
    try {
      const bundle = validateReview(await api.request(`/v1/lesson-reviews/${encodeURIComponent(id)}`), id);
      if (state.reviewEditor !== editor) return;
      editor.bundle = bundle;
      editor.level = bundle.levels.includes(level) ? level : bundle.levels[0];
      editor.index = Math.max(0, Math.min(index, bundle.variants[editor.level].chunks.length - 1));
      const draft = state.reviewDrafts.get(reviewDraftKey(editor));
      editor.text = draft?.text || ""; editor.all = draft?.all || false;
    } catch (error) { if (state.reviewEditor === editor) editor.error = error.message; }
    finally { editor.loading = false; if (state.page === "review" && state.reviewEditor === editor) renderReview(); }
  }
  function reviewJobs(id) { return [...state.jobs.values()].filter(job => job.type === "revision" && job.targetId === id); }
  function reviewUnavailable(bundle) {
    return bundle.revision_unavailable_reason === "LESSON_RESULT_NOT_READY"
      ? "Teaching text is available, but generation has not completed. Revisions become available when the course and narration are ready."
      : "Temporary narration is no longer available. You can read the retained teaching text here. Generate the course again before requesting text and narration changes.";
  }
  function reviewCards() {
    const courses = state.reviewCourses.filter(course => `${course.lesson_id} ${course.course_id} ${course.activity_id}`.toLowerCase().includes(state.reviewSearch.toLowerCase()));
    const alert = state.reviewError ? errorBox(state.reviewError, "refresh-reviews", "Refresh courses") : "";
    if (!courses.length) return `${alert}<div class="card empty-state">${icon("book")}<h3>${state.reviewSearch ? "No matching courses" : "Your teaching text will appear here"}</h3><p>${state.reviewSearch ? "Try another course title or reference." : "Generate a course to begin reviewing its explanations, one part at a time."}</p>${!state.reviewSearch ? '<a class="button button-secondary" href="#generate">Generate course</a>' : ""}</div>`;
    return `${alert}<div class="review-course-list">${courses.map(course => `<article class="card review-course-row"><div><div class="summary-meta">${badge("AI-generated draft", "badge-amber")}${badge(course.revision_available && !expired(course.audio_expires_at) ? "Ready to revise" : "Text available")}</div><h3>${escape(course.lesson_id)}</h3><p class="small-copy">${escape(course.course_id)} · ${escape(course.activity_id)} · ${escape(languageLabel(course.language))}</p>${levelChips(course.levels)}<p class="small-copy">${course.levels.map(level => `${escape(levelLabel(level))}: ${Number(course.chunk_counts[level])} parts`).join(" · ")}</p></div><button class="button button-secondary" data-action="review-course" data-id="${escape(course.job_id)}">${icon("eye")}Review course ${icon("arrow")}</button></article>`).join("")}</div>`;
  }
  function renderReview() {
    releaseReviewAudio();
    const editor = state.reviewEditor;
    const heading = intro("03", "A TEACHER’S PERSPECTIVE", "Good explanations grow<br>with <em>your feedback.</em>", "Read your courses, check each part, and guide the next revision.");
    if (!editor) {
      main.innerHTML = `${heading}<section aria-labelledby="review-courses-title"><div class="section-heading"><div><h2 id="review-courses-title">Courses to review <span class="count-pill">${state.reviewCourses.length}</span></h2><p>All retained teaching text, including courses whose temporary narration has expired.</p></div><label class="search-field">${icon("search")}<span class="sr-only">Search courses to review</span><input id="review-search" type="search" placeholder="Find a course…" value="${escape(state.reviewSearch)}"></label></div><div id="review-results">${reviewCards()}</div>${state.reviewMore ? '<button class="button button-secondary load-more" data-action="load-more-reviews">Load more courses</button>' : ""}</section>`;
      return;
    }
    const back = '<button class="back-button" data-action="review-back">← Back to courses to review</button>';
    if (editor.loading) { main.innerHTML = `${heading}${back}<div class="card empty-state" role="status">${icon("loader", "spinner")}<h3>Loading teaching text…</h3></div>`; return; }
    if (!editor.bundle) { main.innerHTML = `${heading}${back}${errorBox(editor.error, "reload-review", "Reload course text")}`; return; }
    const bundle = editor.bundle, lesson = bundle.variants[editor.level], chunk = lesson.chunks[editor.index];
    const jobs = reviewJobs(editor.id), job = jobs.at(-1);
    const pending = jobs.some(item => !terminal.has(item.status));
    const available = bundle.revision_available && !expired(bundle.audio_expires_at);
    const disabled = editor.busy || pending || !available;
    main.innerHTML = `${heading}${back}<div class="review-course-heading"><div><h2>${escape(bundle.lesson_id)}</h2><p class="small-copy">${escape(bundle.course_id)} · ${escape(languageLabel(bundle.language))} · ${escape(bundle.voice)} · Teaching ${escape((bundle.teaching_version || "v1").toUpperCase())}</p></div>${badge("AI-generated draft · Human review required", "badge-amber")}</div>
      ${job ? `<div class="review-progress" aria-live="polite">${generationView(job)}</div>` : ""}
      ${!available ? `<div class="learning-note">${icon("info")}<div><strong>Teaching text remains available</strong><p>${escape(reviewUnavailable(bundle))}</p><a class="text-button" href="#generate">Go to Generate course ${icon("arrow")}</a></div></div>` : ""}
      <div class="form-grid review-selectors"><div class="field"><label for="review-level">Learning level</label><select id="review-level" ${editor.busy ? "disabled" : ""}>${bundle.levels.map(level => `<option value="${escape(level)}" ${editor.level === level ? "selected" : ""}>${escape(levelLabel(level))}</option>`).join("")}</select></div><div class="field"><label for="review-part">Course part</label><select id="review-part" ${editor.busy ? "disabled" : ""}>${lesson.chunks.map((part, index) => `<option value="${index}" ${editor.index === index ? "selected" : ""}>Part ${index + 1}${unresolvedReview(part.reviewer_report) ? " · Needs review" : ""} — ${escape(partTitle(part))}</option>`).join("")}</select></div></div>
      <div class="review-workspace"><section class="card card-padding review-reading" aria-labelledby="review-part-title"><div class="card-title-row"><h2 id="review-part-title">Part ${editor.index + 1} of ${lesson.chunks.length}</h2>${badge(levelLabel(editor.level))}</div><div class="review-full-text">${textParagraphs(chunk.text, "lesson-paragraph")}</div>${reviewNarration(chunk, bundle, available && !disabled)}<p class="tip-line">${icon("document")}${escape(sourceReferences(chunk.sources))}</p>${reviewDetails(chunk.reviewer_report)}<div class="lesson-navigation"><button class="button button-secondary" data-action="review-previous" ${editor.index === 0 || editor.busy ? "disabled" : ""}>${icon("back")}Previous part</button><button class="button button-secondary" data-action="review-next" ${editor.index === lesson.chunks.length - 1 || editor.busy ? "disabled" : ""}>Next part ${icon("arrow")}</button></div></section>
      <aside class="card card-padding review-feedback" aria-labelledby="feedback-title"><h2 id="feedback-title">Your feedback</h2><p class="small-copy">Describe how this part should change. Updating replaces its text and narration in the same course.</p>${editor.uncertain || state.revisionUncertain.has(editor.id) ? `${errorBox("The previous revision may already have been accepted. Refresh the course and check its text before preparing another request. No revision is retried automatically.")}<button class="button button-secondary" data-action="reload-review">Refresh course text</button><button class="text-button" data-action="prepare-new-review">I checked; prepare new request</button>` : `<form id="review-form"><fieldset class="plain-fieldset" ${disabled ? "disabled" : ""}><div class="field"><label for="review-feedback">What would you change in part ${editor.index + 1}?</label><textarea id="review-feedback" maxlength="8000" required placeholder="e.g. Add a practical example and explain the key term more simply.">${escape(editor.text)}</textarea><small>Your comments stay in this tab while you move between parts.</small></div>${bundle.levels.length > 1 ? `<label class="checkbox-label"><input id="review-all" type="checkbox" ${editor.all ? "checked" : ""}>Apply this change to the corresponding part in every available level</label>` : ""}<button class="button button-wide" type="submit">${icon(editor.busy || pending ? "loader" : "edit", editor.busy || pending ? "spinner" : "")}${editor.busy ? "Submitting…" : pending ? "Revision in progress…" : "Update this part"}</button></fieldset>${editor.error ? errorBox(editor.error) : ""}</form>`}<p class="preview-note">${icon("info")}The updated explanation remains a draft for human review.</p></aside></div>`;
  }

  function reviewNarration(chunk, bundle, playable) {
    if (!["v2", "v3"].includes(bundle.teaching_version) || !chunk.narration_text) return "";
    return `<details class="advanced review-narration"><summary>Spoken explanation · Version ${bundle.teaching_version === "v3" ? "3" : "2"}</summary><div class="review-narration-text result-text">${textParagraphs(chunk.narration_text, "lesson-paragraph")}</div><p class="small-copy">Check the formula explanations and listen before approving this draft.</p>${playable ? `<div class="answer-audio-controls"><button class="button button-secondary button-sm" data-action="review-listen">${icon("headphones")}Listen to this part</button><button class="button button-secondary button-sm" data-action="review-stop" disabled>${icon("stop")}Stop narration</button></div><audio id="review-audio" controls preload="none" aria-label="Narration for this course part" style="display:block;width:100%;margin-top:14px"></audio>` : ""}</details>`;
  }
  function releaseReviewAudio() {
    ++reviewAudioEpoch;
    const audio = document.querySelector("#review-audio");
    if (audio?.getAttribute("src")) { audio.pause(); audio.removeAttribute("src"); audio.load(); }
    if (reviewAudioUrl) URL.revokeObjectURL(reviewAudioUrl); reviewAudioUrl = null;
    const stop = document.querySelector('[data-action="review-stop"]'); if (stop) stop.disabled = true;
  }
  async function listenReview(control) {
    const editor = state.reviewEditor;
    if (!editor?.bundle || editor.busy) return;
    releaseReviewAudio(); const epoch = reviewAudioEpoch;
    const selected = editor.bundle.variants[editor.level].chunks[editor.index];
    control.disabled = true;
    try {
      const bundle = await fetchBundle(editor.id), lesson = variantsOf(bundle)[editor.level];
      const chunk = lesson?.chunks.find(item => item.chunk_id === selected.chunk_id);
      if (!chunk || chunk.text !== selected.text) throw new Error("This part changed. Refresh its teaching text before listening.");
      const blob = await api.verifiedAudio(editor.id, chunk);
      if (epoch !== reviewAudioEpoch || state.reviewEditor !== editor || state.page !== "review") return;
      const audio = document.querySelector("#review-audio"); if (!audio) return;
      reviewAudioUrl = URL.createObjectURL(blob); audio.src = reviewAudioUrl;
      document.querySelector('[data-action="review-stop"]').disabled = false;
      try { await audio.play(); } catch { if (epoch === reviewAudioEpoch && state.reviewEditor === editor) toast("Narration is loaded. Press Play in the audio controls to listen."); }
    } catch (error) { if (epoch === reviewAudioEpoch) toast(error.message); }
    finally { if (control.isConnected) control.disabled = false; }
  }

  function renderLearn() {
    if (state.player) { if (state.player.complete) renderCompletion(); else renderPlayer(); return; }
    main.innerHTML = `${intro("04", "A LITTLE CURIOSITY GOES A LONG WAY", "Make room for<br><em>your next ‘I get it.’</em>", "Pick a course. Find your level. Learn at your own pace.")}<section aria-labelledby="courses-title"><div class="section-heading"><div><h2 id="courses-title">Your learning collection <span class="count-pill">${state.courses.length}</span></h2><p>Generated courses with available text and narration, ready for draft preview.</p></div><label class="search-field">${icon("search")}<span class="sr-only">Search courses</span><input id="course-search" type="search" placeholder="Find a course…" value="${escape(state.courseSearch)}"></label></div><div id="course-results">${courseCards()}</div>${state.more ? `<button class="button button-secondary load-more" data-action="load-more">Load more courses ${icon("plus")}</button>` : ""}</section><aside class="learning-note">${icon("lightbulb")}<div><strong>Same knowledge. Your way of understanding.</strong><p>Choose an available level before starting. Generated courses are drafts. Saved courses retain their narration; learning sessions have their own time limit.</p></div></aside>`;
  }
  function courseCards() {
    const courses = state.courses.filter(course => course.title.toLowerCase().includes(state.courseSearch.toLowerCase()));
    const alert = state.catalogError ? errorBox(state.catalogError, "refresh-workspace", "Refresh courses") : "";
    if (!courses.length && state.courseSearch) return `${alert}<div class="card empty-state">${icon("search")}<h3>No courses found</h3><p>Try a different title or load more courses.</p></div>`;
    return `${alert}<div class="course-grid">${courses.map((course, index) => `<article class="card course-card">${art(index % 2 ? "sand" : "teal", String(index + 1).padStart(2, "0"))}<div class="course-card-body">${badge("Draft · Human review required", "badge-amber")}<div class="course-title"><h2>${escape(course.title)}</h2>${badge(teachingVersionLabel(course), "course-version")}</div><p class="small-copy">${escape(course.course_id)} · ${escape(course.activity_id)}</p><div class="summary-meta"><span>${icon("book")}${Number(course.chunk_count || course.chunks?.length || 0)} parts</span><span>${icon("globe")}${escape(languageLabel(course.language))}</span><span>${icon("headphones")}${escape(course.voice)}</span></div><div class="course-card-bottom">${levelChips(levelsFor(course))}<div class="course-card-cta"><button class="button" data-action="start-course" data-id="${escape(course.id)}">Start learning ${icon("arrow")}</button><button class="icon-button" data-action="preview-course" data-id="${escape(course.id)}" aria-label="Preview ${escape(course.title)}">${icon("eye")}</button></div></div></div></article>`).join("")}<article class="create-course-card"><span class="upload-symbol">${icon("plus")}</span><h2>What will you learn next?</h2><p>There’s a learning experience waiting inside your material.</p><a class="button button-secondary" href="#generate">Create a course ${icon("arrow")}</a></article></div>`;
  }
  async function previewCourse(id) {
    openModal(`${modalHeader("Opening your course…")}<p class="modal-copy">Checking availability and loading the current learning path.</p>`);
    const epoch = modalEpoch;
    try {
      const bundle = await fetchBundle(id);
      if (!modal.open || epoch !== modalEpoch) return;
      const lesson = Object.values(variantsOf(bundle))[0];
      modal.innerHTML = `${modalHeader(lesson.lesson_id, "A FIRST LOOK")}<p class="modal-copy">${lesson.chunks.length} parts · ${Object.keys(variantsOf(bundle)).length} levels · ${escape(languageLabel(lesson.language))}</p>${lesson.chunks.map(chunk => `<section class="preview-part"><h3>${chunk.index + 1}. ${escape(partTitle(chunk))}</h3><p>${escape(chunk.text.slice(0, 350))}${chunk.text.length > 350 ? "…" : ""}</p></section>`).join("")}<p class="modal-disclaimer">AI-generated draft · Human review required.<br>${lesson.expires_at ? `Text and audio are available until ${escape(new Date(lesson.expires_at).toLocaleString())}.` : "Text and narration are saved with this course."}</p><div class="modal-actions"><button class="button button-secondary" data-action="close-modal">Close preview</button><button class="button" data-action="start-course" data-id="${escape(id)}">Start learning ${icon("arrow")}</button></div>`;
    } catch (error) { if (modal.open && epoch === modalEpoch) modal.innerHTML = `${modalHeader("This course couldn’t be opened")}${errorBox(error.message)}<div class="modal-actions"><button class="button button-secondary" data-action="close-modal">Close</button></div>`; }
  }

  async function startCourseDialog(id) {
    if (state.player && !state.player.closed && !state.player.complete) {
      navigate("learn"); toast("End your current learning session before starting another course."); return;
    }
    openModal(`${modalHeader("Finding your starting point…", "LEARN YOUR WAY")}<p class="modal-copy">Checking the available course and learning levels.</p>`);
    const epoch = modalEpoch;
    const pending = { id, busy: false, error: "" }; state.startPending = pending;
    try {
      pending.bundle = await fetchBundle(id);
      if (state.startPending !== pending || !modal.open || epoch !== modalEpoch) return;
      renderStartDialog();
    } catch (error) {
      if (state.startPending === pending && modal.open && epoch === modalEpoch) modal.innerHTML = `${modalHeader("This course is unavailable")}${errorBox(error.message)}<div class="modal-actions"><button class="button button-secondary" data-action="close-modal">Close</button></div>`;
    }
  }
  function renderStartDialog() {
    const pending = state.startPending;
    if (!pending?.bundle) return;
    const variants = variantsOf(pending.bundle);
    const defaultLevel = pending.level || (variants.intermediate ? "intermediate" : Object.keys(variants)[0]);
    modal.innerHTML = `${modalHeader("Find your starting point", "LEARN YOUR WAY")}<p class="modal-copy">${escape(Object.values(variants)[0].lesson_id)}<br>Choose the explanation style that feels right for you.</p><form id="start-form"><fieldset class="plain-fieldset" ${pending.busy ? "disabled" : ""}><div class="level-options">${Object.keys(variants).map(level => `<label class="level-option"><input type="radio" name="level" value="${escape(level)}" ${level === defaultLevel ? "checked" : ""}><span><strong>${escape(levelLabel(level))}</strong><small>${escape(levelDescriptions[level] || "An explanation profile configured for this course.")}</small></span></label>`).join("")}</div>
      ${state.local ? `<label class="checkbox-label"><input id="questions-consent" type="checkbox" ${state.questionsConsent ? "checked" : ""}>Enable AI teacher questions for this development session</label>` : ""}
      <details class="advanced" ${state.local ? "" : "open"}><summary>Session authorization</summary><div class="field" style="margin-top:15px"><label for="session-jwt">Signed lesson session token</label><input id="session-jwt" type="password" autocomplete="off" value="${escape(state.sessionToken)}" ${state.local ? "" : "required"}><small>${state.local ? "Optional in local development mode. A supplied token is always verified." : "Your Middleware must issue a signed token for this exact course, result and learning level. Questions follow its consent scope."}</small></div></details>
      <button class="button button-wide generate-submit" type="submit">${icon(pending.busy ? "loader" : "play", pending.busy ? "spinner" : "")}${pending.busy ? "Opening your session…" : pending.error ? "Retry session start" : "Start learning"}</button></fieldset>${pending.error ? errorBox(pending.error) : ""}<p class="modal-disclaimer">AI-generated draft · Human review required.<br>${state.local ? "This opens a local draft preview, with real course text and audio." : "Your signed session assertion must attest the external approval of this exact result."}</p></form>`;
    const close = modal.querySelector('[data-action="close-modal"]'); if (close) close.disabled = pending.busy;
  }
  function sessionHeaders(player) { return player?.token ? { "X-UPRAISE-Session-JWT": player.token } : {}; }
  async function startCourse(form) {
    const pending = state.startPending;
    if (!pending || pending.busy) return;
    pending.level = form.elements.level.value;
    state.sessionToken = form.querySelector("#session-jwt").value.trim();
    state.questionsConsent = Boolean(form.querySelector("#questions-consent")?.checked);
    if (!state.local && !state.sessionToken) { pending.error = "Enter the signed session token for this course."; renderStartDialog(); return; }
    const selected = variantsOf(pending.bundle)[pending.level];
    const body = { course_id: selected.course_id, activity_id: selected.activity_id, lesson_id: selected.lesson_id, language: selected.language, lesson_job_id: selected.job_id };
    if (selected.generation_version === "a3-v2") body.learner_level = selected.learner_level;
    const signature = JSON.stringify({ body, token: state.sessionToken, questions: state.questionsConsent });
    if (state.startAttempt?.signature !== signature) state.startAttempt = { signature, id: crypto.randomUUID() };
    if (!state.sessionToken) { body.session_id = state.startAttempt.id; body.consent_scope = state.questionsConsent ? ["questions"] : []; }
    const token = state.sessionToken;
    pending.busy = true; pending.error = ""; renderStartDialog();
    let session;
    try {
      session = await api.request("/v1/sessions", { method: "POST", json: body, headers: token ? { "X-UPRAISE-Session-JWT": token } : {} });
      if (!session || !uuidPattern.test(session.session_id) || session.lesson_job_id !== selected.job_id
        || session.learner_level !== selected.learner_level || session.course_id !== selected.course_id
        || session.activity_id !== selected.activity_id || session.lesson_id !== selected.lesson_id
        || session.language !== selected.language || !/^[a-f0-9]{64}$/.test(session.result_sha256 || "") || expired(session.expires_at)) throw new Error("The session did not match the selected course. Please start again.");
      releasePlayerMedia(); clearQuestion();
      state.player = { lesson: selected, course: summaryFromBundle(pending.bundle), session, token,
        index: 0, offset: 0, speed: 1, complete: false, closed: false, invalid: "", busy: false,
        questionsEnabled: token ? true : state.questionsConsent, visited: new Set([0]), audioError: "", audioHash: "", endPending: null };
      state.startAttempt = null; pending.busy = false; state.startPending = null; state.sessionToken = "";
      modal.close(); navigate("learn");
      await loadPart(0, { autoplay: false });
      if (state.page === "learn") renderPlayer();
      scheduleExpiry(); window.scrollTo({ top: 0, behavior: "instant" });
    } catch (error) {
      if (session && state.player?.session?.session_id !== session.session_id && uuidPattern.test(session.session_id)) {
        try { await api.request(`/v1/sessions/${session.session_id}/end`, { method: "POST", json: { reason: "ERROR" }, headers: token ? { "X-UPRAISE-Session-JWT": token } : {} }); state.startAttempt = null; } catch { /* Session expires on the backend if cleanup is unavailable. */ }
      }
      pending.error = error.message; pending.busy = false;
      if (state.startPending === pending && modal.open) renderStartDialog(); else toast(error.message);
    }
  }
  function pauseReply() { ++replyPlaybackEpoch; replyAudio.pause(); }
  function pausePlayback() { lectureAudio.pause(); pauseReply(); updatePlayback(); }
  function releasePlayerMedia() {
    ++playerEpoch; pausePlayback(); clearTimeout(expiryTimer);
    lectureAudio.removeAttribute("src"); lectureAudio.load();
    if (lectureUrl) URL.revokeObjectURL(lectureUrl); lectureUrl = null;
    releaseReply();
  }
  function releaseReply() {
    pauseReply();
    try { replyAudio.currentTime = 0; } catch { /* Media may already be unloaded. */ }
    replyAudio.removeAttribute("src"); replyAudio.load();
    if (replyUrl) URL.revokeObjectURL(replyUrl); replyUrl = null;
  }
  function clearQuestion() {
    questionController?.abort(); questionController = null; releaseReply();
    state.question = ""; state.questionOpen = false; state.questionPending = false; state.questionHistory = []; state.turn = null;
  }
  function scheduleExpiry() {
    clearTimeout(expiryTimer);
    const player = state.player;
    if (!player || player.closed || player.complete) return;
    const deadline = Math.min(Date.parse(player.session.expires_at), Date.parse(player.lesson.expires_at || player.session.expires_at));
    expiryTimer = setTimeout(() => { if (state.player === player) void invalidatePlayer("This learning session has expired. Start a fresh session to continue.", "TIMEOUT"); }, Math.max(0, Math.min(2147483647, deadline - Date.now())));
  }
  async function currentLesson(player) {
    const id = player.lesson.job_id;
    const lesson = validateLesson(await api.request(`/v1/jobs/${id}/result?learner_level=${encodeURIComponent(player.lesson.learner_level)}`), id);
    const status = await api.request(`/v1/jobs/${id}`, { timeout: 30000 });
    if (status.status !== "DONE" || status.cleanup_pending || status.acknowledged || status.result?.result_sha256 !== player.session.result_sha256
      || lesson.learner_level !== player.session.learner_level || lesson.course_id !== player.session.course_id
      || lesson.activity_id !== player.session.activity_id || lesson.lesson_id !== player.session.lesson_id
      || lesson.language !== player.session.language || expired(player.session.expires_at)) throw new Error("This course changed or its session expired. Start a fresh learning session.");
    return lesson;
  }
  async function loadPart(index, { autoplay = false, offset = 0 } = {}) {
    const player = state.player;
    if (!player || player.busy || player.closed || player.invalid || player.endPending) return false;
    const epoch = ++playerEpoch;
    player.busy = true; pausePlayback();
    if (state.page === "learn") renderPlayer();
    try {
      const lesson = await currentLesson(player);
      if (state.player !== player || epoch !== playerEpoch) return false;
      if (index < 0 || index >= lesson.chunks.length) return false;
      const changed = index !== player.index;
      if (changed) clearQuestion();
      player.lesson = lesson; player.index = index; player.offset = offset; player.visited.add(index);
      const chunk = lesson.chunks[index];
      player.audioError = "";
      try {
        if (player.audioHash !== chunk.audio_sha256 || !lectureUrl) {
          const blob = await api.verifiedAudio(lesson.job_id, chunk);
          if (state.player !== player || epoch !== playerEpoch) return false;
          if (lectureUrl) URL.revokeObjectURL(lectureUrl);
          lectureUrl = URL.createObjectURL(blob); lectureAudio.src = lectureUrl; player.audioHash = chunk.audio_sha256;
          lectureAudio.load();
        }
        lectureAudio.playbackRate = player.speed;
        try { lectureAudio.currentTime = Math.max(0, Math.min(offset, chunk.duration_seconds)); } catch { /* loadedmetadata applies the saved offset. */ }
      } catch (error) {
        if ([401, 403, 404, 409, 410].includes(error.status)) throw error;
        player.audioError = error.message; player.audioHash = "";
        lectureAudio.removeAttribute("src"); if (lectureUrl) URL.revokeObjectURL(lectureUrl); lectureUrl = null;
      }
      scheduleExpiry();
      if (state.page === "learn") renderPlayer();
      if (autoplay && lectureUrl && state.page === "learn" && !state.questionOpen) {
        try { await lectureAudio.play(); } catch { toast("Your browser paused autoplay. Press Play to start the narration."); }
      }
      return true;
    } catch (error) {
      if (state.player === player && epoch === playerEpoch) await invalidatePlayer(error.message);
      return false;
    } finally { player.busy = false; if (state.player === player && state.page === "learn") renderPlayer(); }
  }
  async function invalidatePlayer(message, reason = "ERROR") {
    const player = state.player; if (!player) return;
    player.invalid = message; releasePlayerMedia(); clearQuestion();
    await endLearning(reason, false);
  }
  async function endLearning(reason = "STOPPED_BY_LEARNER", back = true) {
    const player = state.player; if (!player) return true;
    releasePlayerMedia(); clearQuestion();
    if (player.closed) { if (back) state.player = null; if (state.page === "learn") renderLearn(); return true; }
    if (!player.endPending) player.endPending = { reason, back }; else if (back) player.endPending.back = true;
    if (player.ending) return false;
    player.ending = true; if (state.page === "learn") renderPlayer();
    try {
      try { await api.request(`/v1/sessions/${player.session.session_id}/end`, { method: "POST", json: { reason: player.endPending.reason }, headers: sessionHeaders(player) }); }
      catch (error) { if (![404, 410].includes(error.status)) throw error; }
      player.closed = true; player.token = "";
      const completed = player.endPending.reason === "COMPLETED", returnToCourses = player.endPending.back;
      player.endPending = null; player.endError = "";
      if (returnToCourses) state.player = null; else if (completed) player.complete = true;
      if (state.page === "learn") renderLearn();
      return true;
    } catch (error) { player.endError = error.message; if (state.page === "learn") renderPlayer(); return false; }
    finally { player.ending = false; if (state.page === "learn") renderLearn(); }
  }
  function renderPlayer() {
    const player = state.player; if (!player) { renderLearn(); return; }
    if (player.complete) { renderCompletion(); return; }
    if (player.endPending || player.invalid || player.closed) {
      main.innerHTML = `<section class="card complete-card">${icon("info")}<h1>${player.endPending ? "Closing your session" : "Time for a fresh start"}</h1><p>${escape(player.invalid || "Playback has stopped and your temporary question history is being cleared.")}</p>${player.endError ? errorBox(player.endError) : ""}<button class="button" data-action="${player.endPending ? "retry-session-end" : "leave-player"}" ${player.ending ? "disabled" : ""}>${player.endPending ? player.ending ? "Closing…" : "Retry session end" : "Back to courses"}</button></section>`; return;
    }
    const lesson = player.lesson, parts = lesson.chunks, part = parts[player.index];
    main.innerHTML = `<button class="back-button" data-action="leave-player">${icon("back")}Back to courses</button><div class="player-top"><div><p class="eyebrow">YOUR LEARNING MOMENT</p><h1>${escape(lesson.lesson_id)}</h1><div class="player-meta"><span>${escape(levelLabel(lesson.learner_level))}</span><span>·</span><span>${escape(languageLabel(lesson.language))}</span><span>·</span>${badge("AI-generated draft · Human review required", "badge-amber")}</div></div><button class="button button-secondary button-sm" data-action="leave-player">End lesson</button></div><p class="ai-notice">${icon("sparkles")}${escape(player.session.ai_notice || "You are interacting with an AI teacher.")}</p>
      <div class="player-grid"><aside class="card parts-card" aria-label="Course contents"><h2 class="parts-title">Your learning path <span>Part ${player.index + 1} of ${parts.length}</span></h2><div class="progress-track"><div class="progress-fill" style="width:${(player.index + 1) / parts.length * 100}%"></div></div><ol class="part-list">${parts.map((chunk, index) => `<li><button class="part-button ${index === player.index ? "active" : ""}" data-action="go-part" data-index="${index}" ${index === player.index ? 'aria-current="step"' : ""} ${player.busy ? "disabled" : ""}><span class="part-number">${player.visited.has(index) && index !== player.index ? icon("check") : String(index + 1).padStart(2, "0")}</span><span>${escape(partTitle(chunk))}<small>${time(chunk.duration_seconds)}</small></span></button></li>`).join("")}</ol></aside>
      <div><article class="card lesson-card"><div class="lesson-heading"><p class="eyebrow">PART ${String(player.index + 1).padStart(2, "0")} <span>/</span> ${escape(levelLabel(lesson.learner_level))}</p>${badge(player.busy ? "Checking availability…" : "Your course", "badge-neutral")}</div><h2>${escape(partTitle(part))}</h2>${textParagraphs(part.text)}<p class="tip-line">${icon("document")}${escape(sourceReferences(part.sources))}</p>
      <div class="audio-player" aria-label="Course narration"><div class="audio-top"><button class="play-button" data-action="toggle-play" aria-label="${lectureAudio.paused ? "Play narration" : "Pause narration"}" ${player.busy ? "disabled" : ""}>${icon(player.busy ? "loader" : lectureAudio.paused ? "play" : "pause", player.busy ? "spinner" : "")}</button><div class="audio-timeline"><label class="sr-only" for="playback-position">Narration position</label><input id="playback-position" type="range" min="0" max="${part.duration_seconds}" value="${player.offset}" step="0.01" ${player.busy ? "disabled" : ""}><div class="audio-time"><span id="current-time">${time(player.offset)}</span><span>${time(part.duration_seconds)}</span></div></div><button class="speed-button" data-action="playback-speed" aria-label="Playback speed ${player.speed} times">${player.speed}×</button><button class="icon-button" data-action="replay" aria-label="Replay narration" ${player.busy ? "disabled" : ""}>${icon("replay")}</button></div><p class="audio-caption">${icon(player.audioError ? "info" : "check")}${escape(player.audioError || `Verified narration · ${lesson.voice}`)}</p></div>
      <div class="lesson-navigation"><button class="button button-secondary" data-action="previous-part" ${player.index === 0 || player.busy ? "disabled" : ""}>${icon("back")}Previous part</button><button class="button" data-action="${player.index === parts.length - 1 ? "finish-course" : "next-part"}" ${player.busy ? "disabled" : ""}>${player.index === parts.length - 1 ? "Finish lesson" : "Next part"}${icon(player.index === parts.length - 1 ? "check" : "arrow")}</button></div></article>
      <section class="card question-card" aria-labelledby="teacher-title"><div class="question-card-head"><div class="teacher-intro"><span class="teacher-avatar">${icon("sparkles")}</span><div><h3 id="teacher-title">A little more clarity?</h3><p>${player.questionsEnabled ? "Pause here and ask your AI teacher." : "Questions were not enabled for this session."}</p></div></div><button class="button button-soft button-sm" data-action="toggle-question" aria-expanded="${state.questionOpen}" aria-controls="question-content" ${player.questionsEnabled && !player.busy ? "" : "disabled"}>${icon(state.questionOpen ? "close" : "chat")}${state.questionOpen ? "Close questions" : "Ask a question"}</button></div><div id="question-content" class="question-content" ${state.questionOpen ? "" : "hidden"}>${questionContent()}</div></section><p class="tip-line">${icon("info")}Progress stays in this tab. Session ends at ${escape(new Date(player.session.expires_at).toLocaleTimeString())}.</p></div></div>`;
  }
  function updatePlayback() {
    const player = state.player; if (!player || state.page !== "learn") return;
    const button = document.querySelector('[data-action="toggle-play"]');
    if (button && !player.busy) { button.setAttribute("aria-label", lectureAudio.paused ? "Play narration" : "Pause narration"); button.innerHTML = icon(lectureAudio.paused ? "play" : "pause"); }
    const range = document.querySelector("#playback-position"); if (range) { range.value = player.offset; document.querySelector("#current-time").textContent = time(player.offset); }
  }
  function answerAudioLabel() {
    if (!replyAudio.paused && !replyAudio.ended) return "Pause answer";
    return replyAudio.currentTime > 0 && !replyAudio.ended ? "Resume answer" : "Listen to answer";
  }
  function answerAudioControls() {
    const label = answerAudioLabel();
    return `<div class="answer-audio-controls" role="group" aria-label="Answer audio"><button class="button button-secondary button-sm" type="button" data-action="listen-answer" data-audio-label="${label}" aria-pressed="${!replyAudio.paused && !replyAudio.ended}">${icon(label === "Pause answer" ? "pause" : "headphones")}${label}</button><button class="button button-secondary button-sm" type="button" data-action="stop-answer" ${replyAudio.paused && !replyAudio.currentTime ? "disabled" : ""}>${icon("stop")}Stop answer</button><button class="button button-secondary button-sm" type="button" data-action="replay-answer">${icon("replay")}Replay answer</button></div>`;
  }
  function updateReplyPlayback() {
    const button = document.querySelector('[data-action="listen-answer"]');
    if (button) {
      const label = answerAudioLabel();
      button.setAttribute("aria-pressed", String(!replyAudio.paused && !replyAudio.ended));
      if (button.dataset.audioLabel !== label) { button.dataset.audioLabel = label; button.innerHTML = `${icon(label === "Pause answer" ? "pause" : "headphones")}${label}`; }
    }
    const stop = document.querySelector('[data-action="stop-answer"]');
    if (stop) stop.disabled = replyAudio.paused && !replyAudio.currentTime;
  }
  async function playAnswer(restart = false) {
    const player = state.player, url = replyUrl;
    if (!url || !state.turn?.response || !state.questionOpen || state.page !== "learn" || !player || player.closed || player.invalid) return;
    lectureAudio.pause();
    const epoch = ++replyPlaybackEpoch;
    try {
      if (restart || replyAudio.ended) { replyAudio.pause(); replyAudio.currentTime = 0; }
      await replyAudio.play();
    } catch {
      if (epoch === replyPlaybackEpoch && replyUrl === url && state.player === player) toast("The answer audio could not be played in this browser.");
    } finally { updateReplyPlayback(); }
  }
  function askAnotherQuestion() {
    if (!state.player || state.questionPending) return;
    pausePlayback(); releaseReply(); state.question = ""; if (state.turn?.error) state.turn = null;
    // Keep questionOffset: every new request refers to the same paused moment.
    state.questionOpen = true; updateQuestionPanel();
    document.querySelector("#question-input")?.focus();
  }
  function questionBubble(text) {
    return `<div class="question-bubble"><span>You</span>${textParagraphs(text, "question-text")}</div>`;
  }
  function answerBubble(reply) {
    return `<div class="answer-bubble"><span>Your AI teacher</span>${reply.text ? textParagraphs(reply.text, "answer-text") : ""}${reply === state.turn?.response && replyUrl ? answerAudioControls() : ""}${reply.playback_action ? `<p class="small-copy">Playback instruction: ${escape(({ REPLAY: "Replay this part", RESUME: "Resume this part", PAUSE: "Keep the lesson paused", SET_SPEED: `Set speed to ${reply.playback_action.rate}×` })[reply.playback_action.action] || "Continue")}</p>` : ""}${reply.sources?.length ? `<p class="small-copy">${escape(sourceReferences(reply.sources))}</p>` : ""}${reply.grounded == null ? `<p class="small-copy">Source grounding has not been independently verified.</p>` : `<p class="small-copy">Source support: ${reply.grounded ? "verified" : "not verified"}.</p>`}${reply.warnings?.length ? `<p class="small-copy">${escape(reply.warnings.filter(warning => warning !== "GROUNDING_NOT_VERIFIED").map(warning => warning.replace(/_/g, " ").toLowerCase()).join(" · "))}</p>` : ""}</div>`;
  }
  function questionContent() {
    const turn = state.turn;
    const history = state.questionHistory.map(exchange => questionBubble(exchange.question) + answerBubble(exchange.response)).join("");
    return `<div class="question-history" role="log" aria-label="This interruption's conversation" aria-live="polite" aria-relevant="additions text" tabindex="0">${history}${turn && !turn.response ? questionBubble(turn.body.text) : ""}${state.questionPending ? `<p class="small-copy" role="status">Your teacher is thinking…</p>` : ""}</div>
      <form class="question-form" id="question-form"><label for="question-input">What would you like to understand?</label><textarea id="question-input" maxlength="6000" placeholder="Ask a question about this part…" required ${state.questionPending ? "disabled" : ""}>${escape(state.question)}</textarea><div class="question-suggestions"><button type="button" data-action="suggest-question" data-question="Explain this more simply" ${state.questionPending ? "disabled" : ""}>Explain this more simply</button><button type="button" data-action="suggest-question" data-question="Give me an example" ${state.questionPending ? "disabled" : ""}>Give me an example</button><button type="button" data-action="suggest-question" data-question="What is the key takeaway?" ${state.questionPending ? "disabled" : ""}>What is the key takeaway?</button></div><div class="form-bottom"><span>${icon("info")}Your lesson is paused at ${time(state.questionOffset)}</span><div class="question-actions">${turn?.error ? `<button class="button button-secondary button-sm" type="button" data-action="retry-question" ${state.questionPending ? "disabled" : ""}>Retry question</button>` : ""}<button class="button button-sm" type="submit" ${state.questionPending ? "disabled" : ""}>${icon(state.questionPending ? "loader" : "send", state.questionPending ? "spinner" : "")}${state.questionPending ? "Your teacher is thinking…" : "Send question"}</button></div></div>${turn?.error ? errorBox(turn.error) : ""}</form>
      ${state.questionHistory.length ? `<div class="answer-actions"><button class="button button-secondary button-sm" type="button" data-action="ask-another-question" ${state.questionPending ? "disabled" : ""}>${icon("chat")}Ask another question</button><button class="button button-secondary button-sm" type="button" data-action="resume-learning">${icon("play")}Continue learning</button></div>` : ""}`;
  }
  function updateQuestionPanel() {
    const panel = document.querySelector("#question-content"); if (panel) { panel.hidden = !state.questionOpen; panel.innerHTML = questionContent(); }
    const history = panel?.querySelector(".question-history"); if (history) history.scrollTop = history.scrollHeight;
    updateReplyPlayback();
    const button = document.querySelector('[data-action="toggle-question"]'); if (button) { button.setAttribute("aria-expanded", String(state.questionOpen)); button.innerHTML = `${icon(state.questionOpen ? "close" : "chat")}${state.questionOpen ? "Close questions" : "Ask a question"}`; }
  }
  function openQuestions() {
    const player = state.player; if (!player || player.busy || !player.questionsEnabled) return;
    pausePlayback();
    state.questionOffset = Number.isFinite(lectureAudio.currentTime) && lectureUrl ? lectureAudio.currentTime : player.offset;
    state.questionOpen = true; updateQuestionPanel(); document.querySelector("#question-input")?.focus();
  }
  async function sendQuestion(retry = false) {
    const player = state.player;
    if (!player || state.questionPending || player.closed || player.invalid || player.busy) return;
    if (expired(player.session.expires_at)) { await invalidatePlayer("This learning session has expired.", "TIMEOUT"); return; }
    const question = state.question.trim();
    if (!question) { toast("Write a question or choose one of the suggestions."); return; }
    if (!retry || !state.turn) {
      releaseReply();
      state.turn = { body: { request_id: crypto.randomUUID(), chunk_id: player.lesson.chunks[player.index].chunk_id, text: question, playback_offset_ms: Math.round(state.questionOffset * 1000) }, error: "", response: null };
    }
    const turn = state.turn;
    state.questionPending = true; turn.error = ""; pausePlayback(); updateQuestionPanel();
    questionController = new AbortController();
    try {
      const reply = await api.request(`/v1/sessions/${player.session.session_id}/interrupts`, { method: "POST", json: turn.body, headers: sessionHeaders(player), timeout: 610000, signal: questionController.signal });
      if (state.player !== player || state.turn !== turn || player.closed || player.endPending) return;
      if (reply.request_id !== turn.body.request_id || reply.chunk_id !== turn.body.chunk_id) throw new Error("The teacher reply did not match this question. Retry the same question.");
      // Only the newest answer keeps audio; the visible transcript retains text/metadata.
      turn.response = { ...reply, audio: null, ssml: null, visemes: [] };
      state.questionHistory.push({ question: turn.body.text, response: turn.response });
      state.question = "";
      if (reply.audio?.encoding === "base64" && reply.audio.media_type === "audio/wav") {
        try {
          const bytes = Uint8Array.from(atob(reply.audio.data), character => character.charCodeAt(0));
          releaseReply(); replyUrl = URL.createObjectURL(new Blob([bytes], { type: "audio/wav" })); replyAudio.src = replyUrl; replyAudio.load();
        } catch { toast("The answer text is ready, but its audio could not be opened."); }
      }
    } catch (error) {
      if (error.name === "AbortError" || state.player !== player || state.turn !== turn) return;
      turn.error = error.message;
      if ([404, 410].includes(error.status) || ["SESSION_EXPIRED", "INVALID_SESSION", "SESSION_RESULT_CHANGED"].includes(error.code)) await invalidatePlayer(error.message);
    } finally {
      if (state.turn === turn) { state.questionPending = false; if (state.page === "learn") updateQuestionPanel(); }
    }
  }
  async function resumeLearning() {
    const player = state.player; if (!player) return;
    const action = state.questionHistory.at(-1)?.response?.playback_action;
    const offset = action?.action === "REPLAY" ? 0 : state.questionOffset;
    if (action?.action === "SET_SPEED" && action.rate >= .5 && action.rate <= 2) player.speed = action.rate;
    clearQuestion(); await loadPart(player.index, { autoplay: action?.action !== "PAUSE", offset });
  }
  function renderCompletion() {
    const player = state.player;
    main.innerHTML = `<section class="card complete-card"><div class="complete-symbol">${icon("check")}</div><p class="eyebrow" style="justify-content:center">A LITTLE FURTHER THAN BEFORE</p><h1>That’s a wrap.<br><em>Keep the curiosity.</em></h1><p>You’ve reached the end of<br><strong>${escape(player.lesson.lesson_id)}</strong>.</p><div class="complete-stats"><div><strong>${player.lesson.chunks.length}</strong><span>Course parts</span></div><div><strong>${escape(levelLabel(player.lesson.learner_level))}</strong><span>Your chosen level</span></div></div><button class="button" data-action="leave-player">Explore more courses ${icon("arrow")}</button><p class="preview-note" style="justify-content:center;margin-top:22px">${icon("check")}Your session is closed. Progress is held only in this tab; no certificate is issued.</p></section>`;
  }

  async function reviseCourse(id) {
    openModal(`${modalHeader("Loading the current course…", "REQUEST CHANGES")}<p class="modal-copy">Checking the available parts before preparing your revision.</p>`);
    const epoch = modalEpoch;
    const revision = { id, bundle: null, level: state.resultLevel, index: state.resultPart || 0, text: "", all: false, busy: false, error: "", uncertain: state.revisionUncertain.has(id) };
    state.revision = revision;
    try {
      revision.bundle = await fetchBundle(id);
      if (state.revision === revision && modal.open && epoch === modalEpoch) renderRevisionDialog();
    } catch (error) { if (state.revision === revision && modal.open && epoch === modalEpoch) modal.innerHTML = `${modalHeader("This course couldn’t be loaded")}${errorBox(error.message)}<button class="button button-secondary" data-action="close-modal">Close</button>`; }
  }
  function renderRevisionDialog() {
    const revision = state.revision;
    if (!revision?.bundle) return;
    const variants = variantsOf(revision.bundle);
    if (!variants[revision.level]) revision.level = variants.intermediate ? "intermediate" : Object.keys(variants)[0];
    const lesson = variants[revision.level]; revision.index = Math.min(revision.index, lesson.chunks.length - 1);
    modal.innerHTML = `${modalHeader("Make room for a better explanation", "REQUEST CHANGES")}<p class="modal-copy">${escape(lesson.lesson_id)}<br>Successful processing replaces the selected part’s text and narration in this course.</p>${revision.uncertain ? errorBox("The previous revision may already have been accepted. No automatic retry is available for revisions. Check your course before preparing another request.") + `<div class="modal-actions"><button class="button button-secondary" data-action="preview-course" data-id="${escape(revision.id)}">Check current course</button><button class="button" data-action="prepare-new-revision">I checked; prepare new request</button></div>` : `<form id="revision-form"><fieldset class="plain-fieldset" ${revision.busy ? "disabled" : ""}><div class="form-grid"><div class="field"><label for="revision-level">Learning level</label><select id="revision-level">${Object.keys(variants).map(level => `<option value="${escape(level)}" ${level === revision.level ? "selected" : ""}>${escape(levelLabel(level))}</option>`).join("")}</select></div><div class="field"><label for="revision-part">Course part</label><select id="revision-part">${lesson.chunks.map((chunk, index) => `<option value="${index}" ${index === revision.index ? "selected" : ""}>Part ${index + 1}</option>`).join("")}</select></div></div><div class="revision-excerpt">${escape(lesson.chunks[revision.index].text.slice(0, 450))}</div><div class="field" style="margin-top:18px"><label for="revision-text">What would you change?</label><textarea id="revision-text" maxlength="8000" placeholder="e.g. Add a practical example and explain the key term more simply." required>${escape(revision.text)}</textarea></div>${Object.keys(variants).length > 1 ? `<label class="checkbox-label"><input id="revision-all" type="checkbox" ${revision.all ? "checked" : ""}>Apply this change to the corresponding part in every available level</label>` : ""}<p class="modal-disclaimer">The revision uses the existing language and voice. It keeps the course’s part order and requires human review after completion.</p><div class="modal-actions"><button class="button button-secondary" type="button" data-action="close-modal">Cancel</button><button class="button" type="submit">${icon(revision.busy ? "loader" : "edit", revision.busy ? "spinner" : "")}${revision.busy ? "Submitting…" : "Submit revision"}</button></div></fieldset>${revision.error ? errorBox(revision.error) : ""}</form>`}`;
    const close = modal.querySelector('[data-action="close-modal"]'); if (close) close.disabled = revision.busy;
  }
  async function submitRevision(revision = state.revision) {
    if (!revision || revision.busy || revision.uncertain) return;
    const inline = revision.origin === "review";
    if (inline && (!revision.bundle.revision_available || expired(revision.bundle.audio_expires_at)
      || reviewJobs(revision.id).some(job => !terminal.has(job.status)) || state.revisionUncertain.has(revision.id))) { renderReview(); return; }
    const input = document.querySelector(inline ? "#review-feedback" : "#revision-text");
    const review = input.value.trim();
    if (!review) { input.value = ""; input.reportValidity(); return; }
    revision.text = review;
    const lesson = variantsOf(revision.bundle)[revision.level];
    const body = { lesson_job_id: revision.id, chunk_id: lesson.chunks[revision.index].chunk_id, teacher_review: review, apply_to_all_levels: revision.all };
    const draftKey = inline ? reviewDraftKey(revision) : "";
    revision.busy = true; revision.error = "";
    if (inline) { rememberReviewDraft(revision); renderReview(); } else renderRevisionDialog();
    try {
      const accepted = await api.request("/v1/audio:regenerate", { method: "POST", json: body });
      if (!uuidPattern.test(accepted?.job_id)) { revision.uncertain = true; throw new Error("The revision response could not be read. It may have been accepted."); }
      const job = { job_id: accepted.job_id, targetId: revision.id, title: inline ? revision.bundle.lesson_id : lesson.lesson_id, level: revision.level, partIndex: revision.index, status: "QUEUED", step: "QUEUED", progress: 0, type: "revision", trackingError: "", reviewDraftKey: draftKey, reviewText: inline ? review : "" };
      state.jobs.set(job.job_id, job);
      revision.busy = false;
      if (!inline) { state.activeJobId = job.job_id; state.generatedId = null; modal.close(); navigate("generate"); }
      await refreshJob(job); schedulePoll();
    } catch (error) {
      revision.error = error.message; revision.uncertain = revision.uncertain || Boolean(error.uncertain);
      if (revision.uncertain) state.revisionUncertain.add(revision.id);
    } finally {
      revision.busy = false;
      if (inline && state.page === "review" && state.reviewEditor === revision) renderReview();
      else if (!inline && modal.open && state.revision === revision) renderRevisionDialog();
    }
  }
  function connectionInfo() {
    openModal(`${modalHeader("Your UPRAISE connection", "WORKSPACE SETTINGS")}<p class="modal-copy">${state.connected ? "Connected to your backend." : "Your backend connection needs attention."}<br>${state.local ? "Local development draft preview is active." : "Production gateway authentication and signed learner sessions apply."}</p><div class="field"><label>Backend address</label><p class="preview-text">${escape(state.config?.backend_url || "Not connected")}</p></div><p class="small-copy">Connection credentials are configured on the UPRAISE server. Browser requests stay on this address; service tokens and TLS private keys are not sent to the page.</p><p class="small-copy">Course text and narration use the backend’s existing retention policy. Generated material remains a draft requiring human review.</p><div class="modal-actions"><button class="button button-secondary" data-action="close-modal">Close</button><button class="button" data-action="refresh-connection">Refresh connection ${icon("replay")}</button></div>`);
  }
  async function runAction(action, control) {
    const id = control?.dataset.id;
    if (action === "close-modal") { if (!state.startPending?.busy && !state.revision?.busy) modal.close(); return; }
    if (action === "connection-info") { connectionInfo(); return; }
    if (action === "refresh-connection") { modal.close(); await refreshWorkspace(); return; }
    if (action === "refresh-workspace") { await refreshWorkspace(); return; }
    if (action === "review-course") { await openReview(id, control.dataset.level || "", Number(control.dataset.part || 0)); return; }
    if (action === "review-back") {
      if (state.reviewEditor?.busy) return;
      if (state.reviewEditor?.bundle) rememberReviewDraft(state.reviewEditor);
      state.reviewEditor = null; renderReview(); return;
    }
    if (action === "reload-review") { const editor = state.reviewEditor; if (editor) await openReview(editor.id, editor.level, editor.index); return; }
    if (action === "refresh-reviews" || action === "load-more-reviews") {
      control.disabled = true;
      await loadReviews(action === "load-more-reviews");
      if (state.page === "review") renderReview();
      return;
    }
    if (action === "prepare-new-review") {
      const editor = state.reviewEditor;
      if (editor) { state.revisionUncertain.delete(editor.id); editor.uncertain = false; editor.error = ""; renderReview(); }
      return;
    }
    if (action === "review-listen") { await listenReview(control); return; }
    if (action === "review-stop") { releaseReviewAudio(); return; }
    if (action === "review-previous" || action === "review-next") {
      const editor = state.reviewEditor;
      if (!editor?.bundle || editor.busy) return;
      const index = editor.index + (action === "review-next" ? 1 : -1);
      if (index >= 0 && index < editor.bundle.variants[editor.level].chunks.length) { selectReviewPart(editor, editor.level, index); renderReview(); }
      return;
    }
    if (action === "create-from-material") { selectedSource(id); state.activeJobId = null; state.generatedId = null; navigate("generate"); return; }
    if (action === "remove-material") { removeMaterialDialog(id); return; }
    if (action === "confirm-remove" || action === "retry-removal") { await confirmRemove(); return; }
    if (action === "new-generation") { state.activeJobId = null; state.generatedId = null; state.generationAttempt = null; state.generationError = ""; navigate("generate"); return; }
    if (action === "cancel-generation") { await cancelGeneration(id); return; }
    if (action === "open-tracking") { state.activeJobId = id; state.generatedId = null; navigate("generate"); return; }
    if (action === "retry-tracking") {
      const job = state.page === "review" ? reviewJobs(state.reviewEditor?.id).at(-1) : state.jobs.get(state.activeJobId);
      if (job) { job.trackingError = ""; await refreshJob(job); schedulePoll(); }
      return;
    }
    if (action === "open-job-result") {
      const job = state.jobs.get(id); if (!job) return;
      const targetId = job.type === "revision" ? job.targetId : job.job_id;
      if (state.page === "review") { await openReview(targetId, job.level, job.partIndex); return; }
      await fetchBundle(targetId); state.generatedId = targetId; state.activeJobId = null; renderGenerate(); return;
    }
    if (action === "preview-course") { await previewCourse(id); return; }
    if (action === "start-course") { await startCourseDialog(id); return; }
    if (action === "revise-course") { await reviseCourse(id); return; }
    if (action === "prepare-new-revision") { state.revisionUncertain.delete(state.revision.id); state.revision.uncertain = false; state.revision.error = ""; renderRevisionDialog(); return; }
    if (action === "load-more") { control.disabled = true; try { await loadCourses(true); renderLearn(); } finally { if (control.isConnected) control.disabled = false; } return; }
    if (action === "leave-player") { await endLearning("STOPPED_BY_LEARNER", true); return; }
    if (action === "retry-session-end") { await endLearning(state.player?.endPending?.reason || "ERROR", state.player?.endPending?.back); return; }
    if (action === "finish-course") { await endLearning("COMPLETED", false); return; }
    const player = state.player;
    if (!player || player.closed || player.endPending || player.invalid) return;
    if (action === "go-part") { await loadPart(Number(control.dataset.index)); return; }
    if (action === "next-part") { await loadPart(player.index + 1); return; }
    if (action === "previous-part") { await loadPart(player.index - 1); return; }
    if (action === "toggle-play") {
      if (!lectureAudio.paused) pausePlayback();
      else if (state.questionOpen) await resumeLearning();
      else await loadPart(player.index, { autoplay: true, offset: player.offset >= player.lesson.chunks[player.index].duration_seconds ? 0 : player.offset });
      return;
    }
    if (action === "replay") { clearQuestion(); await loadPart(player.index, { autoplay: true }); return; }
    if (action === "playback-speed") { const speeds = [1, 1.25, 1.5, 2]; player.speed = speeds[(speeds.indexOf(player.speed) + 1) % speeds.length]; lectureAudio.playbackRate = player.speed; control.textContent = `${player.speed}×`; control.setAttribute("aria-label", `Playback speed ${player.speed} times`); return; }
    if (action === "toggle-question") { if (state.questionOpen) { clearQuestion(); updateQuestionPanel(); } else openQuestions(); return; }
    if (action === "suggest-question" && !state.questionPending) { state.question = control.dataset.question; if (state.turn?.error) state.turn = null; pauseReply(); updateQuestionPanel(); document.querySelector("#question-input").focus(); return; }
    if (action === "retry-question") { await sendQuestion(true); return; }
    if (action === "resume-learning") { await resumeLearning(); return; }
    if (action === "ask-another-question") { askAnotherQuestion(); return; }
    if (action === "listen-answer") { if (replyAudio.paused || replyAudio.ended) await playAnswer(); else pauseReply(); return; }
    if (action === "stop-answer") { pauseReply(); try { replyAudio.currentTime = 0; } catch { /* Media may already be unloaded. */ } updateReplyPlayback(); return; }
    if (action === "replay-answer") { await playAnswer(true); return; }
  }

  document.addEventListener("submit", event => {
    event.preventDefault();
    const form = event.target;
    const action = form.id === "upload-form" ? () => uploadMaterial(form) : form.id === "generation-form" ? () => startGeneration(form) : form.id === "start-form" ? () => startCourse(form) : form.id === "question-form" ? () => sendQuestion(Boolean(state.turn?.error)) : form.id === "revision-form" ? submitRevision : form.id === "review-form" ? () => submitRevision(state.reviewEditor) : null;
    if (action) void action().catch(error => toast(error.message));
  });
  document.addEventListener("click", event => {
    const control = event.target.closest("[data-action]");
    if (control && !control.disabled) void runAction(control.dataset.action, control).catch(error => toast(error.message));
  });
  document.addEventListener("input", event => {
    const target = event.target;
    if (target.id === "material-search") { state.search = target.value; updateMaterialList(); }
    if (target.id === "course-search") { state.courseSearch = target.value; document.querySelector("#course-results").innerHTML = courseCards(); }
    if (target.id === "review-search") { state.reviewSearch = target.value; document.querySelector("#review-results").innerHTML = reviewCards(); }
    if (target.id === "review-feedback" && state.reviewEditor) { state.reviewEditor.text = target.value; rememberReviewDraft(state.reviewEditor); }
    if (target.id === "upload-course") state.uploadName = target.value;
    if (target.id === "upload-activity") state.activityId = target.value;
    if (target.id === "course-title") { state.draft.title = target.value; document.querySelector("#summary-title").textContent = target.value || "Your next learning experience"; }
    if (target.id === "target-words") state.draft.words = Number(target.value);
    if (target.id === "source-window") state.draft.window = Number(target.value);
    if (target.id === "max-words") state.draft.maximum = Number(target.value);
    if (target.id === "session-jwt") state.sessionToken = target.value;
    if (target.id === "revision-text" && state.revision) state.revision.text = target.value;
    if (target.id === "question-input" && !state.questionPending) { state.question = target.value; if (state.turn?.error && state.question.trim() !== state.turn.body.text) state.turn = null; pauseReply(); }
    if (target.id === "playback-position" && state.player) {
      const player = state.player;
      if (player.seekPlaying === undefined) player.seekPlaying = !lectureAudio.paused;
      pausePlayback(); player.offset = Number(target.value); updatePlayback();
    }
  });
  document.addEventListener("change", event => {
    const target = event.target;
    if (target.name === "source") { selectedSource(target.value); renderGenerate(); document.querySelector(`input[name="source"][value="${state.selectedMaterial}"]`)?.focus(); }
    if (target.id === "upload-language") state.uploadLanguage = target.value;
    if (target.id === "course-language") { state.draft.language = target.value; state.draft.voice = target.value === "it" ? "Bianca" : "Joanna"; document.querySelector("#course-voice").innerHTML = voiceOptions(target.value); }
    if (target.id === "course-voice") state.draft.voice = target.value;
    if (target.id === "teaching-version") state.draft.teachingVersion = target.value;
    if (target.name === "levels" && target.closest("#generation-form")) {
      state.draft.levels = [...target.form.querySelectorAll('input[name="levels"]:checked')].map(input => input.value);
      document.querySelector("#generation-level-error").hidden = Boolean(state.draft.levels.length);
      target.form.querySelector('[type="submit"]').disabled = !state.draft.levels.length;
    }
    if (target.id === "result-level") { state.resultLevel = target.value; state.resultPart = 0; renderGenerate(); }
    if (target.id === "result-part") { state.resultPart = Number(target.value); renderGenerate(); }
    if (target.id === "revision-level") { state.revision.level = target.value; state.revision.index = 0; renderRevisionDialog(); }
    if (target.id === "revision-part") { state.revision.index = Number(target.value); renderRevisionDialog(); }
    if (target.id === "revision-all") state.revision.all = target.checked;
    if (target.id === "review-level" && state.reviewEditor && !state.reviewEditor.busy) { selectReviewPart(state.reviewEditor, target.value, 0); renderReview(); }
    if (target.id === "review-part" && state.reviewEditor && !state.reviewEditor.busy) { selectReviewPart(state.reviewEditor, state.reviewEditor.level, Number(target.value)); renderReview(); }
    if (target.id === "review-all" && state.reviewEditor) { state.reviewEditor.all = target.checked; rememberReviewDraft(state.reviewEditor); }
    if (target.id === "playback-position" && state.player) {
      const player = state.player, offset = Number(target.value), autoplay = Boolean(player.seekPlaying);
      delete player.seekPlaying; clearQuestion(); void loadPart(player.index, { autoplay, offset });
    }
  });
  modal.addEventListener("close", () => { if (!modal.open) { ++modalEpoch; if (modalTrigger?.isConnected) modalTrigger.focus(); } });
  modal.addEventListener("cancel", event => { if (state.startPending?.busy || state.revision?.busy || state.deletion?.busy) event.preventDefault(); });
  modal.addEventListener("click", event => {
    if (event.target !== modal || state.startPending?.busy || state.revision?.busy || state.deletion?.busy) return;
    const rect = modal.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) modal.close();
  });
  lectureAudio.addEventListener("loadedmetadata", () => { if (state.player && lectureUrl) { try { lectureAudio.currentTime = state.player.offset; } catch { /* Browser will allow seeking after metadata arrives. */ } } });
  lectureAudio.addEventListener("timeupdate", () => { if (state.player && lectureUrl && !state.player.busy && state.player.seekPlaying === undefined) { state.player.offset = lectureAudio.currentTime; updatePlayback(); } });
  for (const event of ["play", "pause", "ended"]) lectureAudio.addEventListener(event, updatePlayback);
  for (const event of ["play", "pause", "ended", "timeupdate", "loadedmetadata"]) replyAudio.addEventListener(event, updateReplyPlayback);
  window.addEventListener("hashchange", () => { render(); main.focus({ preventScroll: true }); window.scrollTo({ top: 0, behavior: "instant" }); });
  window.addEventListener("upraise-auth-mode", event => { state.local = event.detail === "local-test"; updateConnection(); });
  document.addEventListener("visibilitychange", () => { if (document.hidden) pausePlayback(); });
  window.addEventListener("pagehide", () => {
    releaseReviewAudio();
    pausePlayback(); questionController?.abort();
    const player = state.player;
    if (player && !player.closed && player.session) void api.request(`/v1/sessions/${player.session.session_id}/end`, { method: "POST", json: { reason: player.endPending?.reason || "STOPPED_BY_LEARNER" }, headers: sessionHeaders(player), keepalive: true, timeout: 10000 }).catch(() => {});
    // A browser may restore this document from its back/forward cache after B5.
    // Such a document must never resume the session it already closed on exit.
    releasePlayerMedia(); clearQuestion();
    if (player && !player.complete) { player.closed = true; player.invalid = "This page was left and its learning session was closed. Start a fresh session to continue."; player.token = ""; player.endPending = null; }
  });
  window.addEventListener("pageshow", event => { if (event.persisted) render(); });
  render();
  void refreshWorkspace();
})();
