export default function(component) {
    const {data, parentElement, setTriggerValue} = component;
    const audio = parentElement.querySelector("audio");
    const button = parentElement.querySelector("button");
    const note = parentElement.querySelector(".audio-note");
    const playback = `${data.chunk_id}:${data.playback}`;

    // Keep the same audio element/position on unrelated Streamlit reruns.
    if (audio.dataset.playback !== playback) {
        audio.pause();
        audio.dataset.playback = playback;
        audio.src = `data:audio/wav;base64,${data.audio}`;
        button.disabled = false;
        note.textContent = "";
        const ready = () => {
            const seconds = Math.max(0, Number(data.offset_ms) / 1000 || 0);
            audio.currentTime = Math.min(seconds, Number.isFinite(audio.duration) ? audio.duration : seconds);
            audio.playbackRate = data.rate;
            if (data.autoplay) {
                audio.play().catch(() => {
                    note.textContent = "Press Play to listen to the lesson.";
                });
            }
        };
        audio.onloadedmetadata = ready;
        audio.load();
    }
    audio.playbackRate = data.rate;
    audio.onerror = () => { note.textContent = "Voice could not be played. Try Replay voice."; };
    button.onclick = () => {
        // Pause synchronously and send the exact position in the same event.
        audio.pause();
        button.disabled = true;
        setTriggerValue("interrupt", {
            chunk_id: data.chunk_id,
            playback: data.playback,
            offset_ms: Math.round(audio.currentTime * 1000)
        });
    };
    return () => {
        button.onclick = null;
        audio.onloadedmetadata = null;
        audio.onerror = null;
        // Removing a component (question panel, next chunk, end) stops its audio.
        audio.pause();
    };
}
