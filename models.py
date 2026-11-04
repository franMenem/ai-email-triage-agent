from typing import Literal

from pydantic import BaseModel, ConfigDict


class Email(BaseModel):
    message_id: str
    sender: str
    subject: str
    body: str
    thread: str = ""  # earlier messages of the same thread, oldest first ("" if it's the first)


# What Claude must answer. drafter.py builds the JSON schema for Claude's structured
# output from this class, so the allowed values live only here. (A comment, not a
# docstring: Pydantic would send a docstring to Claude as the schema's description.)
class Draft(BaseModel):
    # No extra fields: also makes the schema say "additionalProperties": false
    model_config = ConfigDict(extra="forbid")

    draft: str  # the reply, ready to send
    internal_notes: str  # what Claude wants the reviewer to know
    intent: Literal["order_status", "return_request", "complaint", "general_inquiry", "other"]
    urgency: Literal["high", "medium", "low"]
