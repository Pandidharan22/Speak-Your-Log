"""Spike 02: STT bake-off on synthetic Tamil / Tanglish / English speech.

1. Gemini TTS speaks 9 known sentences -> WAV (spike/out/synth/, git-ignored).
2. Three STT candidates transcribe each WAV.
3. Score = character error rate (CER) vs ground truth + latency.

Caveat: clean synthetic audio flatters STT. Treat results as a floor check
("does Tamil work at all?"), not a real-world accuracy number.
"""
import base64
import json
import os
import re
import time
import wave
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
OUT = Path("spike/out/synth")
OUT.mkdir(parents=True, exist_ok=True)

TTS_MODELS = ["gemini-3.8-flash-tts", "gemini-3.1-flash-tts-preview"]
STT_FLASH = ["gemini-3.8-flash", "gemini-3.1-flash-lite"]

CLIPS = [
    ("ta1", "ta", "இன்னைக்கு நான் ஒரு ரோபோவை லைன் ஃபாலோ பண்ண வைக்க முயற்சி பண்ணேன்."),
    ("ta2", "ta", "சென்சார் ரீடிங் சரியாவே வரல, அதனால முழுசா மறுபடி வயரிங் பண்ண வேண்டியதா போச்சு."),
    ("ta3", "ta", "அல்ட்ராசோனிக்கை விட இது வேகமா வேலை செய்யும்னு நினைச்சேன், அதனால இதை தேர்ந்தெடுத்தேன்."),
    ("mx1", "mixed", "இன்னைக்கு நான் ultrasonic sensor use பண்ணி obstacle detect பண்ண try பண்ணேன்."),
    ("mx2", "mixed", "ஆனா readings ரொம்ப noisy ஆ இருந்துச்சு, அதனால IR sensor க்கு switch பண்ணேன்."),
    ("mx3", "mixed", "Arduino ல code upload பண்ணும்போது port error வந்துச்சு, driver reinstall பண்ணதும் சரி ஆச்சு."),
    ("en1", "en", "Today I tried an ultrasonic sensor for obstacle detection on my line follower robot."),
    ("en2", "en", "The readings were really noisy, so I switched to an IR sensor."),
    ("en3", "en", "I chose it because it is cheaper and responds faster at short range."),
]

VERBATIM_PROMPT = (
    "Transcribe this audio exactly, word for word. Do not translate, summarise, correct "
    "grammar or remove filler words. Write Tamil words in Tamil script and English words "
    "in English (Latin) letters, exactly as spoken. Output only the transcript."
)


def retry(fn, tries=5):
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - spike script
            msg = str(e)
            if ("429" in msg or "RESOURCE_EXHAUSTED" in msg or "503" in msg) and i < tries - 1:
                wait = 20 * (i + 1)
                print(f"    rate-limited/unavailable, sleeping {wait}s")
                time.sleep(wait)
                continue
            raise


def synth(clip_id, text):
    path = OUT / f"{clip_id}.wav"
    if path.exists():
        return path, None, None
    last = None
    for model in TTS_MODELS:
        try:
            t0 = time.time()
            r = retry(lambda: client.models.generate_content(
                model=model,
                contents=text,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Kore")
                        )
                    ),
                ),
            ))
            pcm = r.candidates[0].content.parts[0].inline_data.data
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(24000)
                w.writeframes(pcm)
            return path, model, round(time.time() - t0, 2)
        except Exception as e:  # noqa: BLE001
            last = f"{model}: {type(e).__name__}: {str(e)[:200]}"
            print("    TTS failed ->", last)
    raise RuntimeError(last)


def norm(s):
    s = s.casefold()
    s = re.sub(r"[^\w\s]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def cer(ref, hyp):
    a, b = norm(ref), norm(hyp)
    if not a:
        return 0.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return round(prev[-1] / len(a), 3)


def stt_transcribe_model(path, mode):
    """gemini-3.5-transcribe via Interactions API (uploaded file, per the docs)."""
    f = client.files.upload(file=str(path))
    cfg = {}
    if mode == "verbatim":
        cfg = {"transcription_config": {"mode": {"type": "verbatim"}}}
    elif mode == "hint-ta":
        cfg = {"transcription_config": {"language_codes": ["ta-IN"]}}
    t0 = time.time()
    it = client.interactions.create(
        model="gemini-3.5-transcribe",
        input=[{"type": "audio", "uri": f.uri, "mime_type": f.mime_type}],
        **({"generation_config": cfg} if cfg else {}),
    )
    return it.output_text, round(time.time() - t0, 2)


def stt_flash(path, model):
    """General Gemini model doing transcription with a verbatim prompt (inline audio)."""
    audio = path.read_bytes()
    t0 = time.time()
    r = client.models.generate_content(
        model=model,
        contents=[VERBATIM_PROMPT, types.Part.from_bytes(data=audio, mime_type="audio/wav")],
        config=types.GenerateContentConfig(temperature=0),
    )
    return r.text, round(time.time() - t0, 2)


# gemini-3.8-flash was dropped after repeated 503/429 on the free tier; "verbatim" mode
# gave identical output to "default" on every Tamil clip.
CANDIDATES = {
    "transcribe/default": lambda p: stt_transcribe_model(p, "default"),
    "flash-lite/3.1": lambda p: stt_flash(p, "gemini-3.1-flash-lite"),
}
ONLY = set(filter(None, os.environ.get("ONLY", "").split(",")))

results = []
for clip_id, lang, truth in CLIPS:
    if ONLY and clip_id not in ONLY:
        continue
    print(f"\n== {clip_id} [{lang}]  truth: {truth}")
    try:
        path, tts_model, tts_s = synth(clip_id, truth)
        if tts_model:
            print(f"   TTS ok ({tts_model}, {tts_s}s)")
    except Exception as e:  # noqa: BLE001
        print("   TTS FAILED, skipping clip:", e)
        results.append({"clip": clip_id, "lang": lang, "error": f"tts: {e}"})
        continue
    for name, fn in CANDIDATES.items():
        try:
            text, secs = retry(lambda: fn(path))
            row = {"clip": clip_id, "lang": lang, "cand": name, "cer": cer(truth, text), "sec": secs, "text": text}
            print(f"   {name:20s} CER={row['cer']:<6} {secs:>5}s  -> {text.strip()[:110]}")
        except Exception as e:  # noqa: BLE001
            row = {"clip": clip_id, "lang": lang, "cand": name, "error": f"{type(e).__name__}: {str(e)[:300]}"}
            print(f"   {name:20s} ERROR {row['error'][:160]}")
        results.append(row)

Path(os.environ.get("RESULTS", "spike/out/stt_results.json")).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

print("\n\n===== SUMMARY: mean CER (lower=better) / mean latency by candidate and language =====")
for name in CANDIDATES:
    for lang in ("ta", "mixed", "en"):
        rows = [r for r in results if r.get("cand") == name and r["lang"] == lang and "cer" in r]
        errs = [r for r in results if r.get("cand") == name and r["lang"] == lang and "error" in r]
        if rows or errs:
            m = sum(r["cer"] for r in rows) / max(len(rows), 1)
            s = sum(r["sec"] for r in rows) / max(len(rows), 1)
            print(f"{name:20s} {lang:6s} CER={m:.3f}  lat={s:.2f}s  ok={len(rows)} err={len(errs)}")
