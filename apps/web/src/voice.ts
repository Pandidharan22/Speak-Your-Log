// The browser's side of the voice call: join the LiveKit room with the token the API issued, send
// the microphone, play the interviewer's audio, and surface transcripts. Wrapped behind a tiny
// interface so every screen can be tested with a fake instead of a real microphone.

import { Room, RoomEvent, Track } from "livekit-client";
import type { Line } from "./transcript";

export interface VoiceInfo {
  url: string; // the LiveKit server's wss:// address
  token: string; // a room token: this room only, microphone only
}

export interface VoiceHandlers {
  onLine(line: Line): void;
  onAgentSpeaking(speaking: boolean): void;
  onAgentJoined(): void;
  onDisconnected(): void;
  onError(code: "mic_denied" | "voice_failed"): void;
}

export interface VoiceConnection {
  disconnect(): void;
}

export type VoiceFactory = (info: VoiceInfo, handlers: VoiceHandlers) => Promise<VoiceConnection>;

export const connectLiveKitVoice: VoiceFactory = async (info, handlers) => {
  const room = new Room();
  const players = new Set<HTMLElement>();

  room.on(RoomEvent.TrackSubscribed, (track) => {
    if (track.kind !== Track.Kind.Audio) return;
    const element = track.attach(); // an <audio> element playing the interviewer's voice
    element.style.display = "none";
    document.body.appendChild(element);
    players.add(element);
  });
  room.on(RoomEvent.TrackUnsubscribed, (track) => {
    track.detach().forEach((element) => {
      element.remove();
      players.delete(element);
    });
  });
  room.on(RoomEvent.ParticipantConnected, () => handlers.onAgentJoined());
  room.on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
    handlers.onAgentSpeaking(speakers.some((s) => s.identity !== room.localParticipant.identity));
  });
  room.on(RoomEvent.Disconnected, () => handlers.onDisconnected());

  room.registerTextStreamHandler("lk.transcription", async (reader, participant) => {
    const text = await reader.readAll();
    const attributes = reader.info.attributes ?? {};
    const segment = attributes["lk.segment_id"] ?? reader.info.id;
    const who = participant.identity === room.localParticipant.identity ? "student" : "agent";
    handlers.onLine({
      id: `${who}:${segment}`,
      who,
      text,
      final: attributes["lk.transcription_final"] === "true",
    });
  });

  try {
    await room.connect(info.url, info.token);
  } catch {
    handlers.onError("voice_failed");
    throw new Error("voice_failed");
  }
  if (room.remoteParticipants.size > 0) handlers.onAgentJoined();
  try {
    await room.localParticipant.setMicrophoneEnabled(true); // asks for microphone permission
  } catch {
    room.disconnect();
    handlers.onError("mic_denied");
    throw new Error("mic_denied");
  }
  return {
    disconnect: () => {
      room.disconnect();
      players.forEach((element) => element.remove());
      players.clear();
    },
  };
};
