# Original course-generation V3

Copied from ../Uprise/v2/backend/learning/v1 on 2026-10-05. Despite the
BedrockTeacherV1 name, the working project's V3 course generation uses this
shared teacher with PollySpeechSynthesizerV3.

origin.json records normalized UTF-8 source/local hashes and the only
relocations: the local SHA-256 utility import and installed math-runtime paths.
All original class/function bodies are preserved. Original tests are copied to
tests/test_v3_original_*.py with their import paths relocated.

A3 calls explain -> review_explanation -> finalize_explanation. It selects the
V3 speech class, MathJax converter and narrate_math_fallback. The shared
math_speech_v4 module supplies original finalization helpers; its alternative
speech pipeline is not selected.

Keep API-specific code in the surrounding lesson_models, lesson_compat,
lesson_speech and lesson_pipeline modules. Those modules adapt language/voice,
page windows, API chunks, SSML, additional viseme requests, job checkpoints,
review reports and delivery. The adapter adds the requested explanation level
to the generator/reviewer/editor system prompts, and the requested language
for non-English output. Regression tests compare the base requests and output
contracts with the original teacher after removing those deliberate additions.
The MathJax JavaScript runtime matches
the original docker/math_speech.mjs.

Update source hashes and parity tests deliberately if this bundle is changed.

