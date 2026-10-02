"""Shared builders for the gateway and internal-endpoint tests (isolated schema, fake Proof)."""

import json
import os
from uuid import UUID

import httpx

from app import repo
from app.crypto import TokenVault
from app.proof import ProofClient

PROOF_URL = "https://proof.example.com/api/mcp"
LOG_URL = "https://proof.example.com/u/ana/logs/77"
TOKEN = "tok-GATEWAY-SECRET-1234567890"

FULL = {
    "q1": "I tried an IR sensor today",
    "q2": "it kept giving noisy readings",
    "q3": "because it was cheaper",
    "followup_q": "You said cheaper. Why does cost matter here?",
    "followup_a": "ultrasonic was too slow for me",
}


class FakeProof:
    """A mock Proof server. `handler` may be replaced to simulate any failure."""

    def __init__(self, db=None) -> None:
        self.db = db
        self.requests: list[httpx.Request] = []
        self.db_held: list[int] = []
        self.handler = self.ok

    @staticmethod
    def ok(request: httpx.Request) -> httpx.Response:
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": LOG_URL}]},
        }
        return httpx.Response(200, json=body)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.db_held.append(self.db.active if self.db is not None else 0)
        out = self.handler(request)
        if isinstance(out, Exception):
            raise out
        return out

    @property
    def posts(self) -> list[dict]:
        """The `post_log` arguments of every post attempt received."""
        sent = [json.loads(r.content) for r in self.requests]
        return [
            m["params"]["arguments"] for m in sent if m.get("params", {}).get("name") == "post_log"
        ]

    def client(self) -> ProofClient:
        return ProofClient(PROOF_URL, http=httpx.Client(transport=httpx.MockTransport(self)))


def make_vault() -> TokenVault:
    return TokenVault({"v1": b"c" * 32}, "v1")


def make_user_with_token(conn, vault: TokenVault, token: str = TOKEN) -> UUID:
    from pydantic import SecretStr

    user = repo.create_user(conn)
    enc = vault.encrypt(SecretStr(token), user.id)
    repo.upsert_proof_credential(
        conn,
        user.id,
        ciphertext=enc.ciphertext,
        nonce=enc.nonce,
        key_id=enc.key_id,
        last4=token[-4:],
    )
    return user.id


def make_interview(conn, user_id: UUID, state: str = "confirming", answers: dict | None = None):
    """Insert an interview directly in `state` with `answers` as its draft."""
    iv = repo.create_interview(conn, user_id, f"syl-test-{os.urandom(6).hex()}")
    conn.execute(
        "update speakyourlog.interview_sessions set state = %s, draft = %s::jsonb where id = %s",
        (state, json.dumps({"answers": FULL if answers is None else answers}), iv.id),
    )
    return repo.get_interview(conn, iv.id)


def state_of(conn, interview_id: UUID) -> str:
    return repo.get_interview(conn, interview_id).state
