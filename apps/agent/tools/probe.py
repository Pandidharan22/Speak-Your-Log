"""Live probe: behave like a student's browser against a RUNNING worker, and report what happened.

Needs real keys in the repo-root .env and a worker running (`python agent.py start`). It creates a
temporary room, dispatches the agent into it, joins as a "student", plays a pre-recorded WAV as the
student's microphone, records the agent's audio + transcripts, then deletes the room.

    python tools/probe.py [path/to/speech.wav | wait_SECONDS ...] [--listen SECONDS]
                          [--meta-file path.json]

Used as the end-to-end check for steps 3.3-3.8 (synthetic Tamil/English speech from the spike).
"""

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
import wave
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from livekit import api, rtc

load_dotenv(Path(__file__).resolve().parents[3] / ".env")
URL = os.environ["LIVEKIT_URL"]
KEY, SECRET = os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"]
AGENT_NAME = "syl-interviewer"
FRAME_MS = 10
SPEECH_RMS = 400  # int16 RMS above this counts as "someone is speaking"


class AgentAudio:
    """Tracks when the agent is audibly speaking."""

    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.segments: list[list[float]] = []  # [start, end] in seconds since the probe started
        self._last_loud: float | None = None

    def feed(self, samples: np.ndarray) -> None:
        now = time.monotonic() - self.t0
        loud = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2))) > SPEECH_RMS
        if loud:
            if self._last_loud is None or now - self._last_loud > 0.8:
                self.segments.append([now, now])
            self.segments[-1][1] = now
            self._last_loud = now

    def speaking_since(self) -> float | None:
        return self._last_loud


async def wait_for_quiet(audio: AgentAudio, quiet_for: float, timeout: float) -> bool:
    """Wait until the agent has said something and then been silent for `quiet_for` seconds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        last = audio.speaking_since()
        if last is not None and (time.monotonic() - audio.t0) - last >= quiet_for:
            return True
        await asyncio.sleep(0.1)
    return False


async def play_wav(source: rtc.AudioSource, path: Path, trailing_silence: float = 2.0) -> float:
    """Stream a mono 16-bit WAV as the microphone in real time. Returns when speech ENDED."""
    with wave.open(str(path), "rb") as w:
        rate, pcm = w.getframerate(), np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    step = rate * FRAME_MS // 1000
    for i in range(0, len(pcm), step):
        chunk = pcm[i : i + step]
        if len(chunk) < step:
            chunk = np.pad(chunk, (0, step - len(chunk)))
        await source.capture_frame(rtc.AudioFrame(chunk.tobytes(), rate, 1, step))
    await source.wait_for_playout()  # frames are queued: wait until they have really been sent
    ended = time.monotonic()
    silence = np.zeros(step, dtype=np.int16)
    for _ in range(int(trailing_silence * 1000 / FRAME_MS)):  # lets the model's VAD end the turn
        await source.capture_frame(rtc.AudioFrame(silence.tobytes(), rate, 1, step))
    return ended


async def main(wavs: list[Path], listen: float, meta: dict) -> int:
    room_name = f"syl-probe-{uuid.uuid4().hex[:8]}"
    http = URL.replace("wss://", "https://", 1)
    audio, transcripts = AgentAudio(), []
    async with api.LiveKitAPI(http, KEY, SECRET) as lk:
        await lk.room.create_room(api.CreateRoomRequest(name=room_name, empty_timeout=90))
        await lk.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=AGENT_NAME, room=room_name, metadata=json.dumps(meta)
            )
        )
        token = (
            api.AccessToken(KEY, SECRET)
            .with_identity("probe-student")
            .with_grants(api.VideoGrants(room_join=True, room=room_name, can_publish=True))
            .to_jwt()
        )
        room = rtc.Room()
        tasks: list[asyncio.Task] = []

        async def read_audio(track: rtc.Track) -> None:
            async for event in rtc.AudioStream(track):
                audio.feed(np.frombuffer(event.frame.data, dtype=np.int16))

        @room.on("track_subscribed")
        def _on_track(track, publication, participant):
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                tasks.append(asyncio.create_task(read_audio(track)))

        async def read_text(reader: rtc.TextStreamReader, identity: str) -> None:
            text = await reader.read_all()
            who = "student" if identity == "probe-student" else "agent"
            transcripts.append((round(time.monotonic() - audio.t0, 1), who, text))

        room.register_text_stream_handler(
            "lk.transcription",
            lambda reader, ident: tasks.append(asyncio.create_task(read_text(reader, ident))),
        )

        await room.connect(URL, token)
        source = rtc.AudioSource(24000, 1)
        mic = rtc.LocalAudioTrack.create_audio_track("mic", source)
        await room.local_participant.publish_track(
            mic, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        )
        print(f"joined {room_name}; waiting for the agent to speak...")

        t_join = time.monotonic()
        greeted = await wait_for_quiet(audio, quiet_for=1.5, timeout=listen)
        first = audio.segments[0][0] if audio.segments else None
        print(
            f"agent greeting: spoke={bool(audio.segments)} finished={greeted}"
            + (f" first_audio_after_join={first:.1f}s" if first is not None else "")
        )

        results = []
        for wav in wavs:
            if wav.name.startswith(
                "wait_"
            ):  # "wait_35": stay silent for 35 s (e.g. a long read-back)
                await asyncio.sleep(float(wav.name.removeprefix("wait_")))
                continue
            segs_before = len(audio.segments)
            ended = await play_wav(source, wav)
            t_end_rel = ended - audio.t0
            # First wait for the agent to START replying, then for it to finish.
            deadline = time.monotonic() + listen
            while time.monotonic() < deadline and not any(
                s[0] >= t_end_rel for s in audio.segments[segs_before:]
            ):
                await asyncio.sleep(0.05)
            await wait_for_quiet(audio, quiet_for=1.5, timeout=listen)
            reply = next((s for s in audio.segments[segs_before:] if s[0] >= t_end_rel), None)
            latency = None if reply is None else round(reply[0] - t_end_rel, 2)
            results.append(
                (wav.name, latency, None if reply is None else round(reply[1] - reply[0], 1))
            )
            # Wall-clock stamps (seconds mod 1000) so these can be lined up with worker-side logs.
            w0 = time.time() - (time.monotonic() - audio.t0)
            reply_wall = (w0 + reply[0]) % 1000 if reply else float("nan")
            print(
                f"WALL speech_end={(w0 + t_end_rel) % 1000:.2f} reply_audio_start={reply_wall:.2f}"
            )
            print(
                f"student said {wav.name}: reply_latency={latency}s reply_length={results[-1][2]}s"
            )

        await asyncio.sleep(2.0)  # let final transcripts arrive
        await room.disconnect()
        for t in tasks:
            t.cancel()
        await lk.room.delete_room(api.DeleteRoomRequest(room=room_name))

    print("\n--- transcripts (time, who, text) ---")
    for t, who, text in sorted(transcripts):
        print(f"{t:6.1f}s {who:8s} {text}")
    ok = bool(audio.segments)
    print(
        "\nRESULT:",
        "agent spoke" if ok else "agent never spoke",
        "| total",
        round(time.monotonic() - t_join, 1),
        "s",
    )
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("wavs", nargs="*", type=Path)
    ap.add_argument("--listen", type=float, default=30.0)
    ap.add_argument("--meta-file", type=Path)
    args = ap.parse_args()
    meta = (
        json.loads(args.meta_file.read_text(encoding="utf-8"))
        if args.meta_file
        else {
            "interview_id": str(uuid.uuid4()),
            "job_token": "probe-token",
            "api_base_url": "http://localhost:8000",
        }
    )
    code = asyncio.run(main(args.wavs, args.listen, meta))
    sys.stdout.flush()
    os._exit(code)  # skip interpreter-exit teardown: the audio FFI handles assert noisily there
