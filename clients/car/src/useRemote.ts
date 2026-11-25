import { useEffect, useState } from "react";
import type { Auth } from "./auth";
import type { Drive } from "./drive";
import { createLights } from "./lights";
import { carState, commandText, handleCommand, standsStill, type RemoteControls } from "./remote";
import { RemoteLink, type LinkStatus } from "./remoteLink";

const NOTE_MS = 8000;
const LOOK_MS = 100;

const ENDED: Partial<Record<LinkStatus, string>> = {
  unavailable: "Remote control is not available here.",
  replaced: "Remote control is off: it is on in too many windows.",
  sign_in: "Remote control is off: sign in again to use it.",
};
const MAX_NOTES = 2;

export interface RemoteNote {
  id: number;
  text: string;
}

export interface Remote {
  /** Off whenever the page loads: it is remembered nowhere. */
  on: boolean;
  setOn: (on: boolean) => void;
  status: LinkStatus;
  /** What was done and refused just now. */
  notes: RemoteNote[];
  flashing: boolean;
}

let lastNote = 0;

/** Remote control: while it is on, the driver's own AI can reach the car through the API. */
export function useRemote(apiUrl: string, auth: Auth, drive: Drive): Remote {
  const [on, setOn] = useState(false);
  const [status, setStatus] = useState<LinkStatus>("connecting");
  const [notes, setNotes] = useState<RemoteNote[]>([]);
  const [flashing, setFlashing] = useState(false);
  const [lights] = useState(() => createLights(setFlashing));

  useEffect(() => {
    if (!on) return;
    const note = (text: string) => {
      const id = (lastNote += 1);
      setNotes((all) => [...all, { id, text }].slice(-MAX_NOTES));
      setTimeout(() => setNotes((all) => all.filter((n) => n.id !== id)), NOTE_MS);
    };
    const controls: RemoteControls = {
      honk: (times) => drive.honk(times),
      setWipers: (mode) => drive.setWipers(mode),
      flashLights: (times) => lights.flash(times),
    };
    const link: RemoteLink = new RemoteLink({
      apiUrl,
      getToken: () => auth.getTokenSilently(),
      state: () => carState(drive.car, drive.wipers),
      onCommand: async (command) => {
        const code = await handleCommand(command, { on: !link.closed, car: drive.car, controls });
        note(commandText(command, code));
        return code;
      },
      onStatus: (next) => {
        setStatus(next);
        const why = ENDED[next];
        if (!why) return;
        note(why);
        setOn(false);
      },
    });
    void link.run();
    const off = () => {
      link.close();
      setOn(false);
    };
    window.addEventListener("pagehide", off);
    return () => {
      window.removeEventListener("pagehide", off);
      link.close();
      lights.stop();
    };
  }, [on, apiUrl, auth, drive, lights]);

  // A car that drives does not flash.
  useEffect(() => {
    if (!flashing) return;
    const timer = setInterval(() => {
      if (!standsStill(drive.car)) lights.stop();
    }, LOOK_MS);
    return () => clearInterval(timer);
  }, [flashing, drive, lights]);

  return { on, setOn, status, notes, flashing };
}
