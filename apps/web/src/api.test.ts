import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, TERMINAL_STATES, api } from "./api";

const fetchMock = vi.fn();

beforeEach(() => vi.stubGlobal("fetch", fetchMock));
afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

describe("api client", () => {
  it("sends same-origin credentials and a JSON body only when there is one", async () => {
    fetchMock.mockReturnValueOnce(json(200, { connected: false, last4: null }));
    await api.session();
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("/api/session");
    expect(init).toMatchObject({ method: "POST", credentials: "same-origin", headers: undefined, body: undefined });
  });

  it("sends the Proof token in the request body, never in the URL", async () => {
    fetchMock.mockReturnValueOnce(json(200, { connected: true, last4: "ZZZZ" }));
    await api.connectToken("tok-SECRET-1234");
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).not.toContain("tok-SECRET");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body)).toEqual({ token: "tok-SECRET-1234" });
    expect(init.headers).toEqual({ "Content-Type": "application/json" });
  });

  it("never touches browser storage while handling a token", async () => {
    const set = vi.spyOn(Storage.prototype, "setItem");
    fetchMock.mockReturnValueOnce(json(200, { connected: true, last4: "ZZZZ" }));
    await api.connectToken("tok-SECRET-1234");
    expect(set).not.toHaveBeenCalled();
    set.mockRestore();
  });

  it("reduces any error to its stable code", async () => {
    fetchMock.mockReturnValueOnce(json(400, { detail: "token_rejected" }));
    await expect(api.connectToken("x")).rejects.toMatchObject({ status: 400, code: "token_rejected" });
  });

  it.each([
    ["a non-JSON body", () => Promise.resolve(new Response("<html>bad gateway</html>", { status: 502 }))],
    ["a JSON body without a string detail", () => json(500, { detail: { secret: "x" } })],
    ["an empty body", () => Promise.resolve(new Response(null, { status: 500 }))],
  ])("falls back to 'unknown' for %s and never leaks its text", async (_name, make) => {
    fetchMock.mockReturnValueOnce(make());
    const error = await api.session().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).toBe("unknown");
    expect(String(error)).not.toContain("bad gateway");
  });

  it("reports a network failure as 'network_error'", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await expect(api.session()).rejects.toMatchObject({ status: 0, code: "network_error" });
  });

  it("encodes the interview id so it cannot alter the path", async () => {
    fetchMock.mockReturnValueOnce(json(200, { state: "created", preview: null, proof_url: null }));
    await api.interview("../../etc/passwd?x=1");
    expect(fetchMock.mock.calls[0]![0]).toBe("/api/interviews/..%2F..%2Fetc%2Fpasswd%3Fx%3D1");
  });

  it("knows which interview states are final", () => {
    expect([...TERMINAL_STATES].sort()).toEqual(["cancelled", "failed", "post_unknown", "posted"]);
  });
});
