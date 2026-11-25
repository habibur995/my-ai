// AudioWorkletProcessor that downsamples microphone Float32 audio to 16kHz Int16 mono PCM.
class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._targetRate = 16000;
    this._chunkSamples = 1024; // ~64ms at 16kHz
    this._buffer = new Int16Array(this._chunkSamples);
    this._writeIdx = 0;
    this._phase = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0] || input[0].length === 0) {
      return true;
    }

    const channel = input[0];
    const ratio = sampleRate / this._targetRate;

    // Calculate input RMS for visual voice orb feedback & client VAD
    let sumSq = 0;
    for (let i = 0; i < channel.length; i++) {
      sumSq += channel[i] * channel[i];
    }
    const rms = Math.sqrt(sumSq / channel.length);

    while (this._phase < channel.length) {
      const idx = Math.floor(this._phase);
      const frac = this._phase - idx;
      const s0 = channel[idx];
      const s1 = idx + 1 < channel.length ? channel[idx + 1] : s0;
      const sample = s0 + (s1 - s0) * frac;
      const clamped = Math.max(-1, Math.min(1, sample));
      this._buffer[this._writeIdx++] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;

      if (this._writeIdx >= this._chunkSamples) {
        const out = this._buffer.slice(0);
        this.port.postMessage({ pcm: out.buffer, rms }, [out.buffer]);
        this._writeIdx = 0;
      }

      this._phase += ratio;
    }

    this._phase -= channel.length;
    return true;
  }
}

registerProcessor('pcm-capture-processor', PcmCaptureProcessor);
