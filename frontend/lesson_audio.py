"""Browser audio control that captures an interruption before the server reruns."""

import base64
from pathlib import Path

import streamlit.components.v2 as components

audio_control = components.component(
    "lesson_audio",
    html='<audio controls aria-label="Lesson voice"></audio>'
         '<p class="audio-note" role="status"></p>'
         '<button type="button">User interruption</button>',
    css="""
    audio { display: block; width: 100%; }
    button { margin-top: .5rem; padding: .55rem .9rem; border-radius: .5rem;
      border: 1px solid var(--st-primary-color); background: var(--st-primary-color);
      color: white; font: inherit; cursor: pointer; }
    button:disabled { opacity: .5; cursor: default; }
    button:focus-visible { outline: 3px solid var(--st-text-color); outline-offset: 2px; }
    .audio-note { font-size: .85rem; color: var(--st-text-color); margin: .3rem 0; }
    """,
    js=Path(__file__).with_name("lesson_audio.js").read_text(encoding="utf-8"),
)


def render_audio(state, chunk):
    result = audio_control(
        key="learning_audio_control",
        data={"audio": base64.b64encode(state["audio"]).decode("ascii"),
              "playback": state["playback"], "chunk_id": chunk["chunk_id"],
              "offset_ms": state.get("resume_offset_ms", 0),
              "rate": state.get("playback_rate", 1.0),
              "autoplay": state.get("autoplay", True)},
        on_interrupt_change=lambda: None,
    )
    return result.interrupt
