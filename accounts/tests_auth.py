"""Bearer token 认证的测试：401 / 403 / 200。"""

from __future__ import annotations

import secrets

from django.contrib.auth.models import User
from rest_framework.test import APITestCase

from accounts.models import AuthToken, ScoreRecord

RUNS_URL = "/api/v2/runs/"


class AuthTokenAuthenticationTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user("admin", password="x", is_staff=True)
        cls.alice = User.objects.create_user("alice", password="x")
        cls.inactive = User.objects.create_user(
            "inactive", password="x", is_staff=True, is_active=False
        )
        cls.run_record = ScoreRecord.objects.create(
            user=cls.alice, score=100, level=1, kills=10
        )

    def token_for(self, user: User) -> str:
        return AuthToken.objects.create(
            user=user, token=secrets.token_hex(16)
        ).token

    def test_no_credentials_returns_401(self):
        resp = self.client.get(RUNS_URL)
        self.assertEqual(resp.status_code, 401)
        self.assertIn("Bearer", resp["WWW-Authenticate"])

    def test_invalid_token_returns_401(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")
        self.assertEqual(self.client.get(RUNS_URL).status_code, 401)

    def test_malformed_header_returns_401(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer")
        self.assertEqual(self.client.get(RUNS_URL).status_code, 401)

    def test_other_scheme_returns_401(self):
        self.client.credentials(HTTP_AUTHORIZATION="Basic abc")
        self.assertEqual(self.client.get(RUNS_URL).status_code, 401)

    def test_valid_staff_token_returns_200(self):
        token = self.token_for(self.admin)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(self.client.get(RUNS_URL).status_code, 200)

    def test_valid_token_but_not_staff_returns_403(self):
        token = self.token_for(self.alice)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(self.client.get(RUNS_URL).status_code, 403)

    def test_inactive_user_token_returns_401(self):
        token = self.token_for(self.inactive)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(self.client.get(RUNS_URL).status_code, 401)

    def test_session_login_still_works(self):
        self.assertTrue(self.client.login(username="admin", password="x"))
        self.assertEqual(self.client.get(RUNS_URL).status_code, 200)

    def test_token_can_create_note(self):
        token = self.token_for(self.admin)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        resp = self.client.post(
            f"/api/v2/runs/{self.run_record.id}/notes/",
            {"content": "token 写入"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["author"], "admin")
