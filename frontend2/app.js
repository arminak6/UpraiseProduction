/* UPRAISE visual prototype. All state is in memory; no backend or network calls. */
(() => {
  "use strict";

  const fixtures = window.UPRAISE_DEMO;
  const main = document.querySelector("#main");
  const modal = document.querySelector("#modal");
  const toastElement = document.querySelector("#toast");
  const languages = { en: "English", it: "Italian" };
  const state = {
    materials: structuredClone(fixtures.materials),
    courses: structuredClone(fixtures.courses),
    selectedFile: null,
    selectedMaterial: "material-ai",
    uploadName: "",
    uploadLanguage: "en",
    draft: { title: "AI essentials for everyone", language: "en", voice: "Joanna", words: 200, window: 5 },
    generation: null,
    generatedId: null,
    player: null,
    search: "",
    courseSearch: "",
    question: "",
    answer: "",
    questionOpen: false,
    questionPending: false,
    page: "materials",
  };
  let toastTimer;
  let generationTimer;
  let playbackTimer;
  let questionTimer;
  let lastModalTrigger;
  const uploadTimers = new Set();

  const paths = {
    upload: '<path d="M12 16V4m-4 4 4-4 4 4M4 16v4h16v-4"/>',
    document: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h5"/>',
    book: '<path d="M12 5c-3-2-6-2-10-1v15c4-1 7-1 10 1 3-2 6-2 10-1V4c-4-1-7-1-10 1zm0 0v15"/>',
    sparkles: '<path d="m12 3 2.3 6.7L21 12l-6.7 2.3L12 21l-2.3-6.7L3 12l6.7-2.3L12 3zM20 2v4m-2-2h4M3 19v4m-2-2h4"/>',
    arrow: '<path d="M4 12h15m-6-6 6 6-6 6"/>',
    back: '<path d="M20 12H5m6-6-6 6 6 6"/>',
    check: '<path d="m5 12 4 4L19 6"/>',
    checkCircle: '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    globe: '<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18"/>',
    lock: '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 4v3"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/>',
    search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
    trash: '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/>',
    close: '<path d="m6 6 12 12M6 18 18 6"/>',
    layers: '<path d="m12 3 10 5-10 5L2 8l10-5zm-9 9 9 5 9-5m-18 5 9 5 9-5"/>',
    play: '<path d="m9 5 11 7-11 7z" fill="currentColor" stroke-width="1"/>',
    pause: '<path d="M8 5h2v14H8zm6 0h2v14h-2z" fill="currentColor" stroke-width="1"/>',
    replay: '<path d="M3 10a9 9 0 1 1 1 7M3 4v6h6"/>',
    chat: '<path d="M21 11a8 8 0 0 1-8 8H7l-5 3V11a9 9 0 0 1 19 0z"/><path d="M7 10h9M7 14h5"/>',
    send: '<path d="m22 2-7 20-4-9-9-4 20-7zm0 0L11 13"/>',
    headphones: '<path d="M4 14v-3a8 8 0 0 1 16 0v3"/><rect x="3" y="12" width="4" height="8" rx="2"/><rect x="17" y="12" width="4" height="8" rx="2"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    lightbulb: '<path d="M9 18v-1a7 7 0 1 1 6 0v1M9 21h6M9 18h6"/>',
    leaf: '<path d="M20 3C7 2 2 8 5 15c4 9 16 2 15-12zM5 20 16 8"/>',
    loader: '<path d="M21 12a9 9 0 1 1-6-8.5"/>',
    edit: '<path d="m16 3 5 5-12 12-6 1 1-6L16 3zm-2 2 5 5"/>',
    eye: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    flag: '<path d="M5 22V3m0 1c5-4 9 4 14 0v12c-5 4-9-4-14 0"/>',
  };

  function icon(name, className = "") {
    return `<svg class="icon ${className}" viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.info}</svg>`;
  }

  function escape(value) {
    return String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  }

  function languageLabel(code) { return languages[code] || code; }
  function getMaterial(id = state.selectedMaterial) { return state.materials.find(item => item.id === id); }
  function getCourse(id) { return state.courses.find(item => item.id === id); }
  function getParts(course) { return fixtures.lessons[course?.contentKey] || fixtures.lessons.ai; }
  function levelLabel(id) { return fixtures.levels.find(level => level.id === id)?.label || "Intermediate"; }
  function time(seconds) { return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`; }
  function fileIcon(material) { return `<span class="file-icon ${escape(material.format.toLowerCase())}">${escape(material.format)}</span>`; }
  function badge(text, type = "", symbol = "") { return `<span class="badge ${type}">${symbol ? icon(symbol) : ""}${escape(text)}</span>`; }
  function levelChips() { return `<div class="level-chips">${fixtures.levels.map(level => `<span class="level-chip">${icon("check")}${escape(level.label)}</span>`).join("")}</div>`; }

  function art(theme = "teal", index = "01") {
    return `<div class="course-art ${theme}"><span class="art-label">A new way to understand</span>
      <svg class="book-art" viewBox="0 0 120 110" fill="none" aria-hidden="true">
        <ellipse cx="60" cy="94" rx="41" ry="5" fill="currentColor" opacity=".08"/>
        <path d="M20 28c16-5 27 0 40 8 13-8 24-13 40-8v51c-16-4-27 1-40 9-13-8-24-13-40-9V28z" fill="white" fill-opacity=".65" stroke="currentColor" stroke-opacity=".4" stroke-width="1.5"/>
        <path d="M60 36v52M28 41c8-1 16 2 24 7m-24 3c8-1 16 2 24 7m-24 3c8-1 16 2 24 7m16-20c8-5 16-8 24-7m-24 17c8-5 16-8 24-7m-24 17c8-5 16-8 24-7" stroke="currentColor" stroke-opacity=".5" stroke-width="1.6" stroke-linecap="round"/>
        <path d="m87 10 2 6 6 2-6 2-2 6-2-6-6-2 6-2 2-6z" fill="currentColor" opacity=".7"/>
        <circle cx="28" cy="15" r="3" stroke="currentColor" stroke-opacity=".5"/><path d="M106 47h6m-3-3v6" stroke="currentColor" stroke-opacity=".5"/>
      </svg><span class="art-number">UPRAISE / ${index}</span></div>`;
  }

  function intro(step, label, title, copy, counts = false) {
    return `<div class="page-intro"><div><p class="eyebrow">${step} <span>/</span> ${label}</p><h1>${title}</h1><p class="intro-copy">${copy}</p></div>
      ${counts ? `<dl class="intro-count"><div><dd>${state.materials.length}</dd><dt>Materials</dt></div><div><dd>${state.courses.length}</dd><dt>Courses</dt></div><div><dd>3</dd><dt>Learning levels</dt></div></dl>` : ""}</div>`;
  }

  function toast(message) {
    clearTimeout(toastTimer);
    toastElement.textContent = message;
    toastElement.hidden = false;
    toastTimer = setTimeout(() => { toastElement.hidden = true; }, 4800);
  }

  function openModal(content) {
    if (modal.open) modal.close();
    lastModalTrigger = document.activeElement;
    modal.innerHTML = content;
    modal.showModal();
  }

  function modalHeader(title, eyebrow = "UPRAISE PREVIEW") {
    return `<div class="modal-top"><div><p class="eyebrow">${eyebrow}</p><h2 id="modal-title">${escape(title)}</h2></div><button class="icon-button" data-action="close-modal" aria-label="Close dialog">${icon("close")}</button></div>`;
  }

  modal.addEventListener("close", () => { if (lastModalTrigger?.isConnected) lastModalTrigger.focus(); });
  modal.addEventListener("click", event => { if (event.target === modal) { const rect = modal.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) modal.close(); } });

  function navigate(page) {
    if (location.hash === `#${page}`) render();
    else location.hash = page;
  }

  function selectMaterial(id) {
    if (state.generation && state.generation.materialId !== id) {
      clearInterval(generationTimer);
      state.generation = null;
    }
    state.selectedMaterial = id;
    const material = getMaterial();
    if (!material) return;
    state.draft.title = material.id === "material-ai" ? "AI essentials for everyone" : material.id === "material-responsible" ? "Responsible AI in practice" : `${material.course || material.name.replace(/\.[^.]+$/, "")} — introduction`;
    state.draft.language = material.language;
    state.draft.voice = material.language === "it" ? "Bianca" : "Joanna";
    state.generatedId = null;
  }

  function render() {
    const page = location.hash.slice(1);
    state.page = ["materials", "generate", "learn"].includes(page) ? page : "materials";
    if (state.page !== "learn") pausePlayback();
    for (const link of document.querySelectorAll("[data-nav]")) {
      const active = link.dataset.nav === state.page;
      link.classList.toggle("active", active);
      if (active) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
    }
    if (state.page === "materials") renderMaterials();
    if (state.page === "generate") renderGenerate();
    if (state.page === "learn") renderLearn();
    document.title = `UPRAISE · ${{ materials: "Your material", generate: "Generate course", learn: "Start learning" }[state.page]}`;
  }

  function renderMaterials() {
    main.innerHTML = `${intro("01", "YOUR MATERIAL", "Great learning starts<br>with <em>your material.</em>", "Bring your documents. We’ll help turn them into clear, engaging learning<br class=\"desktop-break\"> experiences, at a level that feels right.", true)}
      <div class="grid-upload">
        <section class="card card-padding" aria-labelledby="upload-title">
          <div class="card-title-row"><h2 id="upload-title">Add your teaching material</h2>${icon("upload")}</div>
          <form id="upload-form">
            <div class="drop-zone" id="drop-zone">
              <span class="upload-symbol">${icon("upload")}</span>
              <strong id="file-heading">${state.selectedFile ? escape(state.selectedFile.name) : "Drag your document here"}</strong>
              <p>or <label class="file-label" for="file-input" tabindex="0">browse files</label> to get started</p>
              <span class="file-note" id="file-note">${state.selectedFile ? `${escape(formatSize(state.selectedFile.size))} · Selected for preview` : "PDF, Word or CSV · Up to 100 MB"}</span>
              <input type="file" class="sr-only" id="file-input" accept=".pdf,.docx,.doc,.csv" aria-label="Choose a PDF, Word or CSV document">
            </div>
            <div class="form-grid"><div class="field"><label for="upload-course">Course name</label><input id="upload-course" name="course" maxlength="100" placeholder="e.g. Introduction to artificial intelligence" value="${escape(state.uploadName)}" required></div>
              <div class="field"><label for="upload-language">Document language</label><select id="upload-language" name="language">${languageOptions(state.uploadLanguage)}</select></div></div>
            <div class="form-bottom"><span>${icon("lock")}Files stay on your device in this preview</span><button class="button" type="submit">${icon("upload")}Upload material</button></div>
            <p class="inline-error" id="upload-error" role="alert" hidden></p>
          </form>
        </section>
        <aside class="journey-card" aria-label="How UPRAISE works"><p class="eyebrow">SMALL STEPS. NEW POSSIBILITIES.</p><h2>From something you have,<br>to something you know.</h2>
          <div class="journey-step"><span class="journey-icon">${icon("document")}</span><div><h3>Bring your knowledge</h3><p>Start with material you already trust.</p></div></div>
          <div class="journey-step"><span class="journey-icon">${icon("sparkles")}</span><div><h3>Make it a learning experience</h3><p>Clear explanations, at different levels.</p></div></div>
          <div class="journey-step"><span class="journey-icon">${icon("headphones")}</span><div><h3>Learn your way</h3><p>Read, listen, and ask along the way.</p></div></div>
          <p class="journey-footnote">${icon("leaf")}Built around people. Supported by AI.</p>
        </aside>
      </div>
      <section class="library" aria-labelledby="library-title"><div class="section-heading"><div><h2 id="library-title">Your material <span class="count-pill" id="material-count">${state.materials.length}</span></h2><p>A home for the knowledge your courses are built on.</p></div>
        <label class="search-field">${icon("search")}<span class="sr-only">Search material</span><input id="material-search" type="search" placeholder="Search material…" value="${escape(state.search)}"></label></div>
        <div id="material-results">${materialTable()}</div>
        <p class="tip-line">${icon("info")}These are sample documents. Add one of your own to preview the upload journey; its contents won’t be read.</p>
      </section>`;
    const fileInput = document.querySelector("#file-input");
    fileInput.addEventListener("change", () => chooseFile(fileInput.files[0]));
    const zone = document.querySelector("#drop-zone");
    zone.addEventListener("dragover", event => { event.preventDefault(); zone.classList.add("dragging"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("dragging"));
    zone.addEventListener("drop", event => { event.preventDefault(); zone.classList.remove("dragging"); if (event.dataTransfer.files.length > 1) showUploadError("Please choose one document at a time."); else chooseFile(event.dataTransfer.files[0]); });
    document.querySelector(".file-label").addEventListener("keydown", event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); fileInput.click(); } });
  }

  function languageOptions(selected) { return Object.entries(languages).map(([value, label]) => `<option value="${value}" ${value === selected ? "selected" : ""}>${label}</option>`).join(""); }
  function formatSize(bytes) { return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`; }
  function showUploadError(message) { const error = document.querySelector("#upload-error"); error.textContent = message; error.hidden = false; }

  function chooseFile(file) {
    if (!file) return;
    let error = "";
    if (!/\.(pdf|docx|doc|csv)$/i.test(file.name)) error = "Choose a PDF, Word (.docx or .doc), or CSV document.";
    else if (file.size === 0) error = "This file is empty. Please choose another document.";
    else if (file.size > 100 * 1024 * 1024) error = "Please choose a document smaller than 100 MB.";
    if (error) {
      state.selectedFile = null;
      document.querySelector("#file-input").value = "";
      document.querySelector("#file-heading").textContent = "Drag your document here";
      document.querySelector("#file-note").textContent = "PDF, Word or CSV · Up to 100 MB";
      showUploadError(error);
      return;
    }
    state.selectedFile = file;
    document.querySelector("#file-heading").textContent = file.name;
    document.querySelector("#file-note").textContent = `${formatSize(file.size)} · Selected for preview`;
    const nameInput = document.querySelector("#upload-course");
    if (!nameInput.value.trim()) { state.uploadName = file.name.replace(/\.[^.]+$/, "").replace(/[_-]/g, " ").slice(0, 100); nameInput.value = state.uploadName; }
    document.querySelector("#upload-error").hidden = true;
  }

  function materialTable() {
    const materials = state.materials.filter(item => `${item.name} ${item.course}`.toLowerCase().includes(state.search.toLowerCase()));
    if (!materials.length) return `<div class="card empty-state">${icon("document")}<h3>${state.search ? "No matching material" : "A fresh start for your next course"}</h3><p>${state.search ? "Try another filename or course name." : "Choose a document above to add your first piece of material."}</p></div>`;
    return `<div class="table-shell"><table><thead><tr><th scope="col">Document</th><th class="table-course" scope="col">Course</th><th class="table-language" scope="col">Language</th><th scope="col">Status</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead><tbody>${materials.map(item => `
      <tr><td><div class="material-name">${fileIcon(item)}<div><strong>${escape(item.name)}</strong><small>${escape(item.size)} <span aria-hidden="true">·</span> ${escape(item.detail)}</small></div></div></td><td class="muted table-course">${escape(item.course)}</td><td class="muted table-language">${escape(languageLabel(item.language))}</td><td>${badge(item.status === "ready" ? "Ready to use" : "Processing preview", item.status === "ready" ? "" : "badge-amber", item.status === "ready" ? "check" : "clock")}</td>
      <td><div class="row-actions"><button class="text-button" data-action="create-from-material" data-id="${escape(item.id)}" ${item.status !== "ready" ? "disabled" : ""} aria-label="Create course from ${escape(item.name)}">Create course ${icon("arrow")}</button><button class="icon-button" data-action="remove-material" data-id="${escape(item.id)}" aria-label="Remove ${escape(item.name)}">${icon("trash")}</button></div></td></tr>`).join("")}</tbody></table></div>`;
  }

  function uploadMaterial(form) {
    if (!state.selectedFile) { showUploadError("Choose a document first, or explore the sample material below."); return; }
    const courseName = form.elements.course.value.trim();
    if (!courseName) { showUploadError("Give your material a course name."); form.elements.course.focus(); return; }
    const file = state.selectedFile;
    const id = `material-${crypto.randomUUID()}`;
    state.materials.unshift({ id, name: file.name, course: courseName, language: form.elements.language.value, size: formatSize(file.size), format: file.name.split(".").pop().toUpperCase(), detail: "Preview file · contents not read", status: "processing", local: true });
    state.selectedFile = null;
    state.uploadName = "";
    state.search = "";
    renderMaterials();
    toast("Added to this preview. No document was uploaded or processed.");
    const timer = setTimeout(() => {
      uploadTimers.delete(timer);
      const material = getMaterial(id);
      if (!material) return;
      material.status = "ready";
      if (state.page === "materials") {
        document.querySelector("#material-results").innerHTML = materialTable();
      } else if (state.page === "generate" && !state.generation && !state.generatedId) renderGenerate();
      toast("Your material is ready for the next preview step. Course content will use sample text.");
    }, 1800);
    uploadTimers.add(timer);
  }

  function removeMaterialDialog(id) {
    const material = getMaterial(id);
    if (!material) return;
    const dependent = state.courses.filter(course => course.materialId === id).length;
    openModal(`${modalHeader("Remove this material?", "YOUR MATERIAL")}<p class="modal-copy"><strong>${escape(material.name)}</strong><br>${dependent ? `This will also remove ${dependent === 1 ? "the sample course" : `${dependent} sample courses`} created from it.` : "This removes the document from this preview workspace."} You can restore the original examples with Reset demo.</p><p class="modal-disclaimer">Only sample data in this browser tab will change.</p><div class="modal-actions"><button class="button button-secondary" data-action="close-modal">Keep material</button><button class="button button-danger" data-action="confirm-remove" data-id="${escape(id)}">Remove material</button></div>`);
  }

  function confirmRemove(id) {
    if (state.generation?.materialId === id) { clearInterval(generationTimer); state.generation = null; }
    if (state.player && getCourse(state.player.courseId)?.materialId === id) { pausePlayback(); state.player = null; clearQuestion(); }
    state.materials = state.materials.filter(item => item.id !== id);
    state.courses = state.courses.filter(item => item.materialId !== id);
    if (!getCourse(state.generatedId)) state.generatedId = null;
    if (!getMaterial()) selectMaterial(state.materials.find(item => item.status === "ready")?.id || null);
    modal.close();
    render();
    toast("Removed from the preview. Reset demo restores the original sample data.");
  }

  function renderGenerate() {
    const ready = state.materials.filter(item => item.status === "ready");
    if (!getMaterial() || getMaterial()?.status !== "ready") { if (ready.length) selectMaterial(ready[0].id); }
    const material = getMaterial();
    const generated = getCourse(state.generatedId);
    main.innerHTML = `${intro("02", "CREATE SOMETHING WORTH LEARNING", "Your knowledge.<br><em>A whole new learning experience.</em>", "Choose your material and a few preferences. We’ll take it from there.")}
      ${generated ? `<div class="success-banner" role="status">${icon("checkCircle")}<div><strong>Your sample course is ready to explore.</strong><p>This is a preview of the generated result. No AI generation has taken place.</p></div></div>` : ""}
      <div class="two-column"><section class="card card-padding" aria-label="Course settings">${state.generation ? generationView() : generated ? generatedView(generated) : ready.length ? generationForm(ready) : `<div class="empty-state">${icon("document")}<h3>Let’s start with your material</h3><p>Add a document in the first step.<br>It will appear here when it is ready.</p><a class="button" href="#materials">${icon("upload")}Upload material</a></div>`}</section>
      <aside class="card course-summary" id="course-summary" aria-label="Course preview">${courseSummary(generated, material)}</aside></div>`;
  }

  function generationForm(ready) {
    return `<form id="generation-form"><div class="form-section"><div class="step-label"><span>1</span>Choose your source</div><div class="source-options">${ready.map(item => `<label class="source-option">${fileIcon(item)}<span><strong>${escape(item.name)}</strong><small>${escape(item.detail)} · ${escape(languageLabel(item.language))}</small></span><input type="radio" name="source" value="${escape(item.id)}" ${item.id === state.selectedMaterial ? "checked" : ""} required></label>`).join("")}</div></div>
      <div class="form-section"><div class="step-label"><span>2</span>Make it yours</div><div class="field"><label for="course-title">Course title</label><input id="course-title" name="title" maxlength="120" value="${escape(state.draft.title)}" required></div>
        <div class="form-grid"><div class="field"><label for="course-language">Output language</label><select id="course-language" name="language">${languageOptions(state.draft.language)}</select></div><div class="field"><label for="course-voice">Narration voice</label><select id="course-voice" name="voice">${voiceOptions(state.draft.language)}</select></div></div></div>
      <div class="form-section"><div class="step-label"><span>3</span>One course. Different ways to understand.</div>${levelChips()}<p class="small-copy" style="margin:12px 0 0">The same ideas, explained at three levels. Learners choose the level that suits them before they start.</p></div>
      <details class="advanced"><summary>Advanced options</summary><div class="form-grid"><div class="field"><label for="target-words">Target words per part</label><input id="target-words" name="words" type="number" min="30" max="500" value="${state.draft.words}"></div><div class="field"><label for="source-window">Source window size</label><input id="source-window" name="window" type="number" min="1" max="50" value="${state.draft.window}"><small>Pages or source blocks per window</small></div></div><p class="small-copy" style="margin:12px 0 0">Preferences are illustrative in this preview.</p></details>
      <button class="button button-wide generate-submit" type="submit">${icon("sparkles")}Generate course ${icon("arrow")}</button><p class="preview-note">${icon("info")}This preview uses a sample course outline. Documents aren’t read; language and voice settings won’t change the sample English content.</p><p class="inline-error" id="generation-error" role="alert" hidden></p></form>`;
  }

  function voiceOptions(language) {
    const voices = language === "it" ? [["Bianca", "Bianca · Italian"]] : [["Joanna", "Joanna · US English"], ["Amy", "Amy · British English"]];
    return voices.map(([value, label]) => `<option value="${value}" ${value === state.draft.voice ? "selected" : ""}>${label}</option>`).join("");
  }

  function courseSummary(course, material) {
    const contentKey = course?.contentKey || (material?.id === "material-responsible" ? "responsible" : "ai");
    const parts = fixtures.lessons[contentKey];
    const theme = contentKey === "responsible" ? "sand" : "teal";
    return `<div class="card-title-row"><h2>A little look ahead</h2>${icon("eye")}</div>${art(course?.theme || theme)}<div class="summary-body"><span class="sample-label">${course ? "SAMPLE COURSE" : "ILLUSTRATIVE COURSE PREVIEW"}</span><h3 id="summary-title">${escape(course?.title || state.draft.title || "Your next learning experience")}</h3><p class="small-copy">${escape(course?.description || "A guided path through your material, with text, voice, and room for questions.")}</p>
      <div class="summary-meta"><span>${icon("book")}${parts.length} sample parts</span><span>${icon("layers")}3 levels</span></div><hr class="summary-divider"><span class="field-label">Your sample learning path</span><ol class="outline-list">${parts.slice(0, 4).map((part, i) => `<li><span>${String(i + 1).padStart(2, "0")}</span><span>${escape(part.title)}</span></li>`).join("")}${parts.length > 4 ? `<li><span>···</span><span>And ${parts.length - 4} more parts to explore</span></li>` : ""}</ol><p class="preview-note" style="margin-top:22px">${icon("info")}Actual course structure will come from your material once the backend is connected.</p></div>`;
  }

  function generationView() {
    const generation = state.generation;
    const stages = ["Understanding your material", "Shaping a clear learning path", "Preparing text and narration"];
    const step = Math.min(2, Math.floor(generation.progress / 34));
    return `<div class="progress-card" role="status"><div class="progress-symbol">${icon("sparkles")}</div><span class="sample-label">SIMULATED GENERATION</span><h2 style="margin-top:12px">Good learning takes a little care.</h2><p class="small-copy">A preview of how we’ll keep you in the loop.</p><div class="progress-track" role="progressbar" aria-label="Sample generation progress" aria-valuenow="${generation.progress}" aria-valuemin="0" aria-valuemax="100"><div class="progress-fill" style="width:${generation.progress}%"></div></div><div class="progress-description"><span>Sample progress</span><span>${generation.progress}%</span></div>${stages.map((stage, i) => `<div class="progress-stage ${i < step ? "done" : i > step ? "pending" : ""}">${icon(i < step ? "checkCircle" : i === step ? "loader" : "clock", i === step ? "spinner" : "")}<span>${stage}</span></div>`).join("")}<p class="preview-note" style="text-align:left;margin-top:28px">${icon("info")}This animation demonstrates the experience. No file processing, AI generation, or audio creation is running.</p><button class="text-button" data-action="cancel-generation" style="margin-top:15px">Cancel preview</button></div>`;
  }

  function startGeneration(form) {
    const title = form.elements.title.value.trim();
    if (!title) { const error = document.querySelector("#generation-error"); error.textContent = "Give your course a title before continuing."; error.hidden = false; form.elements.title.focus(); return; }
    const material = getMaterial(form.elements.source.value);
    if (!material || material.status !== "ready") return;
    state.draft = { title, language: form.elements.language.value, voice: form.elements.voice.value, words: Number(form.elements.words.value), window: Number(form.elements.window.value) };
    state.generation = { materialId: material.id, draft: { ...state.draft }, progress: 0 };
    state.generatedId = null;
    renderGenerate();
    clearInterval(generationTimer);
    generationTimer = setInterval(() => {
      if (!state.generation) { clearInterval(generationTimer); return; }
      state.generation.progress = Math.min(100, state.generation.progress + 10);
      if (state.generation.progress >= 100) {
        const generation = state.generation;
        const contentKey = material.id === "material-responsible" ? "responsible" : "ai";
        const course = { id: `course-${crypto.randomUUID()}`, materialId: material.id, title: generation.draft.title, language: generation.draft.language, voice: generation.draft.voice, theme: contentKey === "responsible" ? "sand" : "teal", contentKey, minutes: contentKey === "responsible" ? 8 : 12, description: "A sample learning experience. Explore the structure, explanations, and learning controls.", created: "Just added · preview", completed: false, local: true };
        state.courses.unshift(course);
        state.generatedId = course.id;
        state.generation = null;
        clearInterval(generationTimer);
        if (state.page === "generate") renderGenerate();
        else if (state.page === "learn" && !state.player) renderLearn();
        toast("Your sample course is ready. Preview it or start learning.");
      } else if (state.page === "generate") renderGenerate();
    }, 450);
  }

  function generatedView(course) {
    return `<div class="card-title-row"><h2>Ready for a first look</h2>${badge("Sample result", "badge-outline")}</div><h3 style="font-size:24px;font-weight:500;letter-spacing:-.6px;line-height:1.4;overflow-wrap:anywhere">${escape(course.title)}</h3><p class="small-copy">Your material, brought together as a guided experience. Choose a level to see how learning will feel.</p><div class="summary-meta"><span>${icon("book")}${getParts(course).length} parts</span><span>${icon("globe")}${escape(languageLabel(course.language))} selected</span><span>${icon("headphones")}Text & voice</span></div>${levelChips()}<hr class="summary-divider">${badge("AI-generated draft · Human review required", "badge-amber")}<p class="small-copy" style="margin:16px 0 22px">This result contains curated sample text, not content extracted from your document. Playback and questions demonstrate the interface.</p><button class="button button-wide" data-action="start-course" data-id="${escape(course.id)}">${icon("play")}Start learning</button><div class="form-grid" style="grid-template-columns:1fr 1fr;gap:10px;margin:12px 0 0"><button class="button button-secondary" data-action="preview-course" data-id="${escape(course.id)}">${icon("eye")}Preview course</button><button class="button button-secondary" data-action="revise-course" data-id="${escape(course.id)}">${icon("edit")}Request changes</button></div><button class="text-button" data-action="new-generation" style="margin-top:25px">${icon("plus")}Create another course</button>`;
  }

  function renderLearn() {
    if (state.player) { if (state.player.complete) renderCompletion(); else renderPlayer(); return; }
    main.innerHTML = `${intro("03", "A LITTLE CURIOSITY GOES A LONG WAY", "Make room for<br><em>your next ‘I get it.’</em>", "Pick a course. Find your level. Learn at your own pace.")}
      <section aria-labelledby="courses-title"><div class="section-heading"><div><h2 id="courses-title">Your learning collection <span class="count-pill">${state.courses.length}</span></h2><p>Clear ideas, thoughtful explanations, and an AI teacher to ask along the way.</p></div><label class="search-field">${icon("search")}<span class="sr-only">Search courses</span><input id="course-search" type="search" placeholder="Find a course…" value="${escape(state.courseSearch)}"></label></div><div id="course-results">${courseCards()}</div></section>
      <aside class="learning-note">${icon("lightbulb")}<div><strong>Same knowledge. Your way of understanding.</strong><p>Every course offers different difficulty levels. Choose one before starting; you can read, listen, and pause to ask a question.</p></div></aside>`;
  }

  function courseCards() {
    const courses = state.courses.filter(course => course.title.toLowerCase().includes(state.courseSearch.toLowerCase()));
    if (!courses.length && state.courseSearch) return `<div class="card empty-state">${icon("search")}<h3>No courses found</h3><p>Try a different title.</p></div>`;
    return `<div class="course-grid">${courses.map((course, index) => `<article class="card course-card">${art(course.theme, String(index + 1).padStart(2, "0"))}<div class="course-card-body"><div style="display:flex;align-items:center;justify-content:space-between;gap:8px">${badge(course.completed ? "Preview completed" : "Ready to explore", course.completed ? "" : "badge-outline", course.completed ? "check" : "")}${badge("Sample", "badge-neutral")}</div><h2>${escape(course.title)}</h2><p class="small-copy">${escape(course.description)}</p><div class="summary-meta"><span>${icon("book")}${getParts(course).length} parts</span><span>${icon("clock")}~${course.minutes} min</span><span>${icon("globe")}${escape(languageLabel(course.language))}${course.local ? " selected" : ""}</span></div><div class="course-card-bottom">${levelChips()}<div class="course-card-cta"><button class="button" data-action="start-course" data-id="${escape(course.id)}">${course.completed ? "Explore again" : "Start learning"}${icon("arrow")}</button><button class="icon-button" data-action="preview-course" data-id="${escape(course.id)}" aria-label="Preview ${escape(course.title)}">${icon("eye")}</button></div></div></div></article>`).join("")}
      <article class="create-course-card"><span class="upload-symbol">${icon("plus")}</span><h2>What will you learn next?</h2><p>There’s a learning experience waiting inside your material.</p><a class="button button-secondary" href="#generate">Create a course ${icon("arrow")}</a></article></div>`;
  }

  function startCourseDialog(id) {
    const course = getCourse(id);
    if (!course) return;
    openModal(`${modalHeader("Find your starting point", "LEARN YOUR WAY")}<p class="modal-copy">${escape(course.title)}<br>Choose the explanation style that feels right for you.</p><form id="start-form" data-id="${escape(id)}"><div class="level-options">${fixtures.levels.map(level => `<label class="level-option"><input type="radio" name="level" value="${level.id}" ${level.id === "intermediate" ? "checked" : ""}><span><strong>${level.label}</strong><small>${level.description}</small></span></label>`).join("")}</div><button class="button button-wide" type="submit">${icon("play")}Start learning</button><p class="modal-disclaimer">AI-generated draft · Human review required.<br>Sample English text, simulated playback, and example answers. Your progress lasts until this page is reloaded.</p></form>`);
  }

  function startCourse(id, level) {
    pausePlayback();
    clearQuestion();
    state.player = { courseId: id, level, index: 0, offset: 0, playing: false, speed: 1, complete: false, visited: new Set([0]) };
    modal.close();
    navigate("learn");
    main.focus({ preventScroll: true });
    window.scrollTo({ top: 0, behavior: "instant" });
  }

  function paragraphsFor(part, level) {
    if (level === "beginner") return [part.simple, part.paragraphs[0], part.example];
    if (level === "advanced") return [part.advanced, ...part.paragraphs.slice(1)];
    return part.paragraphs;
  }

  function renderPlayer() {
    const player = state.player;
    const course = getCourse(player.courseId);
    if (!course) { state.player = null; renderLearn(); return; }
    const parts = getParts(course);
    const part = parts[player.index];
    main.innerHTML = `<button class="back-button" data-action="leave-player">${icon("back")}Back to courses</button>
      <div class="player-top"><div><p class="eyebrow">YOUR LEARNING MOMENT</p><h1>${escape(course.title)}</h1><div class="player-meta"><span>${escape(levelLabel(player.level))}</span><span>·</span><span>${escape(languageLabel(course.language))}${course.local ? " selected" : ""}</span><span>·</span>${badge("AI-generated draft · Human review required", "badge-amber")}</div></div><button class="button button-secondary button-sm" data-action="leave-player">End preview</button></div>
      <div class="player-grid"><aside class="card parts-card" aria-label="Course contents"><h2 class="parts-title">Your learning path <span>Part ${player.index + 1} of ${parts.length}</span></h2><div class="progress-track"><div class="progress-fill" style="width:${(player.index + 1) / parts.length * 100}%"></div></div><ol class="part-list">${parts.map((item, i) => `<li><button class="part-button ${i === player.index ? "active" : ""}" data-action="go-part" data-index="${i}" ${i === player.index ? 'aria-current="step"' : ""}><span class="part-number">${player.visited.has(i) && i !== player.index ? icon("check") : String(i + 1).padStart(2, "0")}</span><span>${escape(item.title)}<small>${time(item.duration)} · Sample part</small></span></button></li>`).join("")}</ol></aside>
      <div><article class="card lesson-card"><div class="lesson-heading"><p class="eyebrow">PART ${String(player.index + 1).padStart(2, "0")} <span>/</span> ${escape(part.kicker)}</p>${badge("Sample lesson", "badge-neutral")}</div><h2>${escape(part.title)}</h2>${paragraphsFor(part, player.level).map(text => `<p class="lesson-paragraph">${escape(text)}</p>`).join("")}<aside class="takeaway"><strong>${icon("lightbulb")}The idea to take with you</strong><p>${escape(part.takeaway)}</p></aside>
        <div class="audio-player" aria-label="Playback controls preview"><div class="audio-top"><button class="play-button" data-action="toggle-play" aria-label="${player.playing ? "Pause preview" : "Play preview"}">${icon(player.playing ? "pause" : "play")}</button><div class="audio-timeline"><label class="sr-only" for="playback-position">Preview playback position</label><input id="playback-position" type="range" min="0" max="${part.duration}" value="${player.offset}" step="1"><div class="audio-time"><span id="current-time">${time(player.offset)}</span><span>${time(part.duration)}</span></div></div><button class="speed-button" data-action="playback-speed" aria-label="Playback speed ${player.speed} times">${player.speed}×</button><button class="icon-button" data-action="replay" aria-label="Replay preview">${icon("replay")}</button></div><p class="audio-caption">${icon("info")}Playback preview · The timer moves; no audio is played.</p></div>
        <div class="lesson-navigation"><button class="button button-secondary" data-action="previous-part" ${player.index === 0 ? "disabled" : ""}>${icon("back")}Previous part</button><button class="button" data-action="${player.index === parts.length - 1 ? "finish-course" : "next-part"}">${player.index === parts.length - 1 ? "Finish preview" : "Next part"}${icon(player.index === parts.length - 1 ? "check" : "arrow")}</button></div></article>
        <section class="card question-card" aria-labelledby="teacher-title"><div class="question-card-head"><div class="teacher-intro"><span class="teacher-avatar">${icon("sparkles")}</span><div><h3 id="teacher-title">A little more clarity?</h3><p>Pause here and ask your AI teacher.</p></div></div><button class="button button-soft button-sm" data-action="toggle-question" aria-expanded="${state.questionOpen}">${icon(state.questionOpen ? "close" : "chat")}${state.questionOpen ? "Close questions" : "Ask a question"}</button></div>
        <div id="question-content" ${state.questionOpen ? "" : "hidden"} class="question-content">${questionContent()}</div></section>
        <p class="tip-line">${icon("info")}Sample English content · Example answers · Progress is kept only in this tab until reload.</p>
      </div></div>`;
  }

  function questionContent() {
    return `<form class="question-form" id="question-form"><label for="question-input">What would you like to understand?</label><textarea id="question-input" maxlength="2000" placeholder="Ask a question about this part…" required ${state.questionPending ? "disabled" : ""}>${escape(state.question)}</textarea><div class="question-suggestions"><button type="button" data-action="suggest-question" data-question="Explain this more simply">Explain this more simply</button><button type="button" data-action="suggest-question" data-question="Give me an example">Give me an example</button><button type="button" data-action="suggest-question" data-question="What is the key takeaway?">What is the key takeaway?</button></div><div class="form-bottom"><span>${icon("info")}Preview only · No AI is connected</span><button class="button button-sm" type="submit" ${state.questionPending ? "disabled" : ""}>${icon(state.questionPending ? "loader" : "send", state.questionPending ? "spinner" : "")}${state.questionPending ? "Showing sample reply…" : "Send question"}</button></div><p class="inline-error" id="question-error" role="alert" hidden></p></form>
      ${state.answer ? `<div class="answer-bubble" role="status"><span>Example answer · Not generated by AI</span><p>${escape(state.answer)}</p><button class="button button-secondary button-sm" data-action="resume-learning">${icon("play")}Continue learning</button></div>` : ""}`;
  }

  function updateQuestionPanel() {
    const panel = document.querySelector("#question-content");
    if (panel) { panel.hidden = !state.questionOpen; panel.innerHTML = questionContent(); }
    const button = document.querySelector('[data-action="toggle-question"]');
    if (button) { button.setAttribute("aria-expanded", String(state.questionOpen)); button.innerHTML = `${icon(state.questionOpen ? "close" : "chat")}${state.questionOpen ? "Close questions" : "Ask a question"}`; }
  }

  function clearQuestion() { clearTimeout(questionTimer); state.question = ""; state.answer = ""; state.questionOpen = false; state.questionPending = false; }

  function pausePlayback() {
    clearInterval(playbackTimer);
    if (state.player) state.player.playing = false;
    updatePlayback();
  }

  function updatePlayback() {
    const player = state.player;
    if (!player || state.page !== "learn") return;
    const playButton = document.querySelector('[data-action="toggle-play"]');
    if (playButton) { playButton.setAttribute("aria-label", player.playing ? "Pause preview" : "Play preview"); playButton.innerHTML = icon(player.playing ? "pause" : "play"); }
    const timeline = document.querySelector("#playback-position");
    if (timeline) { timeline.value = player.offset; document.querySelector("#current-time").textContent = time(player.offset); }
  }

  function playPreview() {
    const player = state.player;
    if (!player) return;
    const part = getParts(getCourse(player.courseId))[player.index];
    if (player.offset >= part.duration) player.offset = 0;
    if (state.questionOpen) { clearQuestion(); updateQuestionPanel(); }
    player.playing = true;
    updatePlayback();
    clearInterval(playbackTimer);
    playbackTimer = setInterval(() => {
      player.offset = Math.min(part.duration, player.offset + player.speed);
      if (player.offset >= part.duration) pausePlayback(); else updatePlayback();
    }, 1000);
  }

  function goPart(index) {
    const player = state.player;
    const course = getCourse(player?.courseId);
    if (!course || index < 0 || index >= getParts(course).length) return;
    pausePlayback();
    clearQuestion();
    player.index = index;
    player.offset = 0;
    player.visited.add(index);
    renderPlayer();
    document.querySelector(".lesson-card h2").setAttribute("tabindex", "-1");
    document.querySelector(".lesson-card h2").focus({ preventScroll: true });
  }

  function sendQuestion(form) {
    if (state.questionPending || !state.player) return;
    const question = form.querySelector("textarea").value.trim();
    if (!question) { const error = document.querySelector("#question-error"); error.textContent = "Write a question or choose one of the suggestions."; error.hidden = false; return; }
    state.question = question;
    state.questionPending = true;
    state.answer = "";
    pausePlayback();
    updateQuestionPanel();
    const player = state.player;
    const part = getParts(getCourse(player.courseId))[player.index];
    questionTimer = setTimeout(() => {
      if (state.player !== player) return;
      const key = question.toLowerCase();
      state.answer = key === "explain this more simply" ? part.simple : key === "give me an example" ? part.example : key === "what is the key takeaway?" ? part.takeaway : "This is where a reply to your question will appear. In the connected version, the AI teacher will use the course material and the part you are learning. For this preview, try one of the suggested questions to see an example explanation.";
      state.questionPending = false;
      if (state.page === "learn" && state.questionOpen) updateQuestionPanel();
    }, 650);
  }

  function renderCompletion() {
    const course = getCourse(state.player.courseId);
    main.innerHTML = `<section class="card complete-card"><div class="complete-symbol">${icon("check")}</div><p class="eyebrow" style="justify-content:center">A LITTLE FURTHER THAN BEFORE</p><h1>That’s a wrap.<br><em>Keep the curiosity.</em></h1><p>You’ve reached the end of the sample course<br><strong>${escape(course.title)}</strong>.</p><div class="complete-stats"><div><strong>${getParts(course).length}</strong><span>Course parts</span></div><div><strong>${escape(levelLabel(state.player.level))}</strong><span>Your chosen level</span></div></div><button class="button" data-action="leave-player">Explore more courses ${icon("arrow")}</button><p class="preview-note" style="justify-content:center;margin-top:22px">${icon("info")}Preview complete · No learning record or certificate is saved.</p></section>`;
  }

  function previewCourse(id) {
    const course = getCourse(id);
    if (!course) return;
    openModal(`${modalHeader(course.title, "A FIRST LOOK")}<p class="modal-copy">Sample course outline · ${getParts(course).length} parts · 3 learning levels</p>${getParts(course).map((part, index) => `<section class="preview-part"><h3>${String(index + 1).padStart(2, "0")} · ${escape(part.title)}</h3><p>${escape(part.takeaway)}</p></section>`).join("")}<p class="modal-disclaimer">AI-generated draft · Human review required.<br>This prototype uses curated sample English text and simulated playback.</p><div class="modal-actions"><button class="button button-secondary" data-action="close-modal">Close preview</button><button class="button" data-action="start-course" data-id="${escape(id)}">Start learning ${icon("arrow")}</button></div>`);
  }

  function reviseCourse(id) {
    const course = getCourse(id);
    if (!course) return;
    openModal(`${modalHeader("Make room for a better explanation", "REQUEST CHANGES")}<p class="modal-copy">${escape(course.title)}<br>Choose a part and describe what you would improve.</p><form id="revision-form"><div class="field"><label for="revision-part">Course part</label><select id="revision-part">${getParts(course).map((part, index) => `<option value="${index}">${index + 1}. ${escape(part.title)}</option>`).join("")}</select></div><div class="field" style="margin-top:18px"><label for="revision-text">What would you change?</label><textarea id="revision-text" maxlength="2000" placeholder="e.g. Add a practical example and explain the key term more simply." required></textarea></div><label class="checkbox-label"><input type="checkbox">Apply this change to all difficulty levels</label><p class="modal-disclaimer">This form demonstrates how a revision request will look. It won’t modify the sample course or contact the backend.</p><div class="modal-actions"><button class="button button-secondary" type="button" data-action="close-modal">Cancel</button><button class="button" type="submit">Preview request ${icon("arrow")}</button></div></form>`);
  }

  function resetDemo() {
    openModal(`${modalHeader("A fresh look at UPRAISE", "RESET PREVIEW")}<p class="modal-copy">Restore the original sample documents and courses. Preview uploads, course settings, and learning progress will be cleared from this tab.</p><div class="modal-actions"><button class="button button-secondary" data-action="close-modal">Keep exploring</button><button class="button" data-action="confirm-reset">Reset demo ${icon("replay")}</button></div>`);
  }

  function confirmReset() {
    clearInterval(generationTimer);
    clearTimeout(toastTimer);
    for (const timer of uploadTimers) clearTimeout(timer);
    uploadTimers.clear();
    pausePlayback();
    clearQuestion();
    state.materials = structuredClone(fixtures.materials);
    state.courses = structuredClone(fixtures.courses);
    state.selectedFile = null;
    state.uploadName = "";
    state.uploadLanguage = "en";
    state.generation = null;
    state.generatedId = null;
    state.player = null;
    state.search = "";
    state.courseSearch = "";
    state.draft.words = 200;
    state.draft.window = 5;
    selectMaterial("material-ai");
    modal.close();
    navigate("materials");
    toast("Fresh start. The original sample workspace is back.");
  }

  document.addEventListener("submit", event => {
    event.preventDefault();
    const form = event.target;
    if (form.id === "upload-form") uploadMaterial(form);
    if (form.id === "generation-form") startGeneration(form);
    if (form.id === "start-form") startCourse(form.dataset.id, form.elements.level.value);
    if (form.id === "question-form") sendQuestion(form);
    if (form.id === "revision-form") {
      if (!document.querySelector("#revision-text").value.trim()) { document.querySelector("#revision-text").value = ""; document.querySelector("#revision-text").reportValidity(); return; }
      modal.close(); toast("Revision request previewed. No change was submitted or saved.");
    }
  });

  document.addEventListener("input", event => {
    const target = event.target;
    if (target.id === "material-search") { state.search = target.value; document.querySelector("#material-results").innerHTML = materialTable(); }
    if (target.id === "course-search") { state.courseSearch = target.value; document.querySelector("#course-results").innerHTML = courseCards(); }
    if (target.id === "upload-course") state.uploadName = target.value;
    if (target.id === "course-title") { state.draft.title = target.value; document.querySelector("#summary-title").textContent = target.value || "Your next learning experience"; }
    if (target.id === "target-words") state.draft.words = Number(target.value);
    if (target.id === "source-window") state.draft.window = Number(target.value);
    if (target.id === "question-input") state.question = target.value;
    if (target.id === "playback-position" && state.player) { state.player.offset = Number(target.value); updatePlayback(); }
  });

  document.addEventListener("change", event => {
    const target = event.target;
    if (target.name === "source") { selectMaterial(target.value); renderGenerate(); }
    if (target.id === "upload-language") state.uploadLanguage = target.value;
    if (target.id === "course-language") { state.draft.language = target.value; state.draft.voice = target.value === "it" ? "Bianca" : "Joanna"; document.querySelector("#course-voice").innerHTML = voiceOptions(target.value); }
    if (target.id === "course-voice") state.draft.voice = target.value;
  });

  document.addEventListener("click", event => {
    const control = event.target.closest("[data-action]");
    if (!control || control.disabled) return;
    const action = control.dataset.action;
    const id = control.dataset.id;
    if (action === "close-modal") modal.close();
    if (action === "preview-info") openModal(`${modalHeader("A first look at better learning")}<p class="modal-copy">This is the UPRAISE interface prototype. Explore uploading material, creating a course, and learning with sample content.</p><p class="preview-text">Navigation and demo controls work locally. Documents stay on your device, generation is simulated, playback has no audio, and teacher replies are examples.</p><p class="modal-disclaimer">No backend, AWS, AI provider, or database is connected. Nothing is saved after you reload this page.</p><div class="modal-actions"><button class="button" data-action="close-modal">Let’s explore ${icon("arrow")}</button></div>`);
    if (action === "reset-demo") resetDemo();
    if (action === "confirm-reset") confirmReset();
    if (action === "create-from-material") { selectMaterial(id); navigate("generate"); window.scrollTo({ top: 0, behavior: "instant" }); }
    if (action === "remove-material") removeMaterialDialog(id);
    if (action === "confirm-remove") confirmRemove(id);
    if (action === "cancel-generation") { clearInterval(generationTimer); state.generation = null; renderGenerate(); toast("Generation preview cancelled. Your settings are still here."); }
    if (action === "new-generation") { state.generatedId = null; renderGenerate(); }
    if (action === "start-course") {
      if (state.player && !state.player.complete) { pausePlayback(); render(); }
      startCourseDialog(id);
    }
    if (action === "preview-course") previewCourse(id);
    if (action === "revise-course") reviseCourse(id);
    if (action === "leave-player") { pausePlayback(); clearQuestion(); state.player = null; renderLearn(); main.focus({ preventScroll: true }); window.scrollTo({ top: 0, behavior: "instant" }); }
    if (action === "go-part") goPart(Number(control.dataset.index));
    if (action === "next-part") goPart(state.player.index + 1);
    if (action === "previous-part") goPart(state.player.index - 1);
    if (action === "toggle-play") { if (state.player.playing) pausePlayback(); else playPreview(); }
    if (action === "replay") { state.player.offset = 0; playPreview(); }
    if (action === "playback-speed") { const speeds = [1, 1.25, 1.5, 2]; state.player.speed = speeds[(speeds.indexOf(state.player.speed) + 1) % speeds.length]; control.textContent = `${state.player.speed}×`; control.setAttribute("aria-label", `Playback speed ${state.player.speed} times`); }
    if (action === "toggle-question") { pausePlayback(); state.questionOpen = !state.questionOpen; if (!state.questionOpen) { clearTimeout(questionTimer); state.questionPending = false; } updateQuestionPanel(); if (state.questionOpen) document.querySelector("#question-input").focus(); }
    if (action === "suggest-question" && !state.questionPending) { state.question = control.dataset.question; document.querySelector("#question-input").value = state.question; document.querySelector("#question-input").focus(); }
    if (action === "resume-learning") { clearQuestion(); updateQuestionPanel(); playPreview(); }
    if (action === "finish-course") { pausePlayback(); clearQuestion(); getCourse(state.player.courseId).completed = true; state.player.complete = true; renderCompletion(); window.scrollTo({ top: 0, behavior: "instant" }); }
  });

  window.addEventListener("hashchange", () => { render(); main.focus({ preventScroll: true }); window.scrollTo({ top: 0, behavior: "instant" }); });
  window.addEventListener("pagehide", () => { pausePlayback(); });
  document.addEventListener("visibilitychange", () => { if (document.hidden) pausePlayback(); });
  render();
})();
