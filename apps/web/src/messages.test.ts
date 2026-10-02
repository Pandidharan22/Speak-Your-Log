import { describe, expect, it } from "vitest";
import { MESSAGES, errorText } from "./messages";

// Every error code the backend can return to the page (see apps/api routers).
const BACKEND_CODES = [
  "invalid_token_format", "token_rejected", "token_cannot_post", "proof_rate_limited",
  "proof_unavailable", "too_many_attempts", "token_not_connected", "busy_try_shortly",
  "too_many_interviews", "voice_service_unavailable", "too_many_new_sessions", "network_error",
  "unknown",
];

describe("messages", () => {
  it.each(Object.keys(MESSAGES) as Array<keyof typeof MESSAGES>)("%s has text for every backend error code", (lang) => {
    for (const code of BACKEND_CODES) expect(MESSAGES[lang].errors[code], `${lang}:${code}`).toBeTruthy();
  });

  it("English and Tamil define exactly the same messages", () => {
    expect(Object.keys(MESSAGES.ta).sort()).toEqual(Object.keys(MESSAGES.en).sort());
    expect(Object.keys(MESSAGES.ta.errors).sort()).toEqual(Object.keys(MESSAGES.en.errors).sort());
  });

  it("Tamil text really is Tamil (not an accidental copy of the English)", () => {
    const tamil = /[\u0B80-\u0BFF]/;
    for (const key of ["tagline", "connectTitle", "startTitle", "readBackTitle", "publicWarning", "postedTitle"] as const) {
      expect(MESSAGES.ta[key]).toMatch(tamil);
    }
  });

  it("falls back to a generic message for an unknown error code", () => {
    expect(errorText(MESSAGES.en, "something_new")).toBe(MESSAGES.en.errors.unknown);
  });

  it("never mentions the token value, only its last four characters", () => {
    expect(MESSAGES.en.connectedAs("ZZZZ")).toContain("ZZZZ");
    expect(MESSAGES.ta.connectedAs("ZZZZ")).toContain("ZZZZ");
  });

  it("discloses that posts are public and that Google may use free-tier data", () => {
    expect(MESSAGES.en.publicWarning.toLowerCase()).toContain("public");
    expect(MESSAGES.en.privacyNotice).toContain("Google");
    expect(MESSAGES.en.privacyNotice.toLowerCase()).toContain("improve");
  });
});
