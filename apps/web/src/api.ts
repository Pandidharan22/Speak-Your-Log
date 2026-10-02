// Thin client for the FastAPI backend. Same origin, so the browser attaches the (HttpOnly) session
// cookie and the correct Origin header by itself. NOTHING secret is stored here: the Proof token is
// sent once by connectToken() and never kept, returned, logged or written to any storage.

export interface Status {
  connected: boolean;
  last4: string | null; // the only fragment of the Proof token that ever comes back
}

export interface InterviewStart {
  interview_id: string;
  livekit_url: string;
  token: string; // a LiveKit room token: this room only, microphone only, 10 minutes
}

export type InterviewState =
  | "created"
  | "interviewing"
  | "confirming"
  | "posting"
  | "posted"
  | "post_unknown"
  | "failed"
  | "cancelled";

export interface InterviewStatus {
  state: InterviewState;
  preview: { content: string; why: string } | null;
  proof_url: string | null;
}

export const TERMINAL_STATES: ReadonlySet<InterviewState> = new Set([
  "posted",
  "post_unknown",
  "failed",
  "cancelled",
]);

/** An API error reduced to its stable machine code (e.g. "token_rejected"); never a raw message. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super(code);
    this.name = "ApiError";
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method,
      credentials: "same-origin",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "network_error"); // offline, or the server is waking up
  }
  if (!response.ok) {
    let code = "unknown";
    try {
      const data: unknown = await response.json();
      if (data && typeof data === "object" && "detail" in data && typeof data.detail === "string") {
        code = data.detail;
      }
    } catch {
      /* a non-JSON error body (e.g. a proxy page): keep "unknown" */
    }
    throw new ApiError(response.status, code);
  }
  return (await response.json()) as T;
}

export const api = {
  /** Recognise this device, or silently create it. Idempotent; call on every page load. */
  session: () => request<Status>("POST", "/api/session"),
  connectToken: (token: string) => request<Status>("PUT", "/api/proof-token", { token }),
  disconnect: () => request<Status>("DELETE", "/api/proof-token"),
  startInterview: () => request<InterviewStart>("POST", "/api/interviews"),
  interview: (id: string) => request<InterviewStatus>("GET", `/api/interviews/${encodeURIComponent(id)}`),
};
