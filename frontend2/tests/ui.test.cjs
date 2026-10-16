const test = require("node:test");
const assert = require("node:assert/strict");
const { harness, id, bundle } = require("./harness.cjs");

test("loads real API collections without seed content and groups aligned levels", async t => {
  const h = await harness(); t.after(h.close);
  assert.equal(h.document.querySelectorAll("tbody tr").length, 1);
  assert.match(h.find("#connection-label").textContent, /Backend connected/);
  assert.match(h.find("#connection-description").textContent, /Development draft/);
  assert.doesNotMatch(h.find("main").textContent, /Introduction to AI.pdf|Interactive preview|Sample content/);
  await h.navigate("learn");
  assert.equal(h.document.querySelectorAll(".course-card").length, 1);
  assert.equal(h.document.querySelectorAll(".course-card .level-chip").length, 3);
  assert.equal(h.backend.calls.filter(call => call.method !== "GET").length, 0);
});

test("uploads real multipart and SHA-256, preserving the retry key across navigation", async t => {
  const h = await harness(); t.after(h.close);
  h.chooseFile("<img src=x>.pdf", "a document"); h.input("#upload-course", "Client upload");
  h.backend.failNext("POST", "/v1/documents"); h.submit("#upload-form");
  await h.waitFor(() => h.find("#upload-error").textContent.includes("Retry with the same file"));
  await h.navigate("learn"); await h.navigate("materials"); h.submit("#upload-form");
  await h.waitFor(() => h.document.querySelectorAll("tbody tr").length === 2);
  const attempts = h.backend.records("POST", "/v1/documents");
  assert.equal(attempts.length, 2);
  assert.equal(attempts[0].headers.get("Idempotency-Key"), attempts[1].headers.get("Idempotency-Key"));
  assert.equal(attempts[1].rawBody.get("course_id"), "Client upload");
  assert.equal(attempts[1].rawBody.get("activity_id"), "client-activity");
  assert.match(attempts[1].rawBody.get("content_hash"), /^[a-f0-9]{64}$/);
  assert.equal(h.document.querySelectorAll("main img").length, 0);
  assert.match(h.find("tbody tr").textContent, /Processing/);
  h.backend.documents[0].status = "DONE"; h.backend.documents[0].progress = 1;
  await h.runTimers(5000);
  assert.match(h.find("tbody tr").textContent, /Ready to use/);
});

test("generation sends inherited source IDs and safely retries the same explicit request", async t => {
  const h = await harness(); t.after(h.close);
  h.click('[data-action="create-from-material"]'); await h.navigate("generate");
  h.input("#course-title", "Client course"); h.input("#course-language", "it", "change");
  h.click('input[name="levels"][value="advanced"]');
  assert.equal(h.find("#course-voice").value, "Bianca");
  h.backend.failNext("POST", "/v2/lesson-jobs"); h.submit("#generation-form");
  await h.waitFor(() => h.find("#generation-error").textContent.includes("Retry unchanged"));
  await h.navigate("materials"); await h.navigate("generate"); h.submit("#generation-form");
  await h.waitFor(() => Boolean(h.document.querySelector('[role="progressbar"]')));
  const calls = h.backend.records("POST", "/v2/lesson-jobs");
  assert.equal(calls[0].headers.get("Idempotency-Key"), calls[1].headers.get("Idempotency-Key"));
  assert.deepEqual(calls[1].body.document_ids, [id(1)]);
  assert.equal(calls[1].body.course_id, "client-course");
  assert.equal(calls[1].body.activity_id, "client-activity");
  assert.equal(calls[1].body.language, "it");
  assert.equal(calls[1].body.voice, "Bianca");
  assert.deepEqual(calls[0].body.levels, ["beginner", "intermediate"]);
  assert.deepEqual(calls[1].body.levels, ["beginner", "intermediate"]);
  await h.runTimers(5000);
  assert.match(h.find(".success-banner").textContent, /ready for review/);
  assert.match(h.find(".result-text").textContent, /source material/);
  assert.equal(h.find("#result-level").options.length, 2);
});

test("generation defaults to all levels, requires one, and retains an empty selection through navigation", async t => {
  const h = await harness(); t.after(h.close);
  await h.navigate("generate");
  assert.equal(h.document.querySelectorAll('input[name="levels"]:checked').length, 3);
  for (const level of ["beginner", "intermediate", "advanced"]) h.click(`input[name="levels"][value="${level}"]`);
  assert.equal(h.find("#generation-level-error").hidden, false);
  assert.equal(h.find(".generate-submit").disabled, true);
  await h.navigate("learn"); await h.navigate("generate");
  assert.equal(h.document.querySelectorAll('input[name="levels"]:checked').length, 0);
  h.submit("#generation-form"); await h.settle();
  assert.equal(h.backend.records("POST", "/v2/lesson-jobs").length, 0);
  h.click('input[name="levels"][value="advanced"]');
  assert.equal(h.find(".generate-submit").disabled, false);
  h.submit("#generation-form"); await h.settle(); await h.runTimers(5000);
  assert.deepEqual(h.backend.records("POST", "/v2/lesson-jobs")[0].body.levels, ["advanced"]);
  assert.equal(h.find("#result-level").options.length, 1);
  assert.equal(h.find("#result-level").value, "advanced");
});

test("changing selected generation levels after an uncertain request uses a new idempotency key", async t => {
  const h = await harness(); t.after(h.close);
  await h.navigate("generate");
  h.backend.failNext("POST", "/v2/lesson-jobs"); h.submit("#generation-form");
  await h.waitFor(() => h.find("#generation-error").textContent.includes("Retry unchanged"));
  h.click('input[name="levels"][value="advanced"]');
  h.submit("#generation-form"); await h.settle();
  const calls = h.backend.records("POST", "/v2/lesson-jobs");
  assert.deepEqual(calls[0].body.levels, ["beginner", "intermediate", "advanced"]);
  assert.deepEqual(calls[1].body.levels, ["beginner", "intermediate"]);
  assert.notEqual(calls[0].headers.get("Idempotency-Key"), calls[1].headers.get("Idempotency-Key"));
});

test("generation offers configured custom levels without adding default levels", async t => {
  const h = await harness(backend => {
    backend.intercept = call => call.path === "/config" ? backend.response({ ui_nonce: "test-ui-nonce", activity_id: "client-activity", levels: ["foundation", "expert"] }) : undefined;
  }); t.after(h.close);
  await h.navigate("generate");
  assert.equal(h.document.querySelectorAll('input[name="levels"]').length, 2);
  h.click('input[name="levels"][value="foundation"]');
  h.submit("#generation-form"); await h.settle();
  assert.deepEqual(h.backend.records("POST", "/v2/lesson-jobs")[0].body.levels, ["expert"]);
});

test("job status errors pause tracking and explicit cancellation uses A6 CANCEL only", async t => {
  const h = await harness(); t.after(h.close);
  await h.navigate("generate");
  h.backend.failNext("GET", `/v1/jobs/${id(20)}`);
  h.submit("#generation-form");
  await h.waitFor(() => Boolean(h.document.querySelector('[data-action="retry-tracking"]')));
  const before = h.backend.records("GET", `/v1/jobs/${id(20)}`).length;
  await h.runTimers(5000);
  assert.equal(h.backend.records("GET", `/v1/jobs/${id(20)}`).length, before);
  h.click('[data-action="retry-tracking"]'); await h.settle();
  h.click('[data-action="cancel-generation"]'); await h.settle();
  const cancelled = h.backend.records("DELETE", `/v1/jobs/${id(20)}`);
  assert.equal(cancelled.length, 1); assert.equal(cancelled[0].query.get("reason"), "CANCEL");
  assert.equal(h.backend.calls.some(call => call.query.get("reason") === "RECEIVED"), false);
});

test("starts B1 at the selected level, verifies audio, and uses the exact playback offset in B4 retries", async t => {
  const h = await harness(); t.after(h.close);
  await h.start(id(10), "beginner");
  const start = h.backend.records("POST", "/v1/sessions")[0];
  assert.equal(start.body.learner_level, "beginner");
  assert.deepEqual(start.body.consent_scope, ["questions"]);
  assert.match(start.body.session_id, /^[a-f0-9-]{36}$/);
  assert.match(h.find(".lesson-card").textContent, /beginner part/);
  assert.equal(h.urls.size, 1);
  h.click('[data-action="toggle-play"]'); await h.settle();
  h.audio[0].advance(12.345);
  h.click('[data-action="toggle-question"]');
  assert.equal(h.audio[0].paused, true);
  h.click('[data-question="Give me an example"]');
  const sessionId = start.body.session_id;
  h.backend.failNext("POST", `/v1/sessions/${sessionId}/interrupts`);
  h.submit("#question-form"); await h.settle();
  h.backend.nextReplyAction = { action: "SET_SPEED", rate: 1.5 };
  h.click('[data-action="retry-question"]'); await h.settle();
  const calls = h.backend.records("POST", `/v1/sessions/${sessionId}/interrupts`);
  assert.deepEqual(calls[0].body, calls[1].body);
  assert.equal(calls[0].body.playback_offset_ms, 12345);
  assert.match(h.find(".answer-bubble").textContent, /not been independently verified/);
  h.click('[data-action="resume-learning"]'); await h.settle();
  assert.equal(h.audio[0].currentTime, 12.345);
  assert.equal(h.audio[0].playbackRate, 1.5);
  h.click('[data-action="next-part"]'); await h.settle();
  assert.match(h.find(".lesson-card").textContent, /beginner part 2/);
  assert.equal(h.audio[0].currentTime, 0);
});

test("audio corruption never plays unverified bytes but keeps readable text", async t => {
  const h = await harness(backend => { backend.corruptAudio = true; }); t.after(h.close);
  await h.start();
  assert.match(h.find(".audio-caption").textContent, /Audio verification failed/);
  assert.equal(h.urls.size, 0);
  assert.equal(h.audio[0].src, "");
  assert.match(h.find(".lesson-card").textContent, /source material/);
});

test("a changed bundle hash clears playback and closes the bound session", async t => {
  const h = await harness(); t.after(h.close);
  await h.start(); h.backend.hashes.set(id(10), "b".repeat(64));
  h.click('[data-action="next-part"]'); await h.settle();
  assert.match(h.find("main").textContent, /course changed/);
  assert.equal(h.document.querySelectorAll(".lesson-card").length, 0);
  assert.equal(h.urls.size, 0);
  const end = h.backend.calls.find(call => call.path.endsWith("/end"));
  assert.equal(end.body.reason, "ERROR");
});

test("ending during an in-flight question prevents a late answer from appearing", async t => {
  const h = await harness(); t.after(h.close);
  await h.start(); h.backend.holdQuestion = true;
  h.click('[data-action="toggle-question"]'); h.input("#question-input", "A question"); h.submit("#question-form"); await h.settle();
  h.click('[data-action="leave-player"]'); await h.settle();
  assert.ok(h.find("#course-results"));
  h.backend.pendingQuestion(); await h.settle();
  assert.equal(h.document.querySelectorAll(".answer-bubble").length, 0);
  assert.equal(h.urls.size, 0);
});

test("completion retries B5 with the same session and reason after a network error", async t => {
  const h = await harness(); t.after(h.close);
  await h.start(); h.click('[data-action="next-part"]'); await h.settle();
  const sessionId = h.backend.records("POST", "/v1/sessions")[0].body.session_id;
  h.backend.failNext("POST", `/v1/sessions/${sessionId}/end`);
  h.click('[data-action="finish-course"]'); await h.settle();
  assert.match(h.find("main").textContent, /Retry session end/);
  assert.equal(h.urls.size, 0);
  h.click('[data-action="retry-session-end"]'); await h.settle();
  const calls = h.backend.records("POST", `/v1/sessions/${sessionId}/end`);
  assert.equal(calls.length, 2); assert.deepEqual(calls[0].body, { reason: "COMPLETED" }); assert.deepEqual(calls[0].body, calls[1].body);
  assert.match(h.find(".complete-card").textContent, /session is closed/);
});

test("production starts require a signed assertion and omit local session/consent claims", async t => {
  const h = await harness(backend => { backend.mode = "production"; }); t.after(h.close);
  await h.start(id(10), "advanced", "signed-assertion");
  const call = h.backend.records("POST", "/v1/sessions")[0];
  assert.equal(call.headers.get("X-UPRAISE-Session-JWT"), "signed-assertion");
  assert.equal(call.body.session_id, undefined);
  assert.equal(call.body.consent_scope, undefined);
  assert.equal(call.body.learner_level, "advanced");
  h.click('[data-action="leave-player"]'); await h.settle();
  const end = h.backend.calls.find(item => item.path.endsWith("/end"));
  assert.equal(end.headers.get("X-UPRAISE-Session-JWT"), "signed-assertion");
});

test("B1 retries keep a stable local session UUID", async t => {
  const h = await harness(); t.after(h.close);
  await h.navigate("learn"); h.click('[data-action="start-course"]'); await h.settle();
  h.backend.failNext("POST", "/v1/sessions"); h.submit("#start-form"); await h.settle();
  assert.match(h.find("#start-form").textContent, /Retry session start/);
  h.submit("#start-form"); await h.settle();
  const starts = h.backend.records("POST", "/v1/sessions");
  assert.equal(starts.length, 2); assert.equal(starts[0].body.session_id, starts[1].body.session_id);
});

test("real revision targets the existing chunk and preserves one course across levels", async t => {
  const h = await harness(); t.after(h.close);
  await h.navigate("generate"); h.submit("#generation-form"); await h.settle(); await h.runTimers(5000);
  h.click('[data-action="revise-course"]'); await h.settle();
  assert.equal(h.find("#revision-all").checked, false);
  h.input("#revision-text", "Please add a practical example."); h.find("#revision-all").checked = true;
  h.find("#revision-all").dispatchEvent(new h.window.Event("change", { bubbles: true }));
  h.submit("#revision-form"); await h.settle(); await h.runTimers(5000);
  const call = h.backend.records("POST", "/v1/audio:regenerate")[0];
  assert.equal(call.body.lesson_job_id, id(20)); assert.equal(call.body.apply_to_all_levels, true);
  assert.equal(call.body.teacher_review, "Please add a practical example.");
  assert.match(h.find(".result-text").textContent, /Updated explanation/);
  await h.navigate("learn");
  assert.equal(h.document.querySelectorAll(".course-card").length, 2);
});

test("an uncertain revision is never automatically resubmitted", async t => {
  const h = await harness(); t.after(h.close);
  await h.navigate("generate"); h.submit("#generation-form"); await h.settle(); await h.runTimers(5000);
  h.click('[data-action="revise-course"]'); await h.settle(); h.input("#revision-text", "Revise this explanation.");
  h.backend.failNext("POST", "/v1/audio:regenerate"); h.submit("#revision-form"); await h.settle();
  assert.match(h.find("#modal").textContent, /may already have been accepted/);
  assert.equal(h.document.querySelectorAll("#revision-form").length, 0);
  await h.runTimers(5000);
  assert.equal(h.backend.records("POST", "/v1/audio:regenerate").length, 1);
});

test("material removal warns about every dependent course and retries the pinned target", async t => {
  const h = await harness(); t.after(h.close);
  h.click('[data-action="remove-material"]');
  assert.match(h.find("#modal").textContent, /permanently removed, across all levels and languages/);
  h.backend.failNext("DELETE", `/v1/documents/${id(1)}`); h.click('[data-action="confirm-remove"]'); await h.settle();
  h.click('[data-action="confirm-remove"]'); await h.settle();
  assert.equal(h.backend.records("DELETE", `/v1/documents/${id(1)}`).length, 2);
  assert.equal(h.document.querySelectorAll("tbody tr").length, 0);
  await h.navigate("learn"); assert.equal(h.document.querySelectorAll(".course-card").length, 0);
});

test("custom backend levels are offered from the actual result rather than fixed UI choices", async t => {
  const h = await harness(backend => {
    const value = bundle(); const lesson = value.variants.intermediate;
    value.variants = { foundation: { ...lesson, learner_level: "foundation" }, expert: { ...lesson, learner_level: "expert" } };
    backend.bundles.set(id(10), value);
  }); t.after(h.close);
  await h.navigate("learn"); h.click('[data-action="start-course"]'); await h.settle();
  assert.equal(h.document.querySelectorAll(".level-option").length, 2);
  assert.ok(h.find('input[name="level"][value="foundation"]'));
  assert.ok(h.find('input[name="level"][value="expert"]'));
});

test("course preview transitions to the start dialog without a stale close event", async t => {
  const h = await harness(); t.after(h.close);
  await h.navigate("learn"); h.click('[data-action="preview-course"]'); await h.settle();
  h.click('#modal [data-action="start-course"]'); await h.settle();
  assert.ok(h.find("#start-form")); assert.equal(h.find("#modal").open, true);
});

test("a late course response cannot replace a newer connection dialog", async t => {
  const h = await harness(); t.after(h.close);
  let resolveResult;
  h.backend.intercept = call => call.path === `/v1/jobs/${id(10)}/result` ? new Promise(resolve => { resolveResult = resolve; }) : undefined;
  await h.navigate("learn"); h.click('[data-action="preview-course"]'); await h.settle();
  h.click('#modal [data-action="close-modal"]'); h.click('[data-action="connection-info"]');
  resolveResult(h.backend.response(bundle())); await h.settle();
  assert.equal(h.find("#modal-title").textContent, "Your UPRAISE connection");
});

test("a document restored after page exit cannot resume a closed B1 session", async t => {
  const h = await harness(); t.after(h.close);
  await h.start();
  h.window.dispatchEvent(new h.window.Event("pagehide")); await h.settle();
  const restored = new h.window.Event("pageshow"); Object.defineProperty(restored, "persisted", { value: true });
  h.window.dispatchEvent(restored); await h.settle();
  assert.equal(h.audio[0].src, ""); assert.equal(h.urls.size, 0);
  assert.match(h.find("main").textContent, /session was closed/);
  assert.equal(h.document.querySelectorAll(".lesson-card").length, 0);
  assert.equal(h.backend.calls.filter(call => call.path.endsWith("/end")).length, 1);
});
