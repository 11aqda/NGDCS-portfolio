"""对局列表的过滤与查询数测试。"""

from __future__ import annotations

from datetime import datetime

from django.contrib.auth.models import User
from django.test.utils import CaptureQueriesContext
from django.db import connection
from rest_framework.test import APITestCase

from accounts.models import ScoreRecord

RUNS_URL = "/api/v2/runs/"


class RunFilterTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user("admin", password="x", is_staff=True)
        cls.alice = User.objects.create_user("alice", password="x")
        cls.bob = User.objects.create_user("bob", password="x")

        cls.old = ScoreRecord.objects.create(
            user=cls.alice, score=100, level=1, kills=10
        )
        ScoreRecord.objects.filter(pk=cls.old.pk).update(
            created_at=datetime(2026, 10, 1, 12, 0, 0)
        )
        cls.mid = ScoreRecord.objects.create(
            user=cls.alice, score=400, level=2, kills=40
        )
        ScoreRecord.objects.filter(pk=cls.mid.pk).update(
            created_at=datetime(2026, 10, 20, 12, 0, 0)
        )
        cls.new = ScoreRecord.objects.create(
            user=cls.bob, score=900, level=3, kills=90
        )
        ScoreRecord.objects.filter(pk=cls.new.pk).update(
            created_at=datetime(2026, 11, 5, 12, 0, 0)
        )

    def setUp(self):
        self.client.force_authenticate(self.admin)

    def test_filter_by_level(self):
        resp = self.client.get(RUNS_URL, {"level": 2})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([row["score"] for row in resp.data["results"]], [400])

    def test_filter_by_score_range(self):
        resp = self.client.get(RUNS_URL, {"score_min": 300, "score_max": 800})
        self.assertEqual([row["score"] for row in resp.data["results"]], [400])

    def test_filter_by_created_date(self):
        resp = self.client.get(RUNS_URL, {"created_after": "2026-10-15"})
        self.assertEqual(
            sorted(row["score"] for row in resp.data["results"]), [400, 900]
        )

        resp = self.client.get(
            RUNS_URL, {"created_after": "2026-10-15", "created_before": "2026-10-31"}
        )
        self.assertEqual([row["score"] for row in resp.data["results"]], [400])

    def test_combined_filter_search_ordering(self):
        resp = self.client.get(
            RUNS_URL,
            {
                "level": 3,
                "search": "bob",
                "ordering": "-score",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(resp.data["results"][0]["username"], "bob")

    def test_invalid_filter_value_returns_400(self):
        resp = self.client.get(RUNS_URL, {"level": "abc"})
        self.assertEqual(resp.status_code, 400)


class RunListQueryCountTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user("admin", password="x", is_staff=True)
        cls.alice = User.objects.create_user("alice", password="x")
        for i in range(25):
            ScoreRecord.objects.create(
                user=cls.alice, score=100 + i, level=1, kills=i
            )

    def test_list_uses_constant_queries(self):
        self.client.force_authenticate(self.admin)
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get(RUNS_URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 25)
        # 1 条 count + 1 条列表（select_related user），过滤/排序不额外查库
        self.assertLessEqual(len(ctx.captured_queries), 3)
        self.assertFalse(
            any(
                "auth_user" in query["sql"] and "JOIN" not in query["sql"]
                for query in ctx.captured_queries
            )
        )
