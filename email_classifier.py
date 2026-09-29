from abc import ABC, abstractmethod
from typing import Optional, Any

from schemas import EmailAction, HeaderClassification

class EmailClassifier(ABC):
    """Abstract base class defining the contract for email classifiers."""

    @abstractmethod
    def classify_email(self, msg: Any, rules_path: Optional[str] = None) -> EmailAction:
        """Classifies e-mail according to the provided rules."""
        pass

    @abstractmethod
    def header_classifier(self, **kwargs) -> HeaderClassification:
        pass

    @abstractmethod
    def structured_classifier(self, **kwargs) -> EmailAction:
        pass
