import typing

from pydantic import BaseModel, Field, RootModel


class HeaderClassification(BaseModel):
    category: str = Field(
        description="The primary classification category inferred solely from the email sender and subject (e.g. travel, finance, newsletter, personal, spam, other, or custom rule category)."
    )
    reasoning: str = Field(
        description="A concise 1-sentence audit rationale explaining why this category was chosen from metadata."
    )


class EmailAction(BaseModel):
    category: str = Field(
        description="The primary classification category for the email."
    )
    urgency: typing.Literal["low", "medium", "high", "undefined"] = Field(
        description="Urgency level based on deadlines, time-sensitivity, or required response."
    )
    should_forward: bool = Field(
        description="True if the email matches forwarding criteria (e.g., travel bookings, tickets, itineraries)."
    )
    forward_to: typing.Optional[str] = Field(
        default=None,
        description="Destination email address if forwarding. If null, fallback to system default.",
    )
    apply_folder: typing.Optional[str] = Field(
        default=None,
        description="Proton folder or label name to move/apply (e.g., 'Travel', 'Newsletters', 'Archive').",
    )
    mark_as_read: bool = Field(
        default=True,
        description="Whether to mark the message as read after processing.",
    )
    reasoning: typing.Optional[str] = Field(
        description="A concise 1-sentence audit rationale explaining why these actions were chosen."
    )


class ClassificationRule(BaseModel):
    category: str = Field()
    category_criteria: str = Field()
    prompt: str = Field(
        description="Rule prompt for classification that describes what action should be taken"
    )
    forward_to: typing.Optional[str] = Field(
        default=None,
        description="Destination email address if forwarding. If null an error may be thrown",
    )
    apply_folder: typing.Optional[str] = Field(
        default=None,
        description="Proton folder or label name to move/apply (e.g., 'Travel', 'Newsletters', 'Archive').",
    )


# {
#     "category": "travel",
#     "category_criteria": "Tickets, reservations, itineraries, booking confirmations, boarding passes",
#     "prompt": "If the email contains tickets, reservations, itineraries, or boarding passes and {user} is explicitly mentioned on the travelers list in the email body, confirm the itinerary and set should_forward=True. If traveler is uncertain or not {user}, do not forward.",
#     "apply_folder": "Travel",
#     "should_forward": true,
#     "forward_to": "sample@email.com",
#     "mark_as_read": false
#   },


class RulesFile(RootModel):
    root: list[ClassificationRule]
