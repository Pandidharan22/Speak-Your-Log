"""Spike 03: Gemini Live API (streaming speech-to-speech) on the synthetic clips.

Measures, per model and clip:
  * input_transcription  -> what the Live API thinks the student said (vs ground truth CER)
  * latency              -> end of student speech -> first audio byte of the reply
  * output_transcription -> what the model replied (shows whether it understood the language)
"""
import asyncio
import audioop
import os
import re
import sys
import time
import wave
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv()
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

MODELS = os.environ.get("LIVE_MODELS", "gemini-3.1-flash-live-preview,gemini-3.8-live,gemini-2.5-flash-native-audio-preview-12-2025").split(",")
CLIPS = {
    "ta2": "சென்சார் ரீடிங் சரியாவே வரல, அதனால முழுசா மறுபடி வயரிங் பண்ண வேண்டியதா போச்சு.",
    "mx2": "ஆனா readings ரொம்ப noisy ஆ இருந்துச்சு, அதனால IR sensor க்கு switch பண்ணேன்.",
    "en2": "The readings were really noisy, so I switched to an IR sensor.",
}
SYSTEM = (
    "You are a friendly interviewer for a student. Reply with exactly ONE short sentence: a follow-up "
    "question that quotes a specific thing they just said. Answer in the language the student used."
)


def norm(s):
    s = re.sub(r"[^\w\s]", "", s.casefold())
    return re.sub(r"\s+", " ", s).strip()


def cer(ref, hyp):
    a, b = norm(ref), norm(hyp)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return round(prev[-1] / max(len(a), 1), 3)


def load_pcm16k(clip):
    with wave.open(f"spike/out/synth/{clip}.wav", "rb") as w:
        pcm = w.readframes(w.getnframes())
        rate = w.getframerate()
    pcm16k, _ = audioop.ratecv(pcm, 2, 1, rate, 16000, None)
    return pcm16k


async def run_clip(model, clip, truth):
    pcm = load_pcm16k(clip) + b"\x00" * 16000 * 2  # +1s trailing silence so VAD ends the turn
    cfg = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=SYSTEM,
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )
    in_txt, out_txt, t_first, t_sent = [], [], None, None
    async with client.aio.live.connect(model=model, config=cfg) as session:
        chunk = 3200  # 100 ms of 16 kHz mono s16
        speech_bytes = len(pcm) - 32000
        for i in range(0, len(pcm), chunk):
            await session.send_realtime_input(audio=types.Blob(data=pcm[i:i + chunk], mime_type="audio/pcm;rate=16000"))
            if i + chunk >= speech_bytes and t_sent is None:
                t_sent = time.time()  # moment the last speech chunk went out
            await asyncio.sleep(0.05)  # ~2x realtime; keeps the test short
        async for msg in session.receive():
            sc = msg.server_content
            if not sc:
                continue
            if sc.input_transcription and sc.input_transcription.text:
                in_txt.append(sc.input_transcription.text)
            if sc.output_transcription and sc.output_transcription.text:
                out_txt.append(sc.output_transcription.text)
            if sc.model_turn and t_first is None:
                for p in sc.model_turn.parts or []:
                    if p.inline_data:
                        t_first = time.time()
                        break
            if sc.turn_complete:
                break
    heard = "".join(in_txt).strip()
    return {
        "heard": heard,
        "cer": cer(truth, heard) if heard else None,
        "latency_s": round(t_first - t_sent, 2) if t_first and t_sent else None,
        "reply": "".join(out_txt).strip(),
    }


async def main():
    for model in MODELS:
        print(f"\n######## {model}")
        for clip, truth in CLIPS.items():
            try:
                r = await asyncio.wait_for(run_clip(model, clip, truth), timeout=60)
                print(f"[{clip}] CER={r['cer']}  first-audio-latency={r['latency_s']}s")
                print(f"   truth: {truth}\n   heard: {r['heard']}\n   reply: {r['reply']}")
            except Exception as e:  # noqa: BLE001
                print(f"[{clip}] ERROR {type(e).__name__}: {str(e)[:220]}")
                if "1008" in str(e) or "not found" in str(e).lower() or "not supported" in str(e).lower():
                    break  # model unusable for this config; skip remaining clips
            await asyncio.sleep(3)


asyncio.run(main())
