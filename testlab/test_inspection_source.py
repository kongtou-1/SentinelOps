import unittest
from datetime import UTC, datetime, timedelta

from inspection_source import page


class InspectionSourceTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 10, 24, tzinfo=UTC)
        self.rows = [
            {"id": str(i), "updated_at": (self.start + timedelta(seconds=i)).isoformat()}
            for i in range(4)
        ]
        self.query = {
            "updated_after": [self.start.isoformat()],
            "updated_before": [(self.start + timedelta(seconds=3)).isoformat()],
            "limit": ["2"],
        }

    def test_time_window_and_pagination_preserve_all_records(self):
        first = page(self.rows, self.query)
        self.assertEqual([row["id"] for row in first["items"]], ["0", "1"])
        second = page(self.rows, {**self.query, "cursor": [first["next_cursor"]]})
        self.assertEqual([row["id"] for row in second["items"]], ["2"])
        self.assertIsNone(second["next_cursor"])

    def test_future_observations_do_not_change_frozen_window(self):
        extra = {"id": "future", "updated_at": (self.start + timedelta(days=1)).isoformat()}
        self.assertEqual(page(self.rows, self.query), page([*self.rows, extra], self.query))

    def test_naive_and_reversed_windows_are_rejected(self):
        for changes in [
            {"updated_after": ["2026-10-24T00:00:00"]},
            {"updated_before": [self.start.isoformat()]},
        ]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                page(self.rows, {**self.query, **changes})

    def test_page_budget_and_negative_cursor_are_rejected(self):
        for changes in [{"limit": ["51"]}, {"limit": ["0"]}, {"cursor": ["-1"]}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                page(self.rows, {**self.query, **changes})


if __name__ == "__main__":
    unittest.main()
