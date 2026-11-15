"""看板取数、设置与对局复核备注的测试。"""

from __future__ import annotations

import csv
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import User
from django.db import connection
from django.test import SimpleTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from accounts.models import ScoreRecord
from dashboard import views
from dashboard.models import RunNote


def stamp(offset_seconds: int = 0) -> str:
    return (datetime.now() + timedelta(seconds=offset_seconds)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )


def live_data(**overrides) -> dict:
    data = {
        "timestamp": stamp(),
        "client_count": "4",
        "tick_ms_p95": "1.2",
        "bytes_out": "7804",
        "snapshot_bytes": "1927",
        "cpu_percent": "2.5",
    }
    data.update(overrides)
    return data


class ParseLiveMetricsTest(SimpleTestCase):
    def test_fresh_hash_becomes_dashboard_dict(self):
        got = views._parse_live_metrics(live_data())
        self.assertEqual(got["online"], 4)
        self.assertEqual(got["tick_p95"], 1.2)
        self.assertEqual(got["snapshot_bytes"], 1927)
        self.assertAlmostEqual(got["bandwidth_kbps"], 7804 / 1024)
        self.assertEqual(got["cpu_percent"], 2.5)
        self.assertEqual(got["source"], "redis")
        self.assertTrue(got["live"])
        self.assertGreaterEqual(got["age"], 0)

    def test_stale_hash_is_ignored(self):
        # 超过 TTL 就不认：防止 key 被别的东西续命却写的是旧数据
        self.assertIsNone(
            views._parse_live_metrics(live_data(timestamp=stamp(-30)))
        )

    def test_missing_timestamp_is_ignored(self):
        data = live_data()
        del data["timestamp"]
        self.assertIsNone(views._parse_live_metrics(data))

    def test_empty_hash_is_ignored(self):
        self.assertIsNone(views._parse_live_metrics({}))

    def test_bad_numbers_are_ignored(self):
        self.assertIsNone(
            views._parse_live_metrics(live_data(client_count="not-a-number"))
        )


class ReadLiveMetricsTest(SimpleTestCase):
    def test_redis_error_returns_none(self):
        with mock.patch.object(
            views, "_redis_client", side_effect=ConnectionError("redis down")
        ):
            self.assertIsNone(views._read_live_metrics())

    def test_empty_redis_returns_none(self):
        client = mock.Mock()
        client.hgetall.return_value = {}
        with mock.patch.object(views, "_redis_client", return_value=client):
            self.assertIsNone(views._read_live_metrics())

    def test_redis_hash_is_used(self):
        client = mock.Mock()
        client.hgetall.return_value = live_data()
        with mock.patch.object(views, "_redis_client", return_value=client):
            got = views._read_live_metrics()
        self.assertEqual(got["source"], "redis")
        client.hgetall.assert_called_once_with(settings.LIVE_METRICS_KEY)


class LatestMetricsTest(SimpleTestCase):
    def test_prefers_redis(self):
        with mock.patch.object(
            views, "_read_live_metrics", return_value={"online": 7, "source": "redis"}
        ):
            self.assertEqual(views._latest_metrics()["online"], 7)
            self.assertEqual(views._latest_metrics()["source"], "redis")

    def test_falls_back_to_csv(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "server_metrics.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "timestamp",
                    "client_count",
                    "tick_ms_p95",
                    "bytes_out",
                    "snapshot_bytes",
                    "cpu_percent",
                ]
            )
            writer.writerow([stamp(), "2", "3.4", "2048", "512", "1.5"])

        with override_settings(METRICS_CSV=path), mock.patch.object(
            views, "_read_live_metrics", return_value=None
        ):
            got = views._latest_metrics()

        self.assertEqual(got["source"], "csv")
        self.assertEqual(got["online"], 2)
        self.assertEqual(got["snapshot_bytes"], 512)
        self.assertTrue(got["live"])

    def test_nothing_available_returns_none(self):
        with mock.patch.object(views, "_read_live_metrics", return_value=None), \
             override_settings(METRICS_CSV=Path("Z:/no/such/file.csv")):
            self.assertIsNone(views._latest_metrics())


class SettingsTest(SimpleTestCase):
    def test_metrics_csv_defaults_to_workspace_data(self):
        # 没设 NGDCS_METRICS_CSV 时，默认读本工作区 data/
        self.assertEqual(
            settings.METRICS_CSV,
            settings.BASE_DIR / "data" / "server_metrics.csv",
        )

    def test_live_metrics_key_and_ttl(self):
        self.assertEqual(settings.LIVE_METRICS_KEY, "ngdcs:metrics:live")
        self.assertEqual(settings.LIVE_METRICS_TTL, 10)


class RunNoteApiTest(APITestCase):
    """对局复核备注：权限、校验、对象级删除与 N+1。"""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user("admin", password="x", is_staff=True)
        cls.admin2 = User.objects.create_user("admin2", password="x", is_staff=True)
        cls.alice = User.objects.create_user("alice", password="x")
        cls.run_record = ScoreRecord.objects.create(
            user=cls.alice, score=100, level=1, kills=10
        )
        cls.other_run = ScoreRecord.objects.create(
            user=cls.alice, score=200, level=2, kills=20
        )

    def notes_url(self, run=None):
        return f"/api/v2/runs/{(run or self.run_record).id}/notes/"

    def note_url(self, note, run=None):
        return f"{self.notes_url(run)}{note.id}/"

    def test_anonymous_denied(self):
        resp = self.client.get(self.notes_url())
        self.assertIn(resp.status_code, (401, 403))

    def test_non_staff_denied(self):
        self.client.force_authenticate(self.alice)
        self.assertIn(self.client.get(self.notes_url()).status_code, (401, 403))
        self.assertIn(
            self.client.post(
                self.notes_url(), {"content": "x"}, format="json"
            ).status_code,
            (401, 403),
        )

    def test_admin_lists_notes_of_one_run(self):
        RunNote.objects.create(run=self.run_record, author=self.admin, content="a")
        RunNote.objects.create(run=self.run_record, author=self.admin2, content="b")
        RunNote.objects.create(run=self.other_run, author=self.admin, content="c")

        self.client.force_authenticate(self.admin)
        resp = self.client.get(self.notes_url())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([row["content"] for row in resp.data], ["b", "a"])

    def test_create_note(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(
            self.notes_url(), {"content": "  已复核  "}, format="json"
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["content"], "已复核")
        self.assertEqual(resp.data["author"], "admin")
        note = RunNote.objects.get(id=resp.data["id"])
        self.assertEqual(note.author, self.admin)
        self.assertEqual(note.run, self.run_record)

    def test_create_ignores_client_author(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(
            self.notes_url(),
            {"content": "x", "author": "admin2"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["author"], "admin")

    def test_create_rejects_blank(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(
            self.notes_url(), {"content": "   "}, format="json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_create_rejects_too_long(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(
            self.notes_url(), {"content": "x" * 201}, format="json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_create_404_when_run_missing(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(
            "/api/v2/runs/999999/notes/", {"content": "x"}, format="json"
        )
        self.assertEqual(resp.status_code, 404)

    def test_delete_own_note(self):
        note = RunNote.objects.create(run=self.run_record, author=self.admin, content="x")
        self.client.force_authenticate(self.admin)
        resp = self.client.delete(self.note_url(note))
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(RunNote.objects.filter(id=note.id).exists())

    def test_delete_others_note_forbidden(self):
        note = RunNote.objects.create(run=self.run_record, author=self.admin2, content="x")
        self.client.force_authenticate(self.admin)
        resp = self.client.delete(self.note_url(note))
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(RunNote.objects.filter(id=note.id).exists())

    def test_delete_404_when_note_not_in_run(self):
        note = RunNote.objects.create(
            run=self.other_run, author=self.admin, content="x"
        )
        self.client.force_authenticate(self.admin)
        resp = self.client.delete(self.note_url(note, run=self.run_record))
        self.assertEqual(resp.status_code, 404)

    def test_list_query_count_is_constant(self):
        for i in range(5):
            RunNote.objects.create(
                run=self.run_record, author=self.admin, content=f"note-{i}"
            )
        self.client.force_authenticate(self.admin)
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get(self.notes_url())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 5)
        # 1 次查 run + 1 次查备注（select_related 把 author 带出来）
        self.assertLessEqual(len(ctx.captured_queries), 3)
        self.assertFalse(
            any(
                "auth_user" in query["sql"] and "JOIN" not in query["sql"]
                for query in ctx.captured_queries
            )
        )
