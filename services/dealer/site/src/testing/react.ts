// There is no DOM in these tests. This runs the hooks of a component tree, so that a test can click
// and see what the page shows then. Use it with:
//   vi.mock("react", async () => (await import("./testing/react")).mocked);
import type { ReactElement, ReactNode } from "react";
import { vi } from "vitest";

const actual = await vi.importActual<typeof import("react")>("react");

type Effect = { deps?: readonly unknown[]; cleanup?: void | (() => void); run?: () => void | (() => void) };
/** A host element. `dom` is what its ref points at. */
export interface El {
  type: string;
  props: Record<string, any>;
  children: Found[];
  dom: { focus: () => void; showModal: () => void; close: () => void; open: boolean };
}
export type Found = El | string;

let cells: unknown[] = [];
let at = 0;
let again: () => void = () => {};

function cell<T>(make: () => T): T {
  if (at === cells.length) cells.push(make());
  return cells[at++] as T;
}
const same = (a?: readonly unknown[], b?: readonly unknown[]) => !!a && !!b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));

function useState<T>(initial: T | (() => T)) {
  const rerender = again;
  const c = cell(() => {
    const box = {
      value: typeof initial === "function" ? (initial as () => T)() : initial,
      set: (next: T | ((old: T) => T)) => {
        const value = typeof next === "function" ? (next as (old: T) => T)(box.value) : next;
        if (Object.is(value, box.value)) return;
        box.value = value;
        rerender();
      },
    };
    return box;
  });
  return [c.value, c.set] as const;
}
const useRef = <T>(initial: T) => cell(() => ({ current: initial }));
function useMemo<T>(make: () => T, deps: readonly unknown[]): T {
  const c = cell<{ deps?: readonly unknown[]; value?: T }>(() => ({}));
  if (!same(c.deps, deps)) Object.assign(c, { deps, value: make() });
  return c.value as T;
}
const useCallback = <T>(f: T, deps: readonly unknown[]) => useMemo(() => f, deps);
function useEffect(run: () => void | (() => void), deps?: readonly unknown[]) {
  const c = cell<Effect & { effect: true }>(() => ({ effect: true }));
  if (same(c.deps, deps)) return;
  Object.assign(c, { deps, run });
}
function useSyncExternalStore<T>(subscribe: (f: () => void) => () => void, get: () => T): T {
  const rerender = again;
  useEffect(() => subscribe(rerender), [subscribe]);
  return get();
}
let ids = 0;
const useId = () => cell(() => `id${++ids}`);

export const mocked = { ...actual, useState, useRef, useMemo, useCallback, useEffect, useLayoutEffect: useEffect, useSyncExternalStore, useId };

/** Shows a tree, and shows it again after every change of state. */
export function show(root: ReactElement) {
  const memory = new Map<string, unknown[]>();
  const doms = new Map<string, El["dom"]>();
  const focused: { last?: string } = {};
  let tree: Found[] = [];
  let busy = false;
  let dirty = false;

  function walk(node: ReactNode, path: string, seen: Set<string>): Found[] {
    if (node === null || node === undefined || typeof node === "boolean") return [];
    if (typeof node === "string" || typeof node === "number") return [String(node)];
    if (Array.isArray(node)) return node.flatMap((n, i) => walk(n, `${path}/${(n as ReactElement | null)?.key ?? i}`, seen));
    const el = node as ReactElement<Record<string, any>>;
    if (el.type === actual.Fragment || el.type === actual.StrictMode) return walk(el.props.children, path, seen);
    if (typeof el.type === "function") {
      const here = `${path}<${el.type.name}:${el.key ?? ""}>`;
      seen.add(here);
      if (!memory.has(here)) memory.set(here, []);
      cells = memory.get(here)!;
      at = 0;
      again = render;
      const out = (el.type as (p: unknown) => ReactNode)(el.props);
      return walk(out, here, seen);
    }
    const here = `${path}<${String(el.type)}:${el.key ?? ""}>`;
    if (!doms.has(here)) {
      const dom: El["dom"] = { open: false, focus: () => (focused.last = path), showModal: () => (dom.open = true), close: () => (dom.open = false) };
      doms.set(here, dom);
    }
    const found: El = { type: String(el.type), props: el.props, children: walk(el.props.children, here, seen), dom: doms.get(here)! };
    const ref = el.props.ref as { current: unknown } | undefined;
    if (ref && typeof ref === "object") ref.current = found.dom;
    return [found];
  }

  function effects(list: unknown[]): Effect[] {
    return list.filter((c): c is Effect & { effect: true } => !!c && typeof c === "object" && "effect" in c);
  }

  function render() {
    dirty = true;
    if (busy) return;
    busy = true;
    for (let n = 0; dirty; n++) {
      if (n > 50) throw new Error("It never settles.");
      dirty = false;
      const seen = new Set<string>();
      tree = walk(root, "", seen);
      for (const [path, list] of memory) {
        if (seen.has(path)) {
          for (const e of effects(list)) {
            if (!e.run) continue;
            e.cleanup?.();
            e.cleanup = e.run();
            e.run = undefined;
          }
        } else {
          effects(list).forEach((e) => e.cleanup?.());
          memory.delete(path);
        }
      }
    }
    busy = false;
  }
  render();

  const flat = (list: Found[]): El[] => list.flatMap((f) => (typeof f === "string" ? [] : [f, ...flat(f.children)]));
  const text = (list: Found[] = tree): string => list.map((f) => (typeof f === "string" ? f : text(f.children) + (INLINE.has(f.type) ? "" : "\n"))).join("");
  const name = (e: El): string => e.props["aria-label"] ?? text(e.children).trim();
  const all = (type: string, from: Found[] = tree) => flat(from).filter((e) => e.type === type);
  function button(label: string): El {
    const found = all("button").filter((b) => name(b) === label);
    if (found.length !== 1) throw new Error(`${found.length} buttons named "${label}" among: ${all("button").map(name).join(", ")}`);
    return found[0];
  }
  return {
    all,
    text,
    name,
    button,
    has: (label: string) => all("button").some((b) => name(b) === label),
    click(label: string) {
      const b = button(label);
      if (b.props.disabled) throw new Error(`"${label}" is off.`);
      b.props.onClick({ preventDefault() {}, stopPropagation() {} });
    },
    update(next: ReactElement) {
      root = next;
      render();
    },
    focused,
    unmount() {
      for (const list of memory.values()) effects(list).forEach((e) => e.cleanup?.());
      memory.clear();
    },
  };
}
const INLINE = new Set(["b", "bdi", "span", "strong", "em", "option", "code"]);
