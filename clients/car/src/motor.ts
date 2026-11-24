/** The soft hum of an electric motor, made with Web Audio. It is off until the driver turns it on. */

/** How the motor sounds at a speed. */
export function motorTone(speedKmh: number): { hz: number; volume: number } {
  const share = Math.min(speedKmh / 150, 1);
  return { hz: 70 + 250 * share, volume: share === 0 ? 0 : 0.012 + 0.03 * share };
}

export interface Motor {
  setOn(on: boolean): void;
  setSpeed(speedKmh: number): void;
}

export function createMotor(makeAudio: () => AudioContext = () => new AudioContext()): Motor {
  let audio: AudioContext | undefined;
  let parts: { tones: OscillatorNode[]; volume: GainNode; filter: BiquadFilterNode } | undefined;
  let on = false;
  let speed = 0;

  function start(context: AudioContext) {
    const volume = context.createGain();
    volume.gain.value = 0;
    const filter = context.createBiquadFilter();
    filter.type = "lowpass";
    const tones = [1, 2.01, 3.98].map((times, i) => {
      const tone = context.createOscillator();
      tone.type = i === 0 ? "triangle" : "sine";
      const share = context.createGain();
      share.gain.value = [1, 0.35, 0.12][i]!;
      tone.connect(share).connect(filter);
      tone.frequency.value = 70 * times;
      tone.start();
      return tone;
    });
    filter.connect(volume).connect(context.destination);
    return { tones, volume, filter };
  }

  function update() {
    if (!audio || !parts) return;
    const { hz, volume } = motorTone(speed);
    const now = audio.currentTime;
    parts.tones.forEach((tone, i) => tone.frequency.setTargetAtTime(hz * [1, 2.01, 3.98][i]!, now, 0.12));
    parts.filter.frequency.setTargetAtTime(300 + hz * 3, now, 0.12);
    parts.volume.gain.setTargetAtTime(on ? volume : 0, now, 0.15);
  }

  return {
    setOn(value) {
      on = value;
      if (on && !parts) {
        try {
          audio = makeAudio();
          parts = start(audio);
        } catch {
          return; // a browser without sound
        }
      }
      if (on) void audio?.resume();
      update();
    },
    setSpeed(value) {
      if (value === speed) return;
      speed = value;
      update();
    },
  };
}
