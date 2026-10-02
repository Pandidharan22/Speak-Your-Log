// The live conversation as a list of lines. LiveKit sends each spoken segment as a stream of text
// updates (interim, then final) that share a segment id; a new update REPLACES the earlier one so
// the page shows one growing line per utterance, never duplicates.

export interface Line {
  id: string; // "<who>:<segment id>"
  who: "student" | "agent";
  text: string;
  final: boolean;
}

export function upsertLine(lines: readonly Line[], incoming: Line): Line[] {
  if (!incoming.text.trim()) return [...lines]; // empty updates carry nothing
  const at = lines.findIndex((l) => l.id === incoming.id);
  if (at === -1) return [...lines, incoming];
  const existing = lines[at];
  // A final line is never overwritten by a late interim update for the same segment.
  if (existing?.final && !incoming.final) return [...lines];
  const next = [...lines];
  next[at] = incoming;
  return next;
}
