// Takes a photo of each sign at 50 and at 90 km/h, with the camera of the car, and saves them in out/.
// scripts/spikes/check_sign_photos.py then shows them to the photo handler of the API.
//
//   npm run sign-photos     (CHROME=<path> when Chrome is somewhere else)
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import { createServer } from "vite";

const CHROME = process.env.CHROME ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const root = new URL("..", import.meta.url).pathname;
const out = join(root, "out");
const profile = mkdtempSync(join(tmpdir(), "sign-photos-"));

rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });

let finish;
const finished = new Promise((resolve) => (finish = resolve));
const receive = {
  name: "receive-photos",
  configureServer(server) {
    server.middlewares.use("/__photo", async (req, res) => {
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      const body = Buffer.concat(chunks);
      const name = basename(req.url);
      if (name === "done") finish();
      else if (name === "failed") finish(body.toString());
      else writeFileSync(join(out, name), body);
      res.end();
    });
  },
};

const server = await createServer({ root, plugins: [receive], server: { port: 5399, strictPort: false }, logLevel: "error" });
await server.listen();
const url = `${server.resolvedUrls.local[0]}scripts/sign-photos.html`;
const chrome = spawn(CHROME, ["--headless=new", "--no-first-run", "--mute-audio", `--user-data-dir=${profile}`, url], { stdio: "ignore" });
const problem = await Promise.race([
  finished,
  new Promise((resolve) => chrome.on("error", (e) => resolve(`Chrome did not start: ${e.message}`))),
  new Promise((resolve) => setTimeout(resolve, 90_000, "The photos took too long.")),
]);
const gone = new Promise((resolve) => chrome.on("exit", resolve));
if (chrome.kill()) await gone;
await server.close();
rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
if (problem) {
  console.error(problem);
  process.exit(1);
}
console.log(`16 photos in ${out}`);
process.exit(0); // the timer above is still running
