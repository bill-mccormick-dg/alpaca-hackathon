import unittest
from datetime import datetime, time, timedelta

import farm
from bot.risk import EASTERN

WINDOW = (time(9, 0), time(15, 50))
FLAT, REVIEW = time(15, 50), time(16, 5)


def at(h, m, day=1):  # Tue Sep 1 2026 by default
    return datetime(2026, 9, day, h, m, tzinfo=EASTERN)


class NextActionsTest(unittest.TestCase):
    def test_weekend_does_nothing(self):
        self.assertEqual(farm.next_actions(at(10, 0, day=5), {}, 10, WINDOW, FLAT, REVIEW), [])  # Sat Sep 5

    def test_first_cycle_in_window_is_due(self):
        self.assertEqual(farm.next_actions(at(9, 30), {}, 10, WINDOW, FLAT, REVIEW), ["cycle"])

    def test_cycle_waits_for_the_interval(self):
        last = {"cycle": at(9, 30)}
        self.assertEqual(farm.next_actions(at(9, 35), last, 10, WINDOW, FLAT, REVIEW), [])
        self.assertEqual(farm.next_actions(at(9, 40), last, 10, WINDOW, FLAT, REVIEW), ["cycle"])

    def test_no_cycle_outside_window(self):
        self.assertEqual(farm.next_actions(at(8, 30), {}, 10, WINDOW, FLAT, REVIEW), [])

    def test_flatten_once_after_its_time(self):
        self.assertIn("flatten", farm.next_actions(at(15, 50), {}, 10, WINDOW, FLAT, REVIEW))
        last = {"flatten": at(15, 50)}
        self.assertNotIn("flatten", farm.next_actions(at(15, 55), last, 10, WINDOW, FLAT, REVIEW))
        # ...but again the next day
        self.assertIn("flatten", farm.next_actions(at(15, 51, day=2), last, 10, WINDOW, FLAT, REVIEW))

    def test_review_after_review_time_once(self):
        self.assertEqual(farm.next_actions(at(16, 10), {"flatten": at(15, 50)}, 10, WINDOW, FLAT, REVIEW), ["review"])
        self.assertEqual(farm.next_actions(at(16, 20), {"flatten": at(15, 50), "review": at(16, 10)}, 10, WINDOW, FLAT, REVIEW), [])

    def test_cycle_and_flatten_can_both_be_due_at_1550(self):
        last = {"cycle": at(15, 50) - timedelta(minutes=10)}
        self.assertEqual(farm.next_actions(at(15, 50), last, 10, WINDOW, FLAT, REVIEW), ["cycle", "flatten"])


if __name__ == "__main__":
    unittest.main()
