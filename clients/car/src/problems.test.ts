import { describe, expect, it } from "vitest";
import { readProblem } from "./problems";

const answer = (status: number, body: unknown) => new Response(typeof body === "string" ? body : JSON.stringify(body), { status });

describe("readProblem", () => {
  it("keeps the code of the API", async () => {
    const problem = await readProblem(answer(422, { detail: { code: "unknown_attachment" } }));
    expect(problem.code).toBe("unknown_attachment");
    expect(problem.message).toBe("The API no longer has the photo. Ask again.");
  });

  it.each([
    [408, { detail: "The upload took too long" }, "http_408", /took too long/],
    [413, { detail: "Request body too large" }, "http_413", /too large/],
    [415, { detail: "Unknown kind of attachment" }, "http_415", /JPEG, PNG or WebP/],
    [503, { detail: { code: "busy" } }, "busy", /busy/],
    [409, { detail: "turn_in_progress" }, "http_409", /still answering/],
    [422, { detail: "Invalid multipart data" }, "http_422", /HTTP 422/],
    [503, { detail: "The conversation store is unavailable." }, "http_503", /not available/],
    [401, "", "http_401", /sign.*in again/i],
    [403, "", "http_403", /Chat\.User/],
    [500, "not json", "http_500", /HTTP 500/],
    [422, { detail: [{ loc: ["message"] }] }, "http_422", /HTTP 422/],
    [422, null, "http_422", /HTTP 422/],
  ])("gives %i %j a clear text", async (status, body, code, text) => {
    const problem = await readProblem(answer(status as number, body));
    expect(problem.code).toBe(code);
    expect(problem.message).toMatch(text as RegExp);
  });
});
