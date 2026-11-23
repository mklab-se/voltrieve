/** A car horn made with Web Audio: two tones at once. */

const TONES_HZ = [410, 515];
const HONK_S = 0.35;
const GAP_S = 0.15;
const VOLUME = 0.15;

export type Horn = (times: number) => Promise<boolean>;

/**
 * The result is false when the browser blocks the sound, which it does until the first click. A
 * browser then leaves resume() waiting, so this waits only a little for it.
 */
export function createHorn(makeAudio: () => AudioContext = () => new AudioContext(), waitMs = 300): Horn {
  let audio: AudioContext | undefined;
  return async (times) => {
    try {
      audio ??= makeAudio();
      if (audio.state !== "running") {
        await Promise.race([audio.resume(), new Promise((resolve) => setTimeout(resolve, waitMs))]);
      }
      if ((audio.state as string) !== "running") return false;
    } catch {
      return false;
    }
    for (let i = 0; i < times; i += 1) honkAt(audio, audio.currentTime + 0.02 + i * (HONK_S + GAP_S));
    return true;
  };
}

function honkAt(audio: AudioContext, start: number): void {
  const end = start + HONK_S;
  const volume = audio.createGain();
  volume.connect(audio.destination);
  // Short ramps: a tone that starts or ends at full volume clicks.
  volume.gain.setValueAtTime(0, start);
  volume.gain.linearRampToValueAtTime(VOLUME, start + 0.02);
  volume.gain.setValueAtTime(VOLUME, end - 0.04);
  volume.gain.linearRampToValueAtTime(0, end);
  for (const hz of TONES_HZ) {
    const tone = audio.createOscillator();
    tone.type = "sawtooth";
    tone.frequency.value = hz;
    tone.connect(volume);
    tone.start(start);
    tone.stop(end);
  }
}
