"""A3 voice choices, verified with Polly DescribeVoices on 2026-10-13.

Catalogue: neural engine, eu-central-1. No frontend AWS credentials or calls.
The backend still validates the selected voice against its current settings.
"""

import streamlit as st

# Voice ID -> (language code, display accent/language, gender).
VOICES = {
    "Joanna": ("en-US", "US English", "Female"),
    "Danielle": ("en-US", "US English", "Female"),
    "Gregory": ("en-US", "US English", "Male"),
    "Ivy": ("en-US", "US English (child)", "Female"),
    "Joey": ("en-US", "US English", "Male"),
    "Justin": ("en-US", "US English (child)", "Male"),
    "Kendra": ("en-US", "US English", "Female"),
    "Kevin": ("en-US", "US English (child)", "Male"),
    "Kimberly": ("en-US", "US English", "Female"),
    "Matthew": ("en-US", "US English", "Male"),
    "Ruth": ("en-US", "US English", "Female"),
    "Salli": ("en-US", "US English", "Female"),
    "Stephen": ("en-US", "US English", "Male"),
    "Amy": ("en-GB", "British English", "Female"),
    "Arthur": ("en-GB", "British English", "Male"),
    "Brian": ("en-GB", "British English", "Male"),
    "Emma": ("en-GB", "British English", "Female"),
    "Aria": ("en-NZ", "New Zealand English", "Female"),
    "Ayanda": ("en-ZA", "South African English", "Female"),
    "Jasmine": ("en-SG", "Singaporean English", "Female"),
    "Kajal": ("en-IN", "Indian English", "Female"),
    "Niamh": ("en-IE", "Irish English", "Female"),
    "Olivia": ("en-AU", "Australian English", "Female"),
    "Bianca": ("it-IT", "Italian", "Female"),
    "Adriano": ("it-IT", "Italian", "Male"),
}


def voice_label(voice):
    _, accent, gender = VOICES[voice]
    return f"{voice} · {accent} · {gender}"


def render_language_voice(language_label, *, language_key, voice_key):
    """Keep selectors outside submission forms so language changes filter immediately."""
    left, right = st.columns(2)
    language = left.text_input(language_label, key=language_key)
    code = language.strip().lower()
    options = [voice for voice, (voice_code, _, _) in VOICES.items()
               if voice_code.lower() == code or ("-" not in code and voice_code.split("-")[0] == code)]
    if options:
        if st.session_state.get(voice_key) not in options:
            st.session_state[voice_key] = "Bianca" if code.split("-")[0] == "it" else options[0]
        voice = right.selectbox("Polly voice", options, key=voice_key, format_func=voice_label)
    else:
        # Retain the existing manual API tools for languages beyond EN/IT.
        voice = right.text_input("Polly voice", key=voice_key)
        right.caption("Voice choices are available for English (en) and Italian (it).")
    return language, voice
