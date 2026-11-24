/** 529000 becomes "529 000 SEK", with spaces that do not break. */
export function money(sek: number): string {
  return `${String(Math.round(sek)).replace(/\B(?=(\d{3})+$)/g, " ")} SEK`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const two = (n: number) => String(n).padStart(2, "0");

/** The hour, with the day when it is not today. */
export function when(iso: string | null, now = new Date()): string {
  const d = new Date(iso ?? NaN);
  if (Number.isNaN(d.getTime())) return "";
  const hour = `${two(d.getHours())}:${two(d.getMinutes())}`;
  return d.toDateString() === now.toDateString() ? hour : `${d.getDate()} ${MONTHS[d.getMonth()]} ${hour}`;
}

const FULL_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

/** A day, written out. */
export function day(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : `${d.getDate()} ${FULL_MONTHS[d.getMonth()]} ${d.getFullYear()}`;
}

/** The time of a log entry, with seconds. */
export function clock(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`;
}

const CLIENTS = new Map([["web", "website"], ["cli", "command line"], ["car", "car"]]);
export const clientName = (client: string) => CLIENTS.get(client) ?? client;

export function place(client: string): string {
  if (client === "web") return "on the website";
  if (client === "cli") return "in the command line";
  return `from ${clientName(client)}`;
}
