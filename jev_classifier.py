import math
import os
import re
import html
import json
from typing import  Dict, Any, Optional
from dotenv import load_dotenv
from email_classifier import EmailClassifier
from schemas import EmailAction, HeaderClassification
from typesafe_sdk import Choice, Noul, Score, TypeSafeClient

class JevEmailClassifier(EmailClassifier):
    """
    Two-stage privacy-first email classifier using TypesafeAI's JEV classification.
    Step 1: Classifies sender/subject/date without looking at the email body.
    Step 2: If a category rule includes a custom prompt, passes the body to the LLM.
            Otherwise, withholds the body and applies deterministic actions.
    """

    def __init__(
        self,
        llm: Optional[TypeSafeClient] = None,
        model: Optional[str] = None,
        rules_file: Optional[str] = None,
        mailbox_user: Optional[str] = None,
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

        _api_key = os.getenv("TYPESAFE_API_KEY")

        if llm is not None:
            self._llm = llm
        else:
            if not _api_key:
                raise ValueError(
                    "JEV_API_KEY is required. "
                    "Please provide it in the system environment or a .env file."
                )
            self._llm = TypeSafeClient()
            print("Initiated TypeSafe client")

        print("JEV classifier loaded")

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
                    category_criteria = (
                        str(item["category_criteria"] or "").strip().lower()
                    )
                    prompt = item.get("prompt", "")
                    normalized[cat] = {
                        "category": cat,
                        "prompt": prompt,
                        "category_criteria": category_criteria,
                        **item,
                    }
        elif isinstance(raw_data, dict):
            if "rules" in raw_data and isinstance(raw_data["rules"], list):
                for item in raw_data["rules"]:
                    if isinstance(item, dict) and "category" in item:
                        cat = str(item["category"]).strip().lower()
                        category_criteria = (
                            str(item["category_criteria"] or "").strip().lower()
                        )
                        prompt = item.get("prompt", "")
                        normalized[cat] = {
                            "category": cat,
                            "prompt": prompt,
                            "category_criteria": category_criteria,
                            **item,
                        }
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
        pass

    def structured_classifier(self, **kwargs) -> EmailAction:
        pass

    def classify_email(self, msg: Any, rules_path: Optional[str] = None) -> EmailAction:
        rules = self.load_category_rules(rules_path)

        print(f"JEV classifier running")

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
        try:
            state = {"incoming_email": {"subject": msg.subject, "sender": msg.from_}}
            category_question = {
                "category": Choice(
                    instructions="Which category does this incoming email belong to?",
                    criteria={r: rules.get(r).get("category_criteria") for r in rules},
                ),
            }

            print(f"[JEV input] state: {state}, question: {category_question}")

            response = self._llm.system_one(state, questions={**category_question})

            category = response.answers["category"].choice

            print(f"  [Step 1] Header classification: category='{category}'")

            # Step 2: If category has a custom rule with a prompt, forward body to Step 2
            rule = rules.get(category, {})
            rule_prompt = rule.get("prompt", "").strip()

            if rule and rule_prompt:
                if "{user}" in rule_prompt:
                    rule_prompt = rule_prompt.format(user=self._mailbox_user)

                print(
                    f"  [Step 2] Category '{category}' has prompt in rules. Forwarding body to LLM with category-specific prompt ONLY..."
                )
                raw_body = self.extract_email_text(msg)
                clean_body = raw_body[:3000].strip()

                state = {
                    "incoming_email": {
                        "mailbox_user": self._mailbox_user,
                        "category": category,
                        "sender": msg.from_,
                        "subject": msg.subject,
                        "date": msg.date_str,
                        "body": clean_body,
                    },
                    "rule": rule_prompt,
                }
                question = {
                    "action": Noul(
                        instructions=("The `rule` applies to the `incoming_email`")
                    ),
                    "urgency": Score(
                        instructions="How urgent it is to act on this email",
                        criteria=[
                            "low, No action required at all",
                            "medium, Action advised but it is not imminnent",
                            "high, action required within 3 days",
                        ],
                    ),
                }

                print(f"[JEV request] stage 2 request {state}, {question}")
                response = self._llm.system_one(state=state, questions={**question})
                print(f"[JEV response] stage 2 response {response}")

                urgency_map = {0: "low", 1: "medium", 2: "high"}
                urgency_score = response.answers["urgency"].score
                urgency_frac, urgency_int = math.modf(urgency_score)

                urgency_level = (
                    round(urgency_score) if (urgency_frac > 0.65) else urgency_int
                )
                urgency = urgency_map.get(urgency_level)

                print(
                    f"urgency score: {urgency_score} determined level: {urgency_level} {urgency}"
                )

                if response.answers["action"].noul > 0.8:
                    return self.enforce_rule_outputs(
                        EmailAction(
                            category=category,
                            urgency=urgency,
                            should_forward=False,
                            reasoning="empty",
                        ),
                        rule,
                    )

                return EmailAction(
                    category=category,
                    urgency="low",
                    should_forward=False,
                    forward_to=None,
                    apply_folder=None,
                    mark_as_read=False,
                    reasoning="no action reason",
                )

        except Exception as e:
            print(e)
            raise e

        # Category is NOT configured with a prompt: body is withheld from LLM
        print(
            f"  [Privacy Guard] No Step 2 prompt configured for category '{category}'. Body withheld from LLM. Applying deterministic action."
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

        return EmailAction(
            category=category,
            urgency="",
            should_forward=should_fwd,
            forward_to=fwd_to,
            apply_folder=folder,
            mark_as_read=mark_read,
            reasoning="privacy_reason",
        )
