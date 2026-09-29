import os
import re
import html
import json
from typing import Set, Dict, Any, Optional, List
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from email_classifier import EmailClassifier
from schemas import EmailAction, HeaderClassification


class GeminiEmailClassifier(EmailClassifier):
    """
    Two-stage privacy-first email classifier using Google Gemini LLM.
    Step 1: Classifies sender/subject/date without looking at the email body.
    Step 2: If a category rule includes a custom prompt, passes the body to the LLM.
            Otherwise, withholds the body and applies deterministic actions.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        rules_file: Optional[str] = None,
        mailbox_user: Optional[str] = None,
        llm: Optional[Any] = None,
    ):
        super().__init__()
        # Try loading from .env, but do NOT override environment variables already present
        load_dotenv(override=False)

        self._mailbox_user = (
            mailbox_user
            or os.getenv("MAILBOX_OWNER")
            or os.getenv("PROTON_USER")
            or "the mailbox owner"
        )
        self._rules_file = rules_file or os.getenv("RULES_FILE", "rules.json")
        self._model = (
            model
            or os.getenv("LLM_MODEL")
            or os.getenv("GEMINI_MODEL")
            or "gemini-3.5-flash-lite"
        )

        _api_key = (
            api_key
            or os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_API_KEY")
        )

        if llm is not None:
            self._llm = llm
        else:
            if not _api_key:
                raise ValueError(
                    "GEMINI_API_KEY or GOOGLE_API_KEY is required. "
                    "Please provide it in the system environment or a .env file."
                )
            self._llm = ChatGoogleGenerativeAI(
                model=self._model, temperature=0.0, api_key=_api_key
            )

    @property
    def mailbox_user(self) -> str:
        return self._mailbox_user

    @property
    def rules_file(self) -> str:
        return self._rules_file

    @property
    def model(self) -> str:
        return self._model

    @staticmethod
    def load_category_rules_from_source(
        rules_path: Optional[str] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Load custom JSON-based category rules from file or environment variable.
        Supports list format: [{"category": "travel", "prompt": "..."}, ...]
        or dictionary format: {"travel": {"prompt": "..."}, ...} or {"travel": "prompt..."}.
        Returns empty dict {} if no rules are configured.
        """
        path = rules_path or os.getenv("RULES_FILE", "rules.json")
        raw_data = None

        # 1. Check inline JSON in environment variable
        inline_json = os.getenv("CATEGORY_RULES_JSON")
        if inline_json and inline_json.strip():
            try:
                raw_data = json.loads(inline_json)
            except Exception as e:
                print(f"[!] Warning: Failed to parse CATEGORY_RULES_JSON: {e}")

        # 2. Check JSON rules file
        if raw_data is None and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw_data = json.load(f)
            except Exception as e:
                print(f"[!] Warning: Failed to read rules file '{path}': {e}")

        if raw_data is None:
            return {}

        # Normalize into dict: {category_name: {"category": ..., "prompt": ...}}
        normalized: Dict[str, Dict[str, Any]] = {}
        if isinstance(raw_data, list):
            for item in raw_data:
                if isinstance(item, dict) and "category" in item:
                    cat = str(item["category"]).strip().lower()
                    prompt = item.get("prompt", "")
                    normalized[cat] = {"category": cat, "prompt": prompt, **item}
        elif isinstance(raw_data, dict):
            if "rules" in raw_data and isinstance(raw_data["rules"], list):
                for item in raw_data["rules"]:
                    if isinstance(item, dict) and "category" in item:
                        cat = str(item["category"]).strip().lower()
                        prompt = item.get("prompt", "")
                        normalized[cat] = {"category": cat, "prompt": prompt, **item}
            else:
                for cat, val in raw_data.items():
                    cat_lower = str(cat).strip().lower()
                    if isinstance(val, dict):
                        prompt = val.get("prompt", "")
                        normalized[cat_lower] = {
                            "category": cat_lower,
                            "prompt": prompt,
                            **val,
                        }
                    elif isinstance(val, str):
                        normalized[cat_lower] = {"category": cat_lower, "prompt": val}

        return normalized

    def load_category_rules(
        self, rules_path: Optional[str] = None
    ) -> Dict[str, Dict[str, Any]]:
        path = rules_path or self._rules_file
        return self.load_category_rules_from_source(path)

    def build_step1_prompt(
        self,
        user: str,
        sender: str,
        subject: str,
        date: str,
        custom_categories: List[str],
    ) -> str:
        base_lines = [
            "- travel: Bookings, reservations, flight/train/hotel tickets, itineraries.",
            "- finance: Invoices, monthly bills, bank alerts, payment receipts.",
            "- newsletter: Promotional newsletters, marketing digests, bulletins.",
            "- personal: Personal correspondence, social invitations, direct human conversations.",
            "- spam: Unsolicited junk, scams, phishing.",
            "- other: General messages not matching the above.",
        ]
        known = {"travel", "finance", "newsletter", "personal", "spam", "other"}
        extra_lines = [
            f"- {cat}: Custom category defined in rules."
            for cat in custom_categories
            if cat not in known
        ]
        category_listing = "\n".join(base_lines + extra_lines)

        return f"""You are a privacy-first email triage assistant for {user}.
    Analyze ONLY the sender, subject line, and date of this incoming email to determine its category.
    Do NOT guess or fabricate private details.

    Sender: {sender}
    Subject: {subject}
    Date: {date}

    Classify into one of these categories:
    {category_listing}
    """

    def format_required_outputs_for_prompt(self, rule: Dict[str, Any]) -> str:
        """Format explicit required output constraints from the JSON rule for the LLM prompt."""
        constraints = []
        if "apply_folder" in rule and rule["apply_folder"] is not None:
            constraints.append(
                f"- apply_folder: MUST be set to '{rule['apply_folder']}'"
            )
        if "should_forward" in rule and rule["should_forward"] is not None:
            val_str = "true" if rule["should_forward"] else "false"
            constraints.append(f"- should_forward: MUST be set to {val_str}")
        if "mark_as_read" in rule and rule["mark_as_read"] is not None:
            val_str = "true" if rule["mark_as_read"] else "false"
            constraints.append(f"- mark_as_read: MUST be set to {val_str}")
        if "forward_to" in rule and rule["forward_to"]:
            constraints.append(f"- forward_to: MUST be set to '{rule['forward_to']}'")
        if "urgency" in rule and rule["urgency"]:
            constraints.append(f"- urgency: MUST be set to '{rule['urgency']}'")

        if not constraints:
            return ""

        return "Explicit Required Outputs for this category:\n" + "\n".join(constraints)

    def enforce_rule_outputs(
        self, action: EmailAction, rule: Dict[str, Any]
    ) -> EmailAction:
        """Enforce explicit rule outputs onto the EmailAction result."""
        if "apply_folder" in rule and rule["apply_folder"] is not None:
            action.apply_folder = rule["apply_folder"]
        if "should_forward" in rule and rule["should_forward"] is not None:
            action.should_forward = bool(rule["should_forward"])
        if "mark_as_read" in rule and rule["mark_as_read"] is not None:
            action.mark_as_read = bool(rule["mark_as_read"])
        if "forward_to" in rule and rule["forward_to"]:
            action.forward_to = str(rule["forward_to"])
        if "urgency" in rule and rule["urgency"]:
            action.urgency = rule["urgency"]
        return action

    @staticmethod
    def extract_email_text(msg: Any) -> str:
        """Extract clean plain text from email, supporting plain text, multipart, or HTML-only."""
        if hasattr(msg, "text") and msg.text and msg.text.strip():
            return msg.text.strip()
        if hasattr(msg, "html") and msg.html and msg.html.strip():
            # Fallback for HTML-only emails: strip markup and decode entities
            cleaned = re.sub(
                r"<(script|style)[^>]*>.*?</\1>",
                "",
                msg.html,
                flags=re.DOTALL | re.IGNORECASE,
            )
            cleaned = re.sub(
                r"<(br|p|div|tr|li|h[1-6])[^>]*>", "\n", cleaned, flags=re.IGNORECASE
            )
            cleaned = re.sub(r"<[^>]+>", " ", cleaned)
            cleaned = html.unescape(cleaned)
            cleaned = re.sub(r"[ \t]+", " ", cleaned)
            cleaned = re.sub(r"\n\s*\n+", "\n\n", cleaned)
            return cleaned.strip()
        return ""

    def header_classifier(self, **kwargs) -> HeaderClassification:
        prompt: str = kwargs["prompt"]
        return self._llm.with_structured_output(HeaderClassification).invoke(prompt)

    def structured_classifier(self, **kwargs) -> EmailAction:
        prompt: str = kwargs["prompt"]
        return self._llm.with_structured_output(EmailAction).invoke(prompt)

    def classify_email(self, msg: Any, rules_path: Optional[str] = None) -> EmailAction:
        rules = self.load_category_rules(rules_path)

        # If no rules are configured, skip LLM triage entirely to save tokens and protect privacy
        if not rules:
            print(
                "[!] No classification rules configured in rules.json. Skipping LLM triage."
            )
            return EmailAction(
                category="other",
                urgency="low",
                should_forward=False,
                apply_folder=None,
                mark_as_read=False,
                reasoning="No classification rules configured; email skipped without LLM processing.",
            )

        # Step 1: Metadata-only classification (Sender + Subject) WITHOUT email body
        step1_prompt = self.build_step1_prompt(
            user=self._mailbox_user,
            sender=msg.from_,
            subject=msg.subject,
            date=msg.date_str,
            custom_categories=list(rules.keys()),
        )
        header_decision = self.header_classifier(prompt=step1_prompt)
        category = header_decision.category.strip().lower()

        print(
            f"  [Step 1] Header classification: category='{category}', urgency='{header_decision.urgency}'"
        )

        # Step 2: If category has a custom rule with a prompt, forward body to Step 2
        rule = rules.get(category)
        rule_prompt = rule.get("prompt", "").strip() if rule else ""

        if rule and rule_prompt:
            if "{user}" in rule_prompt:
                rule_prompt = rule_prompt.format(user=self._mailbox_user)

            required_outputs_text = self.format_required_outputs_for_prompt(rule)
            constraints_block = (
                f"\n{required_outputs_text}\n" if required_outputs_text else ""
            )

            print(
                f"  [Step 2] Category '{category}' has prompt in rules. Forwarding body to LLM with category-specific prompt ONLY..."
            )
            raw_body = self.extract_email_text(msg)
            clean_body = raw_body[:3000].strip()

            step2_prompt = f"""You are a mail triage assistant for {self._mailbox_user}.
    The incoming email was pre-classified as '{category}' based on its sender and subject.

    Target Rule for '{category}':
    <category_rule>
    {rule_prompt}
    </category_rule>
    {constraints_block}
    Sender: {msg.from_}
    Subject: {msg.subject}
    Date: {msg.date_str}
    Body:
    <email_body>
    {clean_body}
    </email_body>

    Apply the rule and adhere strictly to any explicit required outputs above when outputting structured actions.
    """
            action = self.structured_classifier(prompt=step2_prompt)
            return self.enforce_rule_outputs(action, rule)

        # Category is NOT configured with a prompt: body is withheld from LLM
        print(
            f"  [Privacy Guard] No Step 2 prompt configured for category '{category}'. Body withheld from LLM. Applying deterministic action."
        )
        privacy_reason = (
            f"{header_decision.reasoning} [Privacy: body withheld from LLM]"
        )

        rule = rule or {}

        # Defaults by category if not explicitly specified in rule
        default_folder = None
        default_read = False
        if category == "newsletter":
            default_folder = "Newsletters"
            default_read = True
        elif category == "finance":
            default_folder = "Finance"
        elif category == "spam":
            default_folder = "Spam"
            default_read = True

        folder = rule.get("apply_folder", default_folder)
        should_fwd = rule.get("should_forward", False)
        mark_read = rule.get("mark_as_read", default_read)
        fwd_to = rule.get("forward_to", None)
        urg = rule.get("urgency", header_decision.urgency)

        return EmailAction(
            category=category,
            urgency=urg,
            should_forward=should_fwd,
            forward_to=fwd_to,
            apply_folder=folder,
            mark_as_read=mark_read,
            reasoning=privacy_reason,
        )


# Backward-compatible module-level functions and helpers
extract_email_text = GeminiEmailClassifier.extract_email_text
load_category_rules = GeminiEmailClassifier.load_category_rules_from_source


def classify_email(msg: Any, rules_path: Optional[str] = None) -> EmailAction:
    """Convenience module function delegating to a default GeminiEmailClassifier instance."""
    classifier = GeminiEmailClassifier()
    return classifier.classify_email(msg, rules_path=rules_path)
