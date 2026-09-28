import os
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from schemas import EmailAction

# Try loading from .env, but do NOT override environment variables already present
load_dotenv(override=False)

mailbox_user = os.getenv("MAILBOX_OWNER") or os.getenv("PROTON_USER") or "the mailbox owner"
api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
if not api_key:
    raise ValueError(
        "GEMINI_API_KEY or GOOGLE_API_KEY is required. "
        "Please provide it in the system environment or a .env file."
    )

LLM_MODEL = os.getenv("LLM_MODEL") or os.getenv("GEMINI_MODEL") or "gemini-3.5-flash-lite"

llm = ChatGoogleGenerativeAI(
    model=LLM_MODEL,
    temperature=0.0,
    api_key=api_key
)
structured_classifier = llm.with_structured_output(EmailAction)

SYSTEM_PROMPT_TEMPLATE = """You are a mail triage assistant for {user}. Analyze this incoming email and output structured actions.

Sender: {sender}
Subject: {subject}
Date: {date}
Body:
<email_body>
{body}
</email_body>

Business Rules:
1. Travel: If the email contains tickets, reservations, itineraries, boarding passes, or travel receipts (flights, trains, hotels, rental cars) and the {user} is explicitly on travelers list, i.e. mentioned in the email body then set category='travel', should_forward=True, apply_folder='Travel'.
If the traveler cannot be safely determined do not forward the message, should_forward=False.
2. Newsletters/Marketing: If it is a promotional newsletter, marketing blast, or digest, set category='newsletter', apply_folder='Newsletters', mark_as_read=True.
3. Finance: If it contains monthly bills, invoices, bank alerts, set category='finance', should_forward=False, apply_folder='Finance'.
4. Default: If no rule triggers, set category='other', should_forward=False, apply_folder=None, mark_as_read=False.
"""

def classify_email(msg) -> EmailAction:
    clean_body = (msg.text or "")[:3000].strip()
    prompt = SYSTEM_PROMPT_TEMPLATE.format(
        user=mailbox_user,
        sender=msg.from_,
        subject=msg.subject,
        date=msg.date_str,
        body=clean_body
    )
    return structured_classifier.invoke(prompt)
