import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { askToPark, PARK_QUESTION } from "./ask";
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

interface Props {
  config: AppConfig;
  auth: Auth;
  session: AuthSession;
}

/** What the HUD says while the car does something that takes time. */
const DOING: Record<string, string | undefined> = { park: "Parking the car", get_sign_photo: "Looking at the sign" };

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

  // The clock of the car runs on from where it was set.
  const offset = useRef(0);
  const now = useCallback(() => new Date(Date.now() + offset.current), []);
  const setClock = useCallback((time: Date | undefined) => {
    offset.current = time ? time.getTime() - Date.now() : 0;
  }, []);

  const [doing, setDoing] = useState<string>();
  const runTool = useCallback(
    async (call: ClientToolCall) => {
      setDoing(DOING[call.name]);
      const result = await runCarTool(call, drive).finally(() => setDoing(undefined));
      if (result.attachments) setAskedBlock.current(drive.askedBlock);
      return result;
    },
    [drive],
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
  function say(text: string) {
    if (!free) return;
    setSourceError(undefined);
    onEvent("send");
    send(text);
  }
  function parkHere() {
    if (!free) return;
    setSourceError(undefined);
    onEvent("send");
    setPending(PARK_QUESTION);
    void askToPark(() => drive.signPhoto(), (text, attachments) => {
      setPending(undefined);
      if (attachments) hud.setAskedBlock(drive.askedBlock);
      send(text, attachments);
    });
  }
  function openDocument(source: SourceItem) {
    if (!source.link) return;
    const link = source.link;
    setSourceError(undefined);
    openSource(async () => fetchSourceUrl(config.apiUrl, await signedIn(auth), link)).catch((e: unknown) =>
      setSourceError(e instanceof Error ? e.message : "The document could not be opened. Try again."),
    );
  }

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
      />
      {underTheHood && <UnderTheHood state={state} backend={backend} onBackend={setBackend} busy={!free} />}
      <Menu
        onNewChat={() => {
          reset();
          onEvent("escape");
        }}
        onSignOut={() => void auth.signOut()}
        username={session.username}
        underTheHood={underTheHood}
        onUnderTheHood={(on) => {
          setUnderTheHood(on);
          rememberUnderTheHood(on);
        }}
      />
    </div>
  );
}
