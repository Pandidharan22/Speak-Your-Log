import { describe, expect, it } from "vitest";

// Static checks over the page's own source: the cheapest way to keep certain classes of mistake out.
const sources = import.meta.glob("./**/*.{ts,tsx}", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;
const app = Object.entries(sources).filter(
  ([path]) => !/\.test\.tsx?$/.test(path) && !path.includes("/test/"),
);

describe("source-level security rules", () => {
  it("scans the real source files", () => {
    expect(app.length).toBeGreaterThan(8);
  });

  it("never injects HTML", () => {
    for (const [path, text] of app) {
      expect(text, path).not.toMatch(
        /dangerouslySetInnerHTML|\.innerHTML\s*=|insertAdjacentHTML|document\.write/,
      );
    }
  });

  it("never evaluates strings as code", () => {
    for (const [path, text] of app) expect(text, path).not.toMatch(/\beval\(|new Function\(/);
  });

  it("uses sessionStorage and IndexedDB nowhere", () => {
    for (const [path, text] of app) expect(text, path).not.toMatch(/sessionStorage|indexedDB/);
  });

  it("uses localStorage only for the language preference", () => {
    const users = app.filter(([, text]) => /localStorage/.test(text));
    expect(users.map(([path]) => path)).toEqual(["./App.tsx"]);
    const text = users[0]![1];
    const calls = [...text.matchAll(/localStorage\.(?:get|set)Item\(([^)]*)\)/g)];
    expect(calls.length).toBeGreaterThan(0);
    expect(calls.every((call) => /LANG_KEY/.test(call[1]!))).toBe(true);
  });

  it("never logs to the console", () => {
    for (const [path, text] of app) {
      expect(text, path).not.toMatch(/console\.(log|debug|info|warn|error)/);
    }
  });

  it("never puts anything token-related in a URL or query string", () => {
    for (const [path, text] of app) {
      expect(text, path).not.toMatch(/\?token=|&token=|location\.(search|hash)/);
    }
  });

  it("opens every external link with noopener", () => {
    for (const [path, text] of app) {
      for (const tag of text.match(/<a\b[^>]*target="_blank"[\s\S]*?>/g) ?? []) {
        expect(tag, path).toMatch(/noopener/);
      }
    }
  });
});
