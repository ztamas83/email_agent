import html
import json
import math
import os
import re
import typing

from dotenv import load_dotenv
from typesafe_sdk import Choice, Noul, Score, TypeSafeClient

from email_classifier import EmailClassifier
from schemas import ClassificationRule, EmailAction, HeaderClassification, RulesFile


class JevEmailClassifier(EmailClassifier):
    """
    Two-stage privacy-first email classifier using TypesafeAI's JEV classification.
    Step 1: Classifies sender/subject/date without looking at the email body.
    Step 2: If a category rule includes a custom prompt, passes the body to the LLM.
            Otherwise, withholds the body and applies deterministic actions.
    """

    def __init__(
        self,
        llm: typing.Optional[TypeSafeClient] = None,
        model: typing.Optional[str] = None,
        rules_file: typing.Optional[str] = None,
        mailbox_user: typing.Optional[str] = None,
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
        rules_path: str | None = None,
    ) -> dict[str, ClassificationRule]:
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
                raw_data = inline_json
            except Exception as e:
                print(f"[!] Warning: Failed to parse CATEGORY_RULES_JSON: {e}")

        # 2. Check JSON rules file
        if raw_data is None and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw_data = f.read()
            except Exception as e:
                print(f"[!] Warning: Failed to read rules file '{path}': {e}")

        if raw_data is None:
            return {}

        # Normalize into dict: {category_name: {"category": ..., "prompt": ...}}

        result = RulesFile.model_validate_json(raw_data)
        print("Rule reading result: ", result)

        return {rule.category: rule for rule in result.root}

    def load_category_rules(
        self, rules_path: typing.Optional[str] = None
    ) -> typing.Dict[str, ClassificationRule]:
        path = rules_path or self._rules_file
        return self.load_category_rules_from_source(path)

    def format_required_outputs_for_prompt(self, rule: typing.Dict[str, typing.Any]) -> str:
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
        self, action: EmailAction, rule: typing.Dict[str, typing.Any]
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
    def extract_email_text(msg: typing.Any) -> str:
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

    def classify_email(self, msg: typing.Any, rules_path: typing.Optional[str] = None) -> EmailAction:
        rules = self.load_category_rules(rules_path)

        print(f"Rules: {rules}")

        # If no rules are configured, skip LLM triage entirely to save tokens and protect privacy
        if not rules:
            print(
                "[!] No classification rules configured in rules.json. Skipping LLM triage."
            )
            return EmailAction(
                category="other",
                urgency="undefined",
                should_forward=False,
                mark_as_read=False,
                reasoning="No classification rules configured; email skipped without LLM processing.",
            )

        try:
            # Step 1: Metadata-only classification (Sender + Subject) WITHOUT email body
            state = {"incoming_email": {"subject": msg.subject, "sender": msg.from_}}
            category_question = {
                "category": Choice(
                    instructions="Which category does this incoming email belong to?",
                    criteria={
                        category: rules.get(category).category_criteria
                        for category in rules
                    },
                ),
            }

            print(f"[JEV input] state: {state}, question: {category_question}")

            response = self._llm.system_one(state, questions={**category_question})
            step1_response = response.answers

            category_response = response.answers["category"]

            print(
                f"  [Step 1] Header classification: category='{category_response.choice}'"
            )

            if category_response.confidence < 0.7:
                return EmailAction(
                    category=category_response.choice,
                    urgency="undefined",
                    should_forward=False,
                    mark_as_read=False,
                    reasoning="Category confidence is too low, no action",
                )

            selected_category = category_response.choice
            selected_rule = rules.get(selected_category)

            # Step 2: If category has a custom rule with a prompt, forward body to Step 2
            rule_prompt = selected_rule.prompt.strip()

            if "{user}" in rule_prompt:
                rule_prompt = rule_prompt.format(user=self._mailbox_user)

            print(
                f"  [Step 2] Category '{selected_category}' has prompt in rules. Forwarding body to LLM with category-specific prompt ONLY..."
            )
            raw_body = self.extract_email_text(msg)
            clean_body = raw_body[:3000].strip()

            state = {
                "incoming_email": {
                    "mailbox_user": self._mailbox_user,
                    "category": selected_category,
                    "sender": msg.from_,
                    "subject": msg.subject,
                    "date": msg.date_str,
                    "body": clean_body,
                },
                "rule": rule_prompt,
            }
            questions = {
                "should_forward": Noul(
                    instructions="Should the `incoming_email` be forwarded to another e-mail address? Decide based on the `rule`",
                    criteria={
                        "true": "Should be forwarded",
                        "false": "No action to be taken",
                    },
                ),
                "mark_as_read": Noul(
                    instructions="Should the incoming email `incoming_email` marked as read? Decide based on the `rule`",
                    criteria={
                        "true": "The email has to be marked as read",
                        "false": "The email should be left in the state it is",
                    },
                ),
                "requires_followup": Noul(
                    instructions="Should the `incoming_email` be followed up with any action other then forwarding or mark as read? Decide based on the `rule`",
                    criteria={
                        "true": "Should be followed up",
                        "false": "No action necessary",
                    },
                ),
                "urgency": Score(
                    instructions="What is the urgency of this email",
                    criteria=[
                        "low, No action required at all",
                        "medium, Action advised but it is not imminnent",
                        "high, action is requested or deemed as necessary within 3 days",
                    ],
                ),
            }

            print(f"[JEV request] stage 2 request {state}, {questions}")

            response = self._llm.system_one(state=state, questions={**questions})

            print(f"[JEV response] stage 2 response {response}")
            step2_response = response.answers
            reasoning = (
                f"Step 1 JEV response: {step1_response}\n"
                f"Step 2 JEV response: {step2_response}"
            )

            urgency_map = {0: "low", 1: "medium", 2: "high"}
            urgency_score = response.answers["urgency"].score
            urgency_frac, urgency_int = math.modf(urgency_score)

            urgency_level = round(urgency_score) if (urgency_frac > 0.65) else min(urgency_int+1, 2)
            
            urgency = urgency_map.get(urgency_level)

            print(
                f"urgency score: {urgency_score} determined level: {urgency_level} {urgency}"
            )

            # if response.answers["action"].noul > 0.8:
            #     return self.enforce_rule_outputs(
            #         EmailAction(
            #             category=category,
            #             urgency=urgency,
            #             should_forward=False,
            #             reasoning=reasoning,
            #         ),
            #         rule,
            #     )

            return EmailAction(
                category=selected_category,
                urgency=urgency,
                should_forward=response.answers["should_forward"].noul > 0.7,
                #forward_to=rules.get[selected_category].forward_to,
                apply_folder=None,
                mark_as_read=response.answers["mark_as_read"].noul > 0.7,
                reasoning=reasoning,
            )
        except Exception as e:
            print("Error in JEV handling", e)
            raise
