from abc import ABC, abstractmethod
from typing import Optional, Any

from schemas import EmailAction


class EmailClassifier(ABC):
    """Abstract base class defining the contract for email classifiers."""

    @abstractmethod
    def classify_email(self, msg: Any, rules_path: Optional[str] = None) -> EmailAction:
        """Classifies e-mail according to the provided rules."""
        pass
