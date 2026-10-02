import { describe, expect, it } from "vitest";
import { upsertLine, type Line } from "./transcript";

const line = (id: string, text: string, final = false, who: Line["who"] = "student"): Line => ({ id, who, text, final });

describe("upsertLine", () => {
  it("appends a new segment and keeps order", () => {
    const a = upsertLine([], line("student:1", "hello"));
    const b = upsertLine(a, line("agent:1", "hi there", true, "agent"));
    expect(b.map((l) => l.id)).toEqual(["student:1", "agent:1"]);
  });

  it("replaces an interim line with its update instead of duplicating it", () => {
    let lines = upsertLine([], line("student:1", "I tried"));
    lines = upsertLine(lines, line("student:1", "I tried an IR sensor"));
    expect(lines).toHaveLength(1);
    expect(lines[0]!.text).toBe("I tried an IR sensor");
  });

  it("never lets a late interim update overwrite a final line", () => {
    let lines = upsertLine([], line("student:1", "final words", true));
    lines = upsertLine(lines, line("student:1", "stale interim"));
    expect(lines[0]).toMatchObject({ text: "final words", final: true });
  });

  it("ignores empty updates and never mutates its input", () => {
    const original = [line("student:1", "x")];
    const frozen = Object.freeze([...original]);
    expect(upsertLine(frozen, line("student:2", "   "))).toEqual(original);
    expect(frozen).toHaveLength(1);
  });

  it("keeps Tamil text exactly as received", () => {
    const text = "  சென்சார் ரீடிங்,  so I  switched ";
    expect(upsertLine([], line("student:1", text))[0]!.text).toBe(text);
  });
});
