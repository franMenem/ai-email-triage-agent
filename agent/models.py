from typing import Literal

from pydantic import BaseModel, ConfigDict


class Email(BaseModel):
    message_id: str
    sender: str
    subject: str
    body: str
    thread: str = (
        ""  # earlier messages of the same thread, oldest first ("" if it's the first)
    )


# What Claude must answer. drafter.py builds the JSON schema for Claude's structured
# output from this class, so the allowed values live only here. (A comment, not a
# docstring: Pydantic would send a docstring to Claude as the schema's description.)
class Draft(BaseModel):
    # No extra fields: also makes the schema say "additionalProperties": false
    # and every field required, also the one with a default: Claude must always decide
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra=lambda schema: schema.update(
            required=list(schema["properties"])
        ),
    )

    # Decided first, before any text is written: False for spam, advertising and
    # automated messages. Defaults to True because replying is the safe side: a
    # customer left without a draft costs more than a draft nobody needed.
    needs_reply: bool = True
    draft: str  # the reply, ready to send ("" from Claude when needs_reply is False)
    internal_notes: str  # what Claude wants the reviewer to know
    intent: Literal[
        "order_status", "return_request", "complaint", "general_inquiry", "other"
    ]
    urgency: Literal["high", "medium", "low"]
