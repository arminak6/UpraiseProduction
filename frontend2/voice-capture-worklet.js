/* Capture bounded mono PCM; the output stays silent to avoid microphone feedback. */
class VoiceCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.limit = Math.floor(sampleRate * options.processorOptions.maxSeconds);
    this.count = 0;
    this.buffer = new Float32Array(2048);
    this.used = 0;
    this.done = false;
    this.port.onmessage = () => this.finish();
  }
  flush() {
    if (!this.used) return;
    const data = this.buffer.slice(0, this.used);
    this.port.postMessage({ samples: data }, [data.buffer]);
    this.used = 0;
  }
  finish() {
    if (this.done) return;
    this.done = true;
    this.flush();
    this.port.postMessage({ done: true });
  }
  process(inputs) {
    if (this.done) return false;
    const channels = inputs[0];
    if (!channels?.length) return true;
    for (let i = 0; i < channels[0].length && this.count < this.limit; i++) {
      let sample = 0;
      for (const channel of channels) sample += channel[i] / channels.length;
      this.buffer[this.used++] = sample;
      this.count++;
      if (this.used === this.buffer.length) this.flush();
    }
    if (this.count >= this.limit) this.finish();
    return !this.done;
  }
}
registerProcessor("upraise-voice-capture", VoiceCaptureProcessor);
