"""Spike 01: validate the Gemini key, list usable models, and read Proof's tool list.

Prints no secrets. Run: .venv/Scripts/python.exe spike/01_smoke.py
"""
import json
import os

import httpx
from dotenv import load_dotenv
from google import genai

load_dotenv()

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
names = sorted(m.name.removeprefix("models/") for m in client.models.list())
print(f"[gemini] key OK, {len(names)} models visible")
for kw in ("flash", "tts", "live", "transcribe", "native-audio"):
    hits = [n for n in names if kw in n]
    print(f"  {kw:13s}: {', '.join(hits) if hits else '-'}")

resp = httpx.post(
    os.environ["PROOF_MCP_URL"],
    headers={"Authorization": f"Bearer {os.environ['PROOF_SPIKE_TOKEN']}"},
    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    timeout=20,
)
print(f"\n[proof] tools/list HTTP {resp.status_code}")
body = resp.json()
with open("spike/out/proof_tools_list.json", "w", encoding="utf-8") as f:
    json.dump(body, f, indent=2, ensure_ascii=False)
for t in body.get("result", {}).get("tools", []):
    print(f"  - {t['name']}: {t.get('description', '')[:160]}")
print("  (full schema saved to spike/out/proof_tools_list.json)" if "result" in body else body)
