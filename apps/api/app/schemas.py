from pydantic import BaseModel


class SessionStatus(BaseModel):
    connected: bool  # has the user stored a Proof token?
    last4: str | None  # the ONLY fragment of the token that ever leaves the server
