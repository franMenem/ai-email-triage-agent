from typing import Literal

from pydantic import BaseModel


class Email(BaseModel):
    message_id: str
    sender: str
    subject: str
    body: str


class Draft(BaseModel):
    draft: str  # the reply, ready to send
    internal_notes: str  # what Claude wants the reviewer to know
    intent: Literal["order_status", "return_request", "complaint", "general_inquiry", "other"]
    urgency: Literal["high", "medium", "low"]
