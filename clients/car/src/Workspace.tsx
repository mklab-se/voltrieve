import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { askToPark, PARK_QUESTION, runShown } from "./ask";
import { uploadPhoto } from "./attachments";
import type { Auth, AuthSession } from "./auth";
import { Cockpit } from "./components/Cockpit";
import { Menu } from "./components/Menu";
import { readUnderTheHood, rememberUnderTheHood, UnderTheHood } from "./components/UnderTheHood";
import type { AppConfig } from "./config";
import { carContext } from "./context";
import { Drive } from "./drive";
import { createHorn } from "./horn";
import { createMotor } from "./motor";
import { Pedals } from "./pedals";
import type { StreetScene } from "./scene/Scene";
import { fetchSourceUrl, openSource } from "./sources";
import { CAR_TOOLS, runCarTool } from "./tools";
import type { Backend, ClientToolCall, SourceItem } from "./types";
import { useChat } from "./useChat";
import { useDriving } from "./useDriving";
import { useHud } from "./useHud";
import { useRemote } from "./useRemote";
import { useStable } from "./useStable";

interface Props {
  config: AppConfig;
  auth: Auth;
  session: AuthSession;
}

/** The token, or an error in words for the driver. */
const signedIn = (auth: Auth) => auth.getToken().catch(() => Promise.reject(new Error("You could not be signed in. Try again.")));

export function Workspace({ config, auth, session }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const scene = useRef<StreetScene | undefined>(undefined);
  const [drive] = useState(() => {
    const made: Drive = new Drive(
      createHorn(),
      (block) => scene.current?.photo(made.car, block) ?? Promise.reject(new Error("The street is not drawn yet.")),
      async ({ block, blob }) => {
        const token = await signedIn(auth);
        return uploadPhoto({ apiUrl: config.apiUrl, token, blob, label: `Parking sign in block ${block + 1}` });
      },
    );
    return made;
  });
  const [motor] = useState(() => createMotor());
  const pedals = useMemo(() => new Pedals(drive), [drive]);
  const driving = useDriving(drive, canvas, scene, motor);

  const remote = useRemote(config.apiUrl, auth, drive);

  // The clock of the car runs on from where it was set.
  const offset = useRef(0);
  const now = useCallback(() => new Date(Date.now() + offset.current), []);
  const setClock = useCallback((time: Date | undefined) => {
    offset.current = time ? time.getTime() - Date.now() : 0;
  }, []);

  const [doing, setDoing] = useState<string>();
  // What an old chat still waits for must not show up in a new one.
  const generation = useRef(0);
  const sameChat = useCallback(() => {
    const mine = generation.current;
    return () => generation.current === mine;
  }, []);
  const runTool = useCallback(
    (call: ClientToolCall) =>
      runShown(call, (c) => runCarTool(c, drive), {
        current: sameChat(),
        doing: setDoing,
        asked: () => setAskedBlock.current(drive.askedBlock),
      }),
    [drive, sameChat],
  );
  const context = useCallback(() => carContext(drive.car, now()), [drive, now]);
  const renew = useCallback((ids: string[]) => drive.renewPhotos(ids), [drive]);
  const [backend, setBackend] = useState<Backend>("loop");
  const [underTheHood, setUnderTheHood] = useState(readUnderTheHood);
  const onStop = useCallback(() => drive.forgetParking(), [drive]);
  const chat = useChat({ auth, config, backend, tools: CAR_TOOLS, runTool, context, renew, onStop });
  const { state, busy, send, reset } = chat;

  const hud = useHud(state.turns.at(-1), busy, driving.gauges.block);
  const setAskedBlock = useRef(hud.setAskedBlock);
  const { onEvent } = hud;

  const [pending, setPending] = useState<string>();
  const [sourceError, setSourceError] = useState<string>();
  const [motorOn, setMotorOn] = useState(false);
  useEffect(() => motor.setOn(motorOn), [motor, motorOn]);

  const free = !busy && pending === undefined;
  const say = useStable((text: string) => {
    if (!free) return;
    setSourceError(undefined);
    onEvent("send");
    send(text);
  });
  function parkHere() {
    if (!free) return;
    setSourceError(undefined);
    onEvent("send");
    setPending(PARK_QUESTION);
    const ask = (text: string, attachments?: string[]) => {
      setPending(undefined);
      if (attachments) hud.setAskedBlock(drive.askedBlock);
      send(text, attachments);
    };
    void askToPark(() => drive.signPhoto(), ask, sameChat());
  }
  function newChat() {
    generation.current += 1;
    drive.startOver();
    reset();
    setPending(undefined);
    setDoing(undefined);
    setSourceError(undefined);
    hud.setAskedBlock(undefined);
    onEvent("escape");
  }
  const openDocument = useStable((source: SourceItem) => {
    if (!source.link) return;
    const link = source.link;
    setSourceError(undefined);
    openSource(async () => fetchSourceUrl(config.apiUrl, await signedIn(auth), link)).catch((e: unknown) =>
      setSourceError(e instanceof Error ? e.message : "The document could not be opened. Try again."),
    );
  });

  return (
    <div className="car">
      <p className="too-small">The car needs a larger window. Make it at least 1280 pixels wide.</p>
      <Cockpit
        canvas={canvas}
        driving={driving}
        pedals={pedals}
        chat={state}
        busy={!free}
        pending={pending}
        doing={doing}
        hud={hud.state}
        onHud={onEvent}
        now={now}
        onSetClock={setClock}
        motorOn={motorOn}
        onMotor={setMotorOn}
        onSend={say}
        onPark={parkHere}
        sourceError={sourceError}
        onOpenSource={openDocument}
        remote={remote}
      />
      {underTheHood && <UnderTheHood state={state} backend={backend} onBackend={setBackend} busy={!free} />}
      <Menu
        onNewChat={newChat}
        onSignOut={() => {
          remote.setOn(false);
          void auth.signOut();
        }}
        username={session.username}
        underTheHood={underTheHood}
        onUnderTheHood={(on) => {
          setUnderTheHood(on);
          rememberUnderTheHood(on);
        }}
        remote={remote.on}
        onRemote={config.remoteControl ? remote.setOn : undefined}
      />
    </div>
  );
}
