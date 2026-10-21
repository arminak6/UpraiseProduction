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

test("existing learning cards show their recorded teaching version without fetching course text", async t => {
  const h = await harness(backend => {
    const versions = [null, { teaching_version: "v2", teaching_policy: "teaching-narration-v2.8" },
      { teaching_version: "v3", teaching_policy: "continuous-teaching-v3.0" },
      { teaching_version: "v2" }];
    backend.bundles.clear();
    versions.forEach((version, index) => {
      const value = bundle(id(10 + index), "The same course title");
      for (const lesson of Object.values(value.variants)) lesson.versions = version;
      backend.bundles.set(value.job_id, value);
    });
  }); t.after(h.close);
  await h.navigate("learn");
  assert.deepEqual([...h.document.querySelectorAll(".course-card .course-version")].map(node => node.textContent),
    ["Generation V1", "Generation V2.8", "Generation V3", "Generation V2"]);
  assert.equal(h.document.querySelectorAll(".course-card").length, 4);
  assert.equal(h.backend.calls.filter(call => call.path.endsWith("/result") || call.method !== "GET").length, 0);
});

test("learning cards never infer missing versions from the title or A3 API version", async t => {
  const h = await harness(backend => {
    backend.bundles.get(id(10)).variants.beginner.lesson_id = "V3 course";
    const catalog = backend.catalog.bind(backend);
    backend.catalog = () => catalog().map(({ teaching_version, ...item }) => ({ ...item,
      teaching_policy: "teaching-narration-v2.8" }));
  }); t.after(h.close);
  await h.navigate("learn");
  assert.equal(h.find(".course-version").textContent, "Version unknown");
});

test("different minor policies across learning levels show the shared major version", async t => {
  const h = await harness(backend => {
    for (const [level, lesson] of Object.entries(backend.bundles.get(id(10)).variants)) {
      lesson.versions = { teaching_version: "v2", teaching_policy: `teaching-narration-v2.${level === "beginner" ? "7" : "8"}` };
    }
  }); t.after(h.close);
  await h.navigate("learn");
  assert.equal(h.find(".course-version").textContent, "Generation V2");
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

test("teaching V2 is opt-in, survives navigation and can switch back to V1 with a new request identity", async t => {
  const h = await harness(); t.after(h.close);
  await h.navigate("generate"); assert.equal(h.find("#teaching-version").value, "v1");
  h.input("#teaching-version", "v2", "change");
  await h.navigate("materials"); await h.navigate("generate");
  assert.equal(h.find("#teaching-version").value, "v2");
  h.backend.failNext("POST", "/v2/lesson-jobs"); h.submit("#generation-form"); await h.settle();
  assert.equal(h.backend.records("POST", "/v2/lesson-jobs")[0].body.teaching_version, "v2");
  h.input("#teaching-version", "v1", "change"); h.submit("#generation-form"); await h.settle();
  const calls = h.backend.records("POST", "/v2/lesson-jobs");
  assert.equal(calls[1].body.teaching_version, undefined);
  assert.notEqual(calls[0].headers.get("Idempotency-Key"), calls[1].headers.get("Idempotency-Key"));
  assert.match(h.backend.bundles.get(id(10)).variants.intermediate.chunks[0].text, /source material/);
});

test("V3 continuous teaching is selectable, survives navigation and has a distinct request identity", async t => {
  const h = await harness(); t.after(h.close);
  await h.navigate("generate");
  assert.equal(h.find("#teaching-version").value, "v1");
  h.input("#teaching-version", "v3", "change");
  await h.navigate("materials"); await h.navigate("generate");
  assert.equal(h.find("#teaching-version").value, "v3");
  h.backend.failNext("POST", "/v2/lesson-jobs"); h.submit("#generation-form"); await h.settle();
  h.input("#teaching-version", "v2", "change"); h.submit("#generation-form"); await h.settle();
  const calls = h.backend.records("POST", "/v2/lesson-jobs");
  assert.equal(calls[0].body.teaching_version, "v3");
  assert.equal(calls[1].body.teaching_version, "v2");
  assert.notEqual(calls[0].headers.get("Idempotency-Key"), calls[1].headers.get("Idempotency-Key"));
});

test("V3 review identifies the version and displays its spoken script", async t => {
  const h = await harness(backend => {
    for (const lesson of Object.values(backend.bundles.get(id(10)).variants)) {
      lesson.versions = { teaching_version: "v3" };
      for (const chunk of lesson.chunks) chunk.narration_text = "We now continue the explanation.";
    }
  }); t.after(h.close);
  await h.navigate("review"); h.click('[data-action="review-course"]'); await h.settle();
  assert.match(h.find("main").textContent, /Teaching V3/);
  assert.match(h.find("main").textContent, /Spoken explanation.*Version 3/);
  assert.match(h.find("main").textContent, /We now continue the explanation/);
});

test("V2 review displays an escaped spoken script and verified audio controls that stop on part changes", async t => {
  const h = await harness(backend => {
    for (const lesson of Object.values(backend.bundles.get(id(10)).variants)) {
      lesson.versions = { teaching_version: "v2" };
      for (const chunk of lesson.chunks) chunk.narration_text = 'Multiply n choose two by p. <img src=x onerror="alert(1)">';
    }
  }); t.after(h.close);
  await h.navigate("review"); h.click('[data-action="review-course"]'); await h.settle();
  assert.match(h.find(".review-course-heading").textContent, /Teaching V2/);
  assert.match(h.find(".review-narration-text").textContent, /Multiply n choose two/);
  assert.equal(h.document.querySelectorAll(".review-narration img").length, 0);
  const audio = h.find("#review-audio"); let stopped = false, played = false;
  audio.play = async () => { played = true; }; audio.pause = () => { stopped = true; }; audio.load = () => {};
  h.click('[data-action="review-listen"]'); await h.settle();
  assert.equal(played, true); assert.equal(h.urls.size, 1);
  assert.ok(h.backend.calls.some(call => call.path.includes("/audio/")));
  h.click('[data-action="review-stop"]'); await h.settle();
  assert.equal(h.urls.size, 0); assert.equal(audio.getAttribute("src"), null);
  assert.equal(h.find('[data-action="review-stop"]').disabled, true);
  h.click('[data-action="review-listen"]'); await h.settle();
  h.click('[data-action="review-next"]'); await h.settle();
  assert.equal(stopped, true); assert.equal(audio.getAttribute("src"), null); assert.equal(h.urls.size, 0);
  assert.equal(h.backend.records("POST", "/v1/sessions").length, 0);
});

test("late V2 review audio cannot appear after switching course parts", async t => {
  let release;
  const h = await harness(backend => {
    for (const lesson of Object.values(backend.bundles.get(id(10)).variants)) {
      lesson.versions = { teaching_version: "v2" };
      for (const chunk of lesson.chunks) chunk.narration_text = "A natural spoken explanation.";
    }
    backend.intercept = call => call.path.includes("/audio/") ? new Promise(resolve => { release = () => resolve(backend.response(Buffer.from("unused"), 200, "audio/wav")); }) : undefined;
  }); t.after(h.close);
  await h.navigate("review"); h.click('[data-action="review-course"]'); await h.settle();
  h.click('[data-action="review-listen"]'); await h.settle();
  h.click('[data-action="review-next"]'); await h.settle();
  release(); await h.settle(); assert.equal(h.urls.size, 0); assert.equal(h.find("#review-audio").getAttribute("src"), null);
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
  assert.equal(h.document.querySelectorAll(".question-bubble").length, 1);
  assert.equal(h.document.querySelectorAll(".answer-bubble").length, 1);
  assert.match(h.find(".answer-bubble").textContent, /not been independently verified/);
  h.click('[data-action="resume-learning"]'); await h.settle();
  assert.equal(h.audio[0].currentTime, 12.345);
  assert.equal(h.audio[0].playbackRate, 1.5);
  h.click('[data-action="next-part"]'); await h.settle();
  assert.match(h.find(".lesson-card").textContent, /beginner part 2/);
  assert.equal(h.audio[0].currentTime, 0);
});

test("answer audio has visible pause, resume, stop and replay controls and stops before the lesson resumes", async t => {
  const h = await harness(); t.after(h.close);
  await h.start(); h.click('[data-action="toggle-play"]'); await h.settle(); h.audio[0].advance(12.345);
  h.click('[data-action="toggle-question"]'); h.input("#question-input", "Explain this part."); h.submit("#question-form"); await h.settle();
  const answer = h.audio[1];
  assert.equal(h.find('[data-action="stop-answer"]').disabled, true);
  h.click('[data-action="listen-answer"]'); await h.settle();
  assert.equal(answer.paused, false); assert.equal(h.audio[0].paused, true);
  assert.equal(h.find('[data-action="listen-answer"]').textContent, "Pause answer");
  assert.equal(h.find('[data-action="listen-answer"]').getAttribute("aria-pressed"), "true");
  answer.advance(4.5); h.click('[data-action="listen-answer"]'); await h.settle();
  assert.equal(answer.paused, true); assert.equal(answer.currentTime, 4.5);
  assert.equal(h.find('[data-action="listen-answer"]').textContent, "Resume answer");
  h.click('[data-action="listen-answer"]'); await h.settle(); assert.equal(answer.currentTime, 4.5);
  h.click('[data-action="stop-answer"]'); await h.settle();
  assert.equal(answer.paused, true); assert.equal(answer.currentTime, 0);
  assert.equal(h.find('[data-action="listen-answer"]').textContent, "Listen to answer");
  assert.equal(h.find('[data-action="stop-answer"]').disabled, true);
  answer.advance(6); h.click('[data-action="replay-answer"]'); await h.settle();
  assert.equal(answer.currentTime, 0); assert.equal(answer.paused, false);
  answer.advance(8); answer.finish();
  assert.equal(h.find('[data-action="listen-answer"]').textContent, "Listen to answer");
  assert.equal(h.find('[data-action="listen-answer"]').getAttribute("aria-pressed"), "false");
  h.click('[data-action="listen-answer"]'); await h.settle();
  assert.equal(answer.currentTime, 0); assert.equal(answer.paused, false);
  h.click('[data-action="resume-learning"]'); await h.settle();
  assert.equal(answer.paused, true); assert.equal(answer.src, "");
  assert.equal(h.audio[0].paused, false); assert.equal(h.audio[0].currentTime, 12.345);
  assert.equal(h.document.querySelectorAll(".answer-bubble").length, 0); assert.equal(h.urls.size, 1);
});

test("ask another question reuses the session and paused position with a fresh request and stops the previous answer", async t => {
  const h = await harness(); t.after(h.close);
  await h.start(); h.click('[data-action="toggle-play"]'); await h.settle(); h.audio[0].advance(19.876);
  h.click('[data-action="toggle-question"]'); h.input("#question-input", "Explain the first example."); h.submit("#question-form"); await h.settle();
  h.click('[data-action="listen-answer"]'); await h.settle(); h.audio[1].advance(3);
  h.click('[data-action="ask-another-question"]'); await h.settle();
  assert.equal(h.audio[1].paused, true); assert.equal(h.audio[1].src, ""); assert.equal(h.urls.size, 1);
  assert.equal(h.audio[0].paused, true); assert.equal(h.audio[0].currentTime, 19.876);
  assert.equal(h.document.activeElement.id, "question-input"); assert.equal(h.find("#question-input").value, "");
  assert.equal(h.document.querySelectorAll(".answer-bubble").length, 1);
  assert.match(h.find(".question-history").textContent, /Explain the first example/);
  h.input("#question-input", "Can you give another example?"); h.submit("#question-form"); await h.settle();
  const calls = h.backend.calls.filter(call => call.path.endsWith("/interrupts"));
  assert.equal(calls.length, 2); assert.notEqual(calls[0].body.request_id, calls[1].body.request_id);
  assert.equal(calls[0].path, calls[1].path); assert.equal(calls[0].body.chunk_id, calls[1].body.chunk_id);
  assert.equal(calls[0].body.playback_offset_ms, 19876); assert.equal(calls[1].body.playback_offset_ms, 19876);
  assert.equal(calls[1].body.text, "Can you give another example?");
  assert.equal(h.document.querySelectorAll(".question-bubble").length, 2);
  assert.equal(h.document.querySelectorAll(".answer-bubble").length, 2);
  assert.equal(h.document.querySelectorAll(".answer-audio-controls").length, 1);
  assert.equal(h.backend.records("POST", "/v1/sessions").length, 1);
  assert.equal(h.audio[0].paused, true);
  h.click('[data-action="resume-learning"]'); await h.settle();
  assert.equal(h.audio[0].currentTime, 19.876); assert.equal(h.audio[0].paused, false);
});

test("all exchanges stay visible in one interruption; Continue and End start an empty visible chat", async t => {
  const h = await harness(backend => {
    backend.intercept = call => call.path.endsWith("/interrupts") ? backend.response({ request_id: call.body.request_id,
      chunk_id: call.body.chunk_id, text: `Answer to ${call.body.text}`, grounded: null, sources: [], warnings: [] }) : undefined;
  }); t.after(h.close);
  await h.start(); h.click('[data-action="toggle-question"]');
  const questions = ['<img src=x onerror="alert(1)"> What does this mean?', "Can you explain more?", "Another example?"];
  for (const [index, question] of questions.entries()) {
    h.input("#question-input", question); h.submit("#question-form"); await h.settle();
    assert.equal(h.document.querySelectorAll(".question-bubble").length, index + 1);
    assert.equal(h.document.querySelectorAll(".answer-bubble").length, index + 1);
    assert.equal(h.find("#question-input").value, "");
  }
  const messages = [...h.find(".question-history").children].map(element => element.textContent);
  for (const [index, question] of questions.entries()) {
    assert.ok(messages[index * 2].includes(question)); assert.ok(messages[index * 2 + 1].includes(`Answer to ${question}`));
  }
  assert.equal(h.document.querySelectorAll(".question-history img").length, 0);
  h.click('[data-action="resume-learning"]'); await h.settle();
  assert.equal(h.document.querySelectorAll(".question-bubble, .answer-bubble").length, 0);
  assert.equal(h.backend.calls.filter(call => call.path.endsWith("/end")).length, 0);
  h.audio[0].advance(21); h.click('[data-action="toggle-question"]');
  assert.equal(h.find(".question-history").textContent, "");
  h.input("#question-input", "A fresh interruption."); h.submit("#question-form"); await h.settle();
  assert.equal(h.document.querySelectorAll(".answer-bubble").length, 1);
  const calls = h.backend.calls.filter(call => call.path.endsWith("/interrupts"));
  assert.equal(new Set(calls.map(call => call.path)).size, 1);
  assert.equal(calls.at(-1).body.playback_offset_ms, 21000);
  assert.ok(calls.every(call => !Object.hasOwn(call.body, "history")));
  h.click('[data-action="leave-player"]'); await h.settle();
  assert.equal(h.document.querySelectorAll(".question-bubble, .answer-bubble").length, 0);
  assert.equal(h.backend.calls.filter(call => call.path.endsWith("/end")).length, 1);
  await h.start(); h.click('[data-action="toggle-question"]');
  assert.equal(h.find(".question-history").textContent, "");
  assert.equal(h.backend.records("POST", "/v1/sessions").length, 2);
});

test("pending questions and retries preserve completed exchanges without duplicating messages", async t => {
  const h = await harness(); t.after(h.close);
  await h.start(); h.click('[data-action="toggle-question"]');
  h.input("#question-input", "First question"); h.submit("#question-form"); await h.settle();
  h.backend.holdQuestion = true;
  h.input("#question-input", "Second question"); h.submit("#question-form"); await h.settle();
  assert.equal(h.document.querySelectorAll(".question-bubble").length, 2);
  assert.equal(h.document.querySelectorAll(".answer-bubble").length, 1);
  assert.equal(h.find('[data-action="ask-another-question"]').disabled, true);
  h.backend.pendingQuestion(); await h.settle(); h.backend.holdQuestion = false;
  const sessionId = h.backend.records("POST", "/v1/sessions")[0].body.session_id;
  h.backend.failNext("POST", `/v1/sessions/${sessionId}/interrupts`);
  h.input("#question-input", "Third question"); h.submit("#question-form"); await h.settle();
  assert.equal(h.document.querySelectorAll(".question-bubble").length, 3);
  assert.equal(h.document.querySelectorAll(".answer-bubble").length, 2);
  h.click('[data-action="retry-question"]'); await h.settle();
  assert.equal(h.document.querySelectorAll(".question-bubble").length, 3);
  assert.equal(h.document.querySelectorAll(".answer-bubble").length, 3);
  const calls = h.backend.calls.filter(call => call.path.endsWith("/interrupts"));
  assert.deepEqual(calls.at(-2).body, calls.at(-1).body);
});

test("text-only answers still offer another question without audio controls", async t => {
  const h = await harness(backend => {
    backend.intercept = call => call.path.endsWith("/interrupts") ? backend.response({ request_id: call.body.request_id,
      chunk_id: call.body.chunk_id, text: "An answer without narration.", grounded: null, sources: [], warnings: [] }) : undefined;
  }); t.after(h.close);
  await h.start(); h.click('[data-action="toggle-question"]'); h.input("#question-input", "Explain this."); h.submit("#question-form"); await h.settle();
  assert.equal(h.document.querySelectorAll(".answer-audio-controls").length, 0);
  h.click('[data-action="ask-another-question"]'); await h.settle();
  assert.equal(h.document.activeElement.id, "question-input"); assert.equal(h.audio[0].paused, true);
  assert.equal(h.backend.calls.filter(call => call.path.endsWith("/interrupts")).length, 1);
});

test("failed answer playback preserves readable text and another-question controls", async t => {
  const h = await harness(); t.after(h.close);
  await h.start(); h.click('[data-action="toggle-question"]'); h.input("#question-input", "Explain this."); h.submit("#question-form"); await h.settle();
  h.audio[1].play = async () => { throw new Error("Playback rejected"); };
  h.click('[data-action="listen-answer"]'); await h.settle();
  assert.match(h.find("#toast").textContent, /audio could not be played/);
  assert.equal(h.find('[data-action="listen-answer"]').textContent, "Listen to answer");
  assert.match(h.find(".answer-bubble").textContent, /real teacher response/);
  h.click('[data-action="ask-another-question"]'); await h.settle(); assert.equal(h.document.activeElement.id, "question-input");
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

test("unresolved automated reviews visibly flag only the affected part and expose escaped findings", async t => {
  const h = await harness(backend => {
    backend.bundles.get(id(10)).variants.beginner.chunks[0].reviewer_report = {
      approved: false, summary: "DRAFT; human review required.",
      issues: [
        { category: "citations", message: "Human review required: source references (LESSON_CITATIONS_INVALID). <img src=x>" },
        { category: "source", message: "Human review required: difficulty review (TEACHING_V2_REVIEW_UNAVAILABLE; PROVIDER_ERROR)." },
        { category: "source", message: "Human review required: another unavailable review (TEACHING_V2_REVIEW_UNAVAILABLE)." },
      ],
    };
  }); t.after(h.close);
  await h.navigate("review"); h.click(`[data-action="review-course"][data-id="${id(10)}"]`); await h.settle();
  assert.match(h.find(".review-flag-banner").textContent, /Generation continued/);
  assert.match(h.find("#review-part").options[0].textContent, /Needs review/);
  assert.doesNotMatch(h.find("#review-part").options[1].textContent, /Needs review/);
  assert.equal(h.find(".advanced").open, true);
  assert.match(h.find(".review-findings").textContent, /<img src=x>/);
  assert.equal(h.document.querySelectorAll(".review-findings img").length, 0);
  assert.deepEqual([...h.document.querySelectorAll(".review-tag")].map(tag => tag.textContent), ["Citations", "Review unavailable"]);
  h.click('[data-action="review-next"]'); await h.settle();
  assert.equal(h.document.querySelectorAll(".review-flag-banner").length, 0);
  assert.equal(h.document.querySelectorAll(".review-tag").length, 0);
});

test("teacher review opens an existing course and preserves per-part feedback through navigation", async t => {
  const h = await harness(); t.after(h.close);
  assert.equal(h.document.querySelectorAll("[data-nav]").length, 4);
  await h.navigate("review");
  assert.equal(h.document.querySelectorAll(".review-course-row").length, 1);
  h.click(`[data-action="review-course"][data-id="${id(10)}"]`); await h.settle();
  assert.match(h.find(".review-full-text").textContent, /beginner part 1/);
  h.input("#review-feedback", "Clarify the first example.");
  h.click('[data-action="review-next"]'); await h.settle();
  assert.match(h.find(".review-full-text").textContent, /beginner part 2/);
  assert.equal(h.find("#review-feedback").value, "");
  h.input("#review-feedback", "Explain the second example.");
  h.input("#review-level", "advanced", "change"); await h.settle();
  assert.equal(h.find("#review-feedback").value, "");
  h.input("#review-level", "beginner", "change"); await h.settle();
  assert.equal(h.find("#review-feedback").value, "Clarify the first example.");
  h.click('[data-action="review-next"]'); await h.settle();
  assert.equal(h.find("#review-feedback").value, "Explain the second example.");
  await h.navigate("materials"); await h.navigate("review");
  assert.equal(h.find("#review-feedback").value, "Explain the second example.");
  assert.equal(h.backend.records("POST", "/v2/lesson-jobs").length, 0);
  assert.equal(h.backend.records("POST", "/v1/audio:regenerate").length, 0);
  assert.equal(h.backend.records("POST", "/v1/sessions").length, 0);
});

test("inline review revises the chosen second part across levels and reloads the same selection", async t => {
  const h = await harness(); t.after(h.close);
  await h.navigate("review"); h.click('[data-action="review-course"]'); await h.settle();
  h.input("#review-level", "advanced", "change"); h.input("#review-part", "1", "change");
  assert.equal(h.find("#review-all").checked, false);
  const chunk = h.backend.bundles.get(id(10)).variants.advanced.chunks[1];
  h.input("#review-feedback", "Add a source-based example to part two.");
  h.find("#review-all").checked = true; h.find("#review-all").dispatchEvent(new h.window.Event("change", { bubbles: true }));
  h.submit("#review-form"); h.submit("#review-form"); await h.settle();
  assert.equal(h.find('#review-form button[type="submit"]').matches(":disabled"), true);
  assert.match(h.find(".review-progress").textContent, /35.00%/);
  await h.runTimers(5000);
  const [call] = h.backend.records("POST", "/v1/audio:regenerate");
  assert.deepEqual(call.body, { lesson_job_id: id(10), chunk_id: chunk.chunk_id,
    teacher_review: "Add a source-based example to part two.", apply_to_all_levels: true });
  assert.equal(h.backend.records("POST", "/v1/audio:regenerate").length, 1);
  assert.equal(h.window.location.hash, "#review");
  assert.equal(h.find("#review-level").value, "advanced"); assert.equal(h.find("#review-part").value, "1");
  assert.match(h.find(".review-full-text").textContent, /Updated explanation/);
  assert.equal(h.find("#review-feedback").value, "");
  h.click('[data-action="review-previous"]'); await h.settle();
  assert.match(h.find(".review-full-text").textContent, /advanced part 1/);
  h.click('[data-action="review-back"]'); await h.settle();
  assert.equal(h.document.querySelectorAll(".review-course-row").length, 1);
});

test("expired audio never hides retained teaching text or permits an A7 submission", async t => {
  const h = await harness(backend => {
    for (const lesson of Object.values(backend.bundles.get(id(10)).variants)) lesson.expires_at = new Date(Date.now() - 1000).toISOString();
  }); t.after(h.close);
  await h.navigate("learn"); assert.equal(h.document.querySelectorAll(".course-card").length, 0);
  await h.navigate("review"); assert.equal(h.document.querySelectorAll(".review-course-row").length, 1);
  h.click('[data-action="review-course"]'); await h.settle();
  assert.match(h.find(".review-full-text").textContent, /source material/);
  assert.match(h.find("main").textContent, /Generate the course again/);
  assert.equal(h.find("#review-form fieldset").disabled, true);
  h.input("#review-feedback", "This must not submit."); h.submit("#review-form"); await h.settle();
  assert.equal(h.backend.records("POST", "/v1/audio:regenerate").length, 0);
  assert.equal(h.backend.records("GET", `/v1/jobs/${id(10)}/result`).length, 0);
});

test("archived courses without an output deadline stay reviewable and playable", async t => {
  const h = await harness(backend => {
    const value = backend.bundles.get(id(10)); value.expires_at = null;
    for (const lesson of Object.values(value.variants)) lesson.expires_at = null;
  }); t.after(h.close);
  await h.navigate("review"); h.click('[data-action="review-course"]'); await h.settle();
  assert.equal(h.find("#review-feedback").matches(":disabled"), false);
  h.input("#review-feedback", "Clarify this saved explanation."); h.submit("#review-form"); await h.settle(); await h.runTimers(5000);
  assert.match(h.find(".review-full-text").textContent, /Updated explanation/);
  await h.navigate("learn"); assert.equal(h.document.querySelectorAll(".course-card").length, 1);
  h.click('[data-action="preview-course"]'); await h.settle();
  assert.match(h.find("#modal").textContent, /Text and narration are saved/);
  assert.doesNotMatch(h.find("#modal").textContent, /1970|Invalid Date/);
  h.click('[data-action="close-modal"]'); await h.settle();
  await h.start(); h.click('[data-action="toggle-play"]'); await h.settle(); assert.equal(h.audio[0].paused, false);
});

test("uncertain inline revisions require explicit preparation and never auto-resubmit", async t => {
  const h = await harness(); t.after(h.close);
  await h.navigate("review"); h.click('[data-action="review-course"]'); await h.settle();
  h.input("#review-feedback", "Make this clearer.");
  h.backend.failNext("POST", "/v1/audio:regenerate"); h.submit("#review-form"); await h.settle();
  assert.match(h.find(".review-feedback").textContent, /may already have been accepted/);
  assert.equal(h.document.querySelectorAll("#review-form").length, 0);
  h.click('[data-action="reload-review"]'); await h.settle();
  await h.navigate("learn"); await h.navigate("review"); await h.runTimers(5000);
  assert.equal(h.backend.records("POST", "/v1/audio:regenerate").length, 1);
  h.click('[data-action="prepare-new-review"]'); await h.settle();
  assert.equal(h.find("#review-feedback").value, "Make this clearer.");
  assert.equal(h.backend.records("POST", "/v1/audio:regenerate").length, 1);
});

test("inline revision tracking pauses on failure and resumes only after manual refresh", async t => {
  const h = await harness(); t.after(h.close);
  await h.navigate("review"); h.click('[data-action="review-course"]'); await h.settle();
  h.input("#review-feedback", "Improve this explanation.");
  h.backend.failNext("GET", `/v1/jobs/${id(30)}`); h.submit("#review-form"); await h.settle();
  assert.match(h.find(".review-progress").textContent, /Status updates paused/);
  await h.runTimers(5000); assert.equal(h.backend.records("GET", `/v1/jobs/${id(30)}`).length, 1);
  h.click('[data-action="retry-tracking"]'); await h.settle(); await h.runTimers(5000);
  assert.match(h.find(".review-full-text").textContent, /Updated explanation/);
  assert.equal(h.backend.records("POST", "/v1/audio:regenerate").length, 1);
});

test("review search and failed text loads keep retry controls and escape backend text", async t => {
  const h = await harness(backend => { backend.bundles.get(id(10)).variants.beginner.chunks[0].text = '<img src=x onerror="alert(1)"> A full explanation.'; }); t.after(h.close);
  await h.navigate("review"); h.input("#review-search", "nonexistent");
  assert.equal(h.document.querySelectorAll(".review-course-row").length, 0);
  h.input("#review-search", "real generated");
  h.backend.failNext("GET", `/v1/lesson-reviews/${id(10)}`, 404, "JOB_NOT_FOUND");
  h.click('[data-action="review-course"]'); await h.settle();
  assert.equal(h.document.querySelectorAll(".review-full-text").length, 0);
  h.click('[data-action="reload-review"]'); await h.settle();
  assert.match(h.find(".review-full-text").textContent, /<img/);
  assert.equal(h.find(".review-full-text").querySelectorAll("img").length, 0);
});

test("a late review response cannot replace a newly selected course", async t => {
  let resolveFirst;
  const h = await harness(backend => {
    backend.bundles.set(id(11), bundle(id(11), "Another course"));
    backend.intercept = call => call.path === `/v1/lesson-reviews/${id(10)}`
      ? new Promise(resolve => { resolveFirst = () => resolve(backend.response(backend.reviewText(backend.bundles.get(id(10))))); }) : undefined;
  }); t.after(h.close);
  await h.navigate("review"); h.click(`[data-action="review-course"][data-id="${id(10)}"]`); await h.settle();
  h.click('[data-action="review-back"]'); await h.settle();
  h.click(`[data-action="review-course"][data-id="${id(11)}"]`); await h.settle();
  resolveFirst(); await h.settle();
  assert.equal(h.find(".review-course-heading h2").textContent, "Another course");
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
