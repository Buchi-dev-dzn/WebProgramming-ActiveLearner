import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.main import UserRegister, require_role, roles_for_legacy_role


class AccountRoleTests(unittest.IsolatedAsyncioTestCase):
    def test_register_does_not_accept_role(self):
        account = UserRegister(email="user@example.test", password="password-123")
        self.assertEqual(account.email, "user@example.test")
        with self.assertRaises(ValidationError):
            UserRegister(
                email="attacker@example.test",
                password="password-123",
                role="admin",
            )

    def test_legacy_customer_and_seller_are_unified_accounts(self):
        self.assertEqual(roles_for_legacy_role("customer"), ["buyer", "seller"])
        self.assertEqual(roles_for_legacy_role("seller"), ["buyer", "seller"])
        self.assertEqual(roles_for_legacy_role("member"), ["buyer", "seller"])

    def test_admin_remains_separate(self):
        self.assertEqual(roles_for_legacy_role("admin"), ["admin"])

    async def test_backend_rejects_authenticated_user_without_permission(self):
        with patch(
            "app.main.current_user_record",
            AsyncMock(return_value={"id": 10, "role": "support"}),
        ):
            with self.assertRaises(HTTPException) as context:
                await require_role("Bearer valid-token", {"seller", "admin"})
        self.assertEqual(context.exception.status_code, 403)

    async def test_legacy_accounts_pass_seller_authorization(self):
        for role in ("customer", "seller"):
            with self.subTest(role=role), patch(
                "app.main.current_user_record",
                AsyncMock(return_value={"id": 10, "role": role}),
            ):
                user = await require_role("Bearer legacy-token", {"seller", "admin"})
                self.assertEqual(user["role"], role)


if __name__ == "__main__":
    unittest.main()
