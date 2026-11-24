import { useCallback, useEffect, useState } from "react";
import { hudReducer, initialHud, type HudEvent, type HudState } from "./hud";
import type { Turn } from "./turns";

/** The HUD over time: it follows the chat and the block the car is in. */
export function useHud(turn: Turn | undefined, busy: boolean, currentBlock: number | undefined) {
  const [state, setState] = useState<HudState>(initialHud);
  const onEvent = useCallback((event: HudEvent) => setState((s) => hudReducer(s, event, performance.now())), []);
  const setAskedBlock = useCallback((askedBlock: number | undefined) => setState((s) => ({ ...s, askedBlock })), []);

  useEffect(() => setState((s) => ({ ...s, currentBlock })), [currentBlock]);
  useEffect(() => {
    if (turn?.answer) onEvent("delta");
  }, [turn?.answer, onEvent]);
  useEffect(() => {
    if (!busy) onEvent("done");
  }, [busy, onEvent]);
  useEffect(() => {
    if (!state.open) return;
    const timer = setInterval(() => onEvent("tick"), 250);
    return () => clearInterval(timer);
  }, [state.open, onEvent]);

  return { state, onEvent, setAskedBlock };
}
