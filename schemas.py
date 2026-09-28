from typing import Literal, Optional
from pydantic import BaseModel, Field

class EmailAction(BaseModel):
    category: Literal["travel", "finance", "newsletter", "personal", "spam", "other"] = Field(
        description="The primary classification category for the email."
    )
    urgency: Literal["low", "medium", "high"] = Field(
        description="Urgency level based on deadlines, time-sensitivity, or required response."
    )
    should_forward: bool = Field(
        description="True if the email matches forwarding criteria (e.g., travel bookings, tickets, itineraries)."
    )
    forward_to: Optional[str] = Field(
        default=None,
        description="Destination email address if forwarding. If null, fallback to system default."
    )
    apply_folder: Optional[str] = Field(
        default=None,
        description="Proton folder or label name to move/apply (e.g., 'Travel', 'Newsletters', 'Archive')."
    )
    mark_as_read: bool = Field(
        default=True,
        description="Whether to mark the message as read after processing."
    )
    reasoning: str = Field(
        description="A concise 1-sentence audit rationale explaining why these actions were chosen."
    )
