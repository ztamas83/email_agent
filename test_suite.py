import unittest

from test_web import TestWebAPI
from test_db import TestDatabase
from test_daemon import TestDaemon
from test_email_classifier import TestEmailClassifier
from test_gemini_classifier import TestGeminiEmailClassifier
from test_jev_classifier import TestJevEmailClassifier

if __name__ == "__main__":
    unittest.main()
