/* In-tab recording only; B4 receives mono 16 kHz, 16-bit PCM WAV. */
(() => {
  "use strict";
  const MAX_SECONDS = 60;
  const RATE = 16000;

  function supported() {
    return Boolean(window.isSecureContext && navigator.mediaDevices?.getUserMedia
      && (window.AudioContext || window.webkitAudioContext) && window.AudioWorkletNode);
  }
  function encodeWav(parts, length, sampleRate) {
    const samples = new Float32Array(length);
    let offset = 0;
    for (const part of parts) { samples.set(part, offset); offset += part.length; }
    const count = Math.floor(length * RATE / sampleRate);
    const bytes = new ArrayBuffer(44 + count * 2), view = new DataView(bytes);
    const text = (at, value) => [...value].forEach((char, i) => view.setUint8(at + i, char.charCodeAt(0)));
    text(0, "RIFF"); view.setUint32(4, 36 + count * 2, true); text(8, "WAVE"); text(12, "fmt ");
    view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, RATE, true); view.setUint32(28, RATE * 2, true);
    view.setUint16(32, 2, true); view.setUint16(34, 16, true); text(36, "data"); view.setUint32(40, count * 2, true);
    // Average each source interval when downsampling instead of discarding samples.
    for (let i = 0; i < count; i++) {
      const from = i * sampleRate / RATE, to = Math.min(length, (i + 1) * sampleRate / RATE);
      let sum = 0;
      for (let j = Math.floor(from); j < Math.ceil(to); j++) {
        sum += samples[j] * (Math.min(j + 1, to) - Math.max(j, from));
      }
      const value = Math.max(-1, Math.min(1, sum / (to - from)));
      view.setInt16(44 + i * 2, Math.round(value * (value < 0 ? 32768 : 32767)), true);
    }
    return new Blob([bytes], { type: "audio/wav" });
  }
  function errorMessage(error) {
    if (["NotAllowedError", "SecurityError"].includes(error?.name)) return "Microphone access was denied. Allow it in your browser, or type your question.";
    if (["NotFoundError", "DevicesNotFoundError"].includes(error?.name)) return "No microphone was found. Connect one, or type your question.";
    if (error?.name === "NotReadableError") return "The microphone is unavailable or in use. Try again, or type your question.";
    return error?.message || "Recording could not start. Try again, or type your question.";
  }
  class Recorder {
    constructor({ onProgress, onLimit, onError }) {
      Object.assign(this, { onProgress, onLimit, onError, parts: [], length: 0, cancelled: false, done: false });
    }
    async start() {
      if (!supported()) throw new Error("Voice recording needs a supported browser at localhost or over HTTPS. You can still type your question.");
      try {
        const Context = window.AudioContext || window.webkitAudioContext;
        this.context = new Context();
        await this.context.resume();
        if (this.cancelled) return;
        this.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true }, video: false });
        if (this.cancelled) { this.release(); return; }
        await this.context.audioWorklet.addModule("./voice-capture-worklet.js");
        if (this.cancelled) return;
        this.node = new AudioWorkletNode(this.context, "upraise-voice-capture", { processorOptions: { maxSeconds: MAX_SECONDS } });
        this.node.port.onmessage = ({ data }) => {
          if (this.cancelled) return;
          if (data.samples) {
            this.parts.push(data.samples); this.length += data.samples.length;
            this.onProgress?.(this.length / this.context.sampleRate);
          }
          if (data.done) {
            this.done = true;
            if (this.resolveStop) this.resolveStop(); else this.onLimit?.();
          }
        };
        this.node.onprocessorerror = () => this.onError?.("The microphone recording was interrupted. Please record again.");
        for (const track of this.stream.getTracks()) track.onended = () => this.onError?.("The microphone was disconnected. Please record again.");
        this.source = this.context.createMediaStreamSource(this.stream);
        this.source.connect(this.node); this.node.connect(this.context.destination);
        // A wall-clock bound also releases the microphone if capture stops producing data.
        this.timer = setTimeout(() => this.onLimit?.(), MAX_SECONDS * 1000);
      } catch (error) { this.cancel(); throw error; }
    }
    async finish() {
      if (this.cancelled || !this.node) throw new Error("There is no recording to send. Please record again.");
      try {
        if (!this.done) await new Promise((resolve, reject) => {
          this.resolveStop = resolve;
          this.rejectStop = reject;
          this.stopTimer = setTimeout(() => reject(new Error("Recording could not be saved. Please record again.")), 2000);
          this.node.port.postMessage({ stop: true });
        });
        if (this.cancelled) throw new DOMException("Recording cancelled", "AbortError");
        const seconds = this.length / this.context.sampleRate;
        if (seconds < .2) throw new Error("The recording is too short. Record your question again.");
        return { blob: encodeWav(this.parts, this.length, this.context.sampleRate), seconds };
      } finally { this.release(); this.parts = []; }
    }
    release() {
      clearTimeout(this.timer); clearTimeout(this.stopTimer);
      for (const track of this.stream?.getTracks() || []) { track.onended = null; track.stop(); }
      this.source?.disconnect(); this.node?.disconnect();
      if (this.node) { this.node.port.onmessage = null; this.node.port.close(); }
      if (this.context?.state !== "closed") this.context?.close().catch(() => {});
    }
    cancel() {
      this.cancelled = true;
      this.rejectStop?.(new DOMException("Recording cancelled", "AbortError"));
      this.release(); this.parts = [];
    }
  }
  window.UpraiseVoice = Object.freeze({ Recorder, supported, errorMessage, MAX_SECONDS });
})();
