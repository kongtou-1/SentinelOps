import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import app as chat
import httpx2
from fastapi.testclient import TestClient


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        key = root / "key"
        key.write_text("test-secret", encoding="utf-8")
        for name, value in (
            ("KEY_FILE", key),
            ("RECORDS_FILE", root / "records.jsonl"),
            ("DATABASE_FILE", root / "chat_test.sqlite3"),
        ):
            mocked = patch.object(chat, name, value)
            mocked.start()
            self.addCleanup(mocked.stop)
        self.client = self.enterContext(TestClient(chat.app))

    def provider(self, answer):
        return Mock(
            status_code=200, json=Mock(return_value={"choices": [{"message": {"content": answer}}]})
        )

    def test_single_turn_requests_and_persistent_inspection_feed(self):
        with patch.object(
            chat.httpx2,
            "post",
            side_effect=[self.provider("第一个回答"), self.provider("第二个回答")],
        ) as provider:
            first = self.client.post("/ask", json={"question": "第一个问题"})
            second = self.client.post("/ask", json={"question": "第二个问题"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertIs(first.json()["checked"], False)
        wire = provider.call_args.kwargs["json"]
        self.assertEqual(wire["messages"], [{"role": "user", "content": "第二个问题"}])
        self.assertIs(wire["stream"], False)
        self.assertNotIn("test-secret", first.text)
        # A separate client reads records from disk, including the full answer.
        with TestClient(chat.app) as reader:
            page = reader.get("/records", params={"limit": 1}).json()
            self.assertEqual(page["items"][0]["facts"]["answer"], "第一个回答")
            tail = reader.get("/records", params={"cursor": page["next_cursor"]}).json()
            self.assertEqual(tail["items"][0]["id"], second.json()["id"])
            self.assertIsNone(tail["next_cursor"])
            start = datetime.fromisoformat(first.json()["updated_at"])
            params = {
                "updated_after": start.isoformat(),
                "updated_before": (start + timedelta(days=1)).isoformat(),
            }
            self.assertEqual(len(reader.get("/records", params=params).json()["items"]), 2)
            params["updated_before"] = second.json()["updated_at"]
            self.assertEqual(len(reader.get("/records", params=params).json()["items"]), 1)

    def test_invalid_inputs_never_call_provider(self):
        with patch.object(chat.httpx2, "post") as provider:
            for question in ("", "  ", "x" * 2001):
                self.assertEqual(
                    self.client.post("/ask", json={"question": question}).status_code, 422
                )
            provider.assert_not_called()
        for params in ({"limit": 51}, {"cursor": -1}, {"updated_after": "2026-10-30"}):
            self.assertEqual(self.client.get("/records", params=params).status_code, 422)

    def test_provider_errors_are_safe_and_do_not_create_records(self):
        cases = [
            (Mock(status_code=401, text="test-secret"), 502),
            (self.provider(""), 502),
            (Mock(status_code=200, json=Mock(return_value={})), 502),
            (httpx2.ReadTimeout("test-secret"), 504),
            (httpx2.ConnectError("test-secret"), 502),
        ]
        for result, status in cases:
            with self.subTest(status=status), patch.object(chat.httpx2, "post") as provider:
                if isinstance(result, Exception):
                    provider.side_effect = result
                else:
                    provider.return_value = result
                response = self.client.post("/ask", json={"question": "测试"})
                self.assertEqual(response.status_code, status)
                self.assertNotIn("test-secret", response.text)
        self.assertEqual(self.client.get("/records").json()["items"], [])

    def create_qa(self, question="测试问题", answer="测试回答"):
        with patch.object(chat.httpx2, "post", return_value=self.provider(answer)):
            response = self.client.post("/ask", json={"question": question})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_mark_single_and_batch_with_individual_results_and_query_filters(self):
        first = self.create_qa("容器健康检查是什么", "检查应用是否健康")
        second = self.create_qa("第二个问题", "第二个回答")
        third = self.create_qa("第三个问题", "第三个回答")
        self.assertEqual(
            len(self.client.get("/records", params={"checked": False}).json()["items"]), 3
        )
        result = {"status": "pass", "summary": "切题且准确", "details": "回答解释了健康检查目的"}
        with patch.object(chat.httpx2, "post") as provider:
            response = self.client.post(
                "/records/checks", json={"records": [{"id": first["id"], "result": result}]}
            )
            provider.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["updated"], 1)
        saved = response.json()["items"][0]
        self.assertIs(saved["checked"], True)
        self.assertEqual(saved["check_result"], result)
        self.assertEqual(saved["created_at"], first["created_at"])
        self.assertEqual(saved["updated_at"], saved["checked_at"])
        self.assertEqual(saved["question"], first["question"])
        self.assertEqual(saved["answer"], first["answer"])
        pending = self.client.get("/records", params={"checked": False}).json()["items"]
        self.assertEqual({row["id"] for row in pending}, {second["id"], third["id"]})
        for params in (
            {"checked": True},
            {"record_id": first["id"]},
            {"q": "健康"},
            {"check_status": "pass", "checked": True},
        ):
            with self.subTest(params=params):
                rows = self.client.get("/records", params=params).json()["items"]
                self.assertEqual([row["id"] for row in rows], [first["id"]])
        response = self.client.post(
            "/records/checks",
            json={
                "records": [
                    {"id": second["id"], "result": {"status": "fail", "summary": "答非所问"}},
                    {"id": third["id"], "result": {"status": "unknown", "summary": "缺少事实依据"}},
                ]
            },
        )
        self.assertEqual(response.json()["updated"], 2)
        self.assertEqual(self.client.get("/records", params={"checked": False}).json()["items"], [])
        # Reopening the application must preserve marks and the latest result.
        with TestClient(chat.app) as reader:
            rows = reader.get("/records", params={"check_status": "unknown"}).json()["items"]
            self.assertEqual(rows[0]["id"], third["id"])
            self.assertIs(rows[0]["facts"]["checked"], True)

    def test_invalid_batch_never_partially_marks_records(self):
        first = self.create_qa()
        good = {"id": first["id"], "result": {"status": "pass", "summary": "通过"}}
        cases = [
            ({"records": []}, 422),
            ({"records": [good, good]}, 422),
            ({"records": [good, {"id": "missing", "result": good["result"]}]}, 404),
            ({"records": [{"id": first["id"], "result": {"status": "pass", "summary": " "}}]}, 422),
            (
                {"records": [{"id": first["id"], "result": {"status": "bad", "summary": "错误"}}]},
                422,
            ),
            ({"records": [good] * 101}, 422),
        ]
        for body, status in cases:
            with self.subTest(body=body):
                self.assertEqual(self.client.post("/records/checks", json=body).status_code, status)
                self.assertEqual(
                    len(self.client.get("/records", params={"checked": False}).json()["items"]), 1
                )

    def test_upgrade_existing_database_defaults_to_unchecked_and_keeps_results(self):
        first = self.create_qa()
        # Simulate the original schema, which did not have any inspection columns.
        with chat.database() as db:
            for column in ("checked", "checked_at", "check_result"):
                db.execute(f"ALTER TABLE conversations DROP COLUMN {column}")
        chat.initialize_database()
        row = self.client.get("/records", params={"record_id": first["id"]}).json()["items"][0][
            "facts"
        ]
        self.assertIs(row["checked"], False)
        self.assertIsNone(row["checked_at"])
        self.assertIsNone(row["check_result"])
        result = {"status": "pass", "summary": "检查通过"}
        self.client.post(
            "/records/checks", json={"records": [{"id": first["id"], "result": result}]}
        )
        chat.initialize_database()
        rows = self.client.get("/records", params={"checked": True}).json()["items"]
        self.assertEqual(rows[0]["facts"]["check_result"]["summary"], "检查通过")

    def test_legacy_migration_is_idempotent_and_preserves_qa_and_times(self):
        record = {
            "id": "legacy-1",
            "question": "旧问题",
            "answer": "旧回答",
            "model": chat.MODEL,
            "created_at": "2026-10-30T20:00:00+08:00",
            "updated_at": "2026-10-30T20:00:00+08:00",
            "deleted_at": None,
        }
        chat.RECORDS_FILE.write_text(json.dumps(record) + "\n", encoding="utf-8")
        chat.initialize_database()
        chat.initialize_database()
        with chat.database() as db:
            rows = [
                tuple(row)
                for row in db.execute("SELECT question, answer, created_at FROM conversations")
            ]
        self.assertEqual(rows, [("旧问题", "旧回答", "2026-10-30T12:00:00.000000+00:00")])
        # Filter boundaries are normalized to UTC before comparing stored timestamps.
        response = self.client.get(
            "/records",
            params={
                "updated_after": "2026-10-30T19:59:59+08:00",
                "updated_before": "2026-10-30T20:00:01+08:00",
            },
        )
        self.assertEqual(len(response.json()["items"]), 1)
        with chat.database() as db:
            db.execute("UPDATE conversations SET deleted_at=updated_at WHERE id=?", (record["id"],))
        self.assertEqual(self.client.get("/records").json()["items"], [])


if __name__ == "__main__":
    unittest.main()
