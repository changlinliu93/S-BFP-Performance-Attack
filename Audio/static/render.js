/** Render the complete shuffled sequence, including silence, as float32 LE PCM. */
export async function renderAudio(config, monitor = null) {
  const OfflineContext = window.OfflineAudioContext || window.webkitOfflineAudioContext;
  if (!OfflineContext) {
    throw new Error("OfflineAudioContext not supported in this environment");
  }

  const samples = new Float32Array(config.total_frames);
  const duration = config.snippet_frames / config.sample_rate;
  let offset = 0;
  for (let i = 0; i < config.count; i++) {
    // Starting each oscillator/envelope at zero keeps its phase and floating
    // point timing independent of the shuffled position and preceding gaps.
    const context = new OfflineContext(1, config.snippet_frames, config.sample_rate);
    const oscillator = context.createOscillator();
    oscillator.type = config.waveforms[i];
    oscillator.frequency.setValueAtTime(config.frequencies[i], 0);

    const gain = context.createGain();
    gain.gain.setValueAtTime(0, 0);
    gain.gain.linearRampToValueAtTime(0.6, Math.min(0.01, duration / 10));
    gain.gain.setValueAtTime(0.6, duration - Math.min(0.02, duration / 5));
    gain.gain.linearRampToValueAtTime(0, duration);
    oscillator.connect(gain).connect(context.destination);
    oscillator.start(0);
    oscillator.stop(duration);

    const rendering = monitor
      ? monitor.measure("render", () => context.startRendering())
      : context.startRendering();
    const buffer = await rendering;
    samples.set(buffer.getChannelData(0), offset);
    offset += config.snippet_frames + (config.gap_frames[i] || 0);
  }

  // Explicit byte order preserves the Web Audio floats on every client platform.
  const pcm = new DataView(new ArrayBuffer(samples.byteLength));
  for (let i = 0; i < samples.length; i++) {
    pcm.setFloat32(i * 4, samples[i], true);
  }
  return pcm.buffer;
}
