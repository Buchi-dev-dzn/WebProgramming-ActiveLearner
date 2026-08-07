import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from app.main import require_csrf, validate_origin


class SecurityControlTests(unittest.TestCase):
    def request(self, origin=None):
        headers = {"origin": origin} if origin else {}
        return SimpleNamespace(headers=headers)

    def test_rejects_untrusted_origin(self):
        with patch("app.main.CORS_ALLOWED_ORIGINS", ["https://trusted.example"]):
            with self.assertRaises(HTTPException) as context:
                validate_origin(self.request("https://evil.example"))
        self.assertEqual(context.exception.status_code, 403)

    def test_requires_matching_double_submit_csrf_token(self):
        with patch("app.main.CORS_ALLOWED_ORIGINS", []):
            with self.assertRaises(HTTPException) as context:
                require_csrf(self.request(), "cookie-token", "wrong-token")
        self.assertEqual(context.exception.detail["error"], "csrf_failed")

    def test_accepts_matching_double_submit_csrf_token(self):
        with patch("app.main.CORS_ALLOWED_ORIGINS", []):
            require_csrf(self.request(), "cookie-token", "cookie-token")


if __name__ == "__main__":
    unittest.main()
