import type { Messages } from "../messages";

interface Props {
  m: Messages;
  preview: { content: string; why: string };
}

// The log exactly as it will be posted. The text is rendered as plain text (never as HTML), in the
// student's own words, together with the plain statement that it will be public.
export function ReadBack({ m, preview }: Props) {
  return (
    <section aria-labelledby="readback-title" className="readback">
      <h3 id="readback-title">{m.readBackTitle}</h3>
      <p>{m.readBackIntro}</p>
      <blockquote lang="ta-IN">
        <p data-testid="preview-content">{preview.content}</p>
        <p data-testid="preview-why">{preview.why}</p>
      </blockquote>
      <p className="warning" role="note">
        {m.publicWarning}
      </p>
      <p>{m.sayYes}</p>
    </section>
  );
}
