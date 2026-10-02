import { api } from "./api";
import { connectLiveKitVoice, type VoiceFactory } from "./voice";

// Everything the screens need from the outside world, in one injectable object. Production uses the
// real API and LiveKit; tests pass fakes (no network, no microphone).
export interface Deps {
  api: typeof api;
  voice: VoiceFactory;
  pollMs?: number; // how often to ask the server for progress (default 1500 ms)
}

export const realDeps: Deps = { api, voice: connectLiveKitVoice };
