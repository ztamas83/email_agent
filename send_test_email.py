#!/usr/bin/env python3
import os
import argparse
import smtplib
from email.message import EmailMessage

try:
    from dotenv import load_dotenv
    load_dotenv(override=False)
except ImportError:
    pass

BRIDGE_HOST = os.getenv("BRIDGE_HOST", "127.0.0.1")
SMTP_PORT = int(os.getenv("SMTP_PORT") or 1025)
DEFAULT_TO = os.getenv("PROTON_USER", "agentuser@example.com")

SAMPLE_EMAILS = {
    "travel-generic": {
        "from": "reservations@skyhigh-airlines.com",
        "subject": "Flight Confirmation - Booking Reference #SKY7890",
        "body": (
            "Dear Passenger,\n\n"
            "Your flight booking is confirmed.\n"
            "Flight: SH-402\n"
            "From: JFK (New York) to LHR (London Heathrow)\n"
            "Date: 2026-10-15 08:30 AM\n"
            "Seat: 14A (Window)\n\n"
            "Your electronic ticket and boarding pass itinerary are attached."
        ),
    },
    "travel": {
        "from": "reservations@hotels.com",
        "subject": "Booking confirmation",
        "body": (
            """
Tack, Testsson! Din bokning är bekräftad.
--------------------------------------

Resplansnummer 544561654815

Se den fullständiga resplanen

Resenärsuppgifter
-----------------

Vuxna: 2

 

U Street Capsule Hostel
-----------------------

1931 13th St NW, Washington, DC, 20009 USA

Incheckning
-----------

Utcheckning
-----------

tors 11 maj

lör 13 maj

Incheckning från kl. 15.00

11.00

Gratis avbokning fram till 14.59 (lokaltid på boendet) 10 maj 2027.

Boendeuppgifter
---------------

Bokat för Test Testsson.

Du bokade 1 rum.

Superior enkelrum - sovsal (män och kvinnor) - icke-rökare - privat badrum

Prisinformation

Debiteras av boendet

2 nätter x 1 rum

185,84 US$

Extra gäst

20,00 US$

Skatter på avgifter

32,84 US$

Delsumma

238,68 US$

Totalt

238,68 US$

Att betala på boendet

238,68 US$ (2 348,40 kr)*

Om inget annat anges visas priserna i svenska kronor.

Du hittar rumsprisuppgifter i din resplan

Det angivna priset i SEK baseras på den aktuella växelkursen, som kan ändras före resetillfället. Slutbetalningen betalas i lokal valuta direkt till boendet.
"""

        ),
    },
    "finance": {
        "from": "billing@cloudservices.io",
        "subject": "Monthly Invoice #INV-2026-09-4821",
        "body": (
            "Hello,\n\n"
            "Your invoice for Cloud Services (September 2026) is now available.\n"
            "Amount Due: $142.50\n"
            "Due Date: October 10, 2026\n"
            "Payment Method: Auto-charge to Visa ending in 4242.\n\n"
            "Thank you for your business."
        ),
    },
    "newsletter": {
        "from": "weekly@techtriage-digest.org",
        "subject": "Tech Triage Digest #142: Emerging AI Agents",
        "body": (
            "Welcome to Issue #142 of the Weekly Tech Triage Digest!\n\n"
            "Top stories this week:\n"
            "- Autonomous LLM triage daemons in production\n"
            "- New developments in IMAP IDLE push listeners\n"
            "- Managing containers with rootless Podman\n\n"
            "Click here to unsubscribe or update your subscription preferences."
        ),
    },
    "other": {
        "from": "friend@example.com",
        "subject": "Coffee catchup this Wednesday?",
        "body": (
            "Hey,\n\n"
            "Are you free for lunch or a quick coffee this Wednesday around 1 PM?\n"
            "Let me know if that time works for you!\n\n"
            "Best,\nAlex"
        ),
    },
}

def send_email(from_addr: str, to_addr: str, subject: str, body: str, host: str, port: int):
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(host, port) as server:
        server.send_message(msg)
    print(f"[+] Delivered '{subject}' from <{from_addr}> to <{to_addr}> via {host}:{port}")

def main():
    parser = argparse.ArgumentParser(description="Send test emails to mock or live mail server.")
    parser.add_argument(
        "--category",
        choices=["travel","travel-generic", "finance", "newsletter", "other", "all"],
        default="travel",
        help="Email category template to send (default: travel)"
    )
    parser.add_argument("--to", default=DEFAULT_TO, help=f"Recipient email address (default: {DEFAULT_TO})")
    parser.add_argument("--host", default="127.0.0.1", help="SMTP server host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=SMTP_PORT, help=f"SMTP server port (default: {SMTP_PORT})")
    parser.add_argument("--subject", default=None, help="Custom email subject")
    parser.add_argument("--body", default=None, help="Custom email body")
    parser.add_argument("--from-addr", default=None, help="Custom sender address")

    args = parser.parse_args()

    if args.category == "all":
        for cat, data in SAMPLE_EMAILS.items():
            send_email(
                from_addr=args.from_addr or data["from"],
                to_addr=args.to,
                subject=args.subject or data["subject"],
                body=args.body or data["body"],
                host=args.host,
                port=args.port
            )
    else:
        data = SAMPLE_EMAILS[args.category]
        send_email(
            from_addr=args.from_addr or data["from"],
            to_addr=args.to,
            subject=args.subject or data["subject"],
            body=args.body or data["body"],
            host=args.host,
            port=args.port
        )

if __name__ == "__main__":
    main()
