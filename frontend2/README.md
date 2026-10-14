# UPRAISE interface preview

A standalone client-demo prototype with three sections: **Upload material**,
**Generate course**, and **Start learning**. This directory is independent of
the existing Streamlit frontend and backend.

## Run locally

From the repository root:

```powershell
python frontend2/serve.py
```

Open **http://127.0.0.1:8502**. Stop with Ctrl+C. To use a different port:

```powershell
python frontend2/serve.py --port 8503
```

Only Python 3.10+ and a modern browser are needed. No npm packages, API keys,
Docker services, internet connection, or Python dependencies are required.
The server binds to the local machine and exposes only the static preview assets.
The UI can also be opened from `index.html` directly in a modern browser.

## What can be reviewed

- Responsive UPRAISE branding, three-step navigation, helpful empty states,
  keyboard access, and preview notices.
- Upload selection/drop zone, basic extension/size validation, sample material
  library, search, and confirmed removal with dependent sample-course cleanup.
- Course source/title/language/voice preferences, advanced options, three aligned
  levels, simulated generation progress/cancellation, result preview and revision
  request dialog.
- Sample course cards, level selection, level-specific English text, part
  navigation, simulated playback/speed/seek/replay, example teacher replies,
  session completion, and demo reset.

## Deliberate preview boundaries

- No backend or external services are connected. The page's Content Security
  Policy disallows fetch/WebSocket connections; all assets are local.
- Files are **never uploaded or read**. Only their name, size, and selected
  course/language are held in tab memory. A short timer simulates readiness.
- Course generation is a timed UI demonstration using curated fixtures, including
  when a user selects their own document. It does not extract source content.
- Language and voice selections are illustrative; all sample lessons are in
  English. The learning player has no actual audio.
- Suggested questions return prepared example explanations; other questions
  show a placeholder reply. No AI model is invoked.
- Revisions only demonstrate the form and a confirmation message. There is no
  approval action, backend submission, certificate, or permanent learning record.
- No browser storage, cookies, analytics, or persistence. Reload or **Reset demo**
  restores the fixtures. Progress only describes navigation through this preview.

## Files and next stage

- `index.html`: semantic shell, navigation, local assets and content policy.
- `styles.css`: shared visual system and responsive layouts.
- `demo-data.js`: sample material, courses, level profiles and lesson content.
- `app.js`: view rendering and in-memory demo interactions.
- `serve.py`: optional dependency-free static preview server.

Validation on 2026-10-14: JavaScript/Python syntax, HTML/CSS parsing, static asset
HTTP responses, denied non-asset paths, and 18 DOM interaction checks passed.
Browser rendering and responsive visual QA remain unverified because the browser
preview tool was unavailable in that session. Ad hoc validation tooling lives
under the ignored `codex_helper/frontend2-validation/`, outside runtime source.

After UI approval, a separate implementation stage can replace these demo actions
with authenticated backend calls. Preserve the current backend's draft/review,
source ownership, session, level alignment, revision, deletion and audio integrity
rules. This prototype makes no backend integration changes.
