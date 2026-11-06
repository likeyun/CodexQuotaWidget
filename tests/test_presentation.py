import unittest

from quota_widget.presentation import select_display_window


def window(seconds, remaining):
    return {"limit_window_seconds": seconds, "remaining_percent": remaining}


def payload(primary=None, secondary=None, **extra):
    return {"windows": {"primary": primary, "secondary": secondary}, **extra}


class DisplayWindowTests(unittest.TestCase):
    def test_five_hour_preferred_over_weekly(self):
        five_hour = window(18000, 57)
        self.assertEqual(select_display_window(payload(five_hour, window(604800, 75))), ("five_hour", five_hour))

    def test_weekly_in_primary_is_usable(self):
        weekly = window(604800, 75)
        self.assertEqual(select_display_window(payload(weekly)), ("weekly", weekly))

    def test_zero_five_hour_balance_is_valid_and_preferred(self):
        five_hour = window(18000, 0)
        self.assertEqual(select_display_window(payload(five_hour, window(604800, 75))), ("five_hour", five_hour))

    def test_invalid_five_hour_balance_falls_back_to_weekly(self):
        weekly = window(604800, 75)
        for remaining in (None, True, "50", float("nan"), float("inf"), -1, 101):
            with self.subTest(remaining=remaining):
                self.assertEqual(select_display_window(payload(window(18000, remaining), weekly)), ("weekly", weekly))

    def test_swapped_windows_still_prefer_five_hour(self):
        five_hour = window(18000, 57)
        self.assertEqual(select_display_window(payload(window(604800, 75), five_hour)), ("five_hour", five_hour))

    def test_no_data_or_missing_windows_waits(self):
        for data in (None, {}, {"windows": None}, {"windows": []}, []):
            with self.subTest(data=data):
                self.assertEqual(select_display_window(data), ("unknown", None))

    def test_missing_or_unknown_duration_is_never_assumed(self):
        for seconds in (None, 0, 3600, "18000"):
            with self.subTest(seconds=seconds):
                self.assertEqual(select_display_window(payload(window(seconds, 80))), ("unknown", None))
        self.assertEqual(select_display_window(payload({"remaining_percent": 80})), ("unknown", None))

    def test_stale_data_is_still_usable(self):
        five_hour = window(18000, 57)
        self.assertEqual(select_display_window(payload(five_hour, stale=True, ok=False)), ("five_hour", five_hour))

    def test_both_windows_missing_waits(self):
        self.assertEqual(select_display_window(payload()), ("unknown", None))

    def test_unknown_primary_does_not_hide_known_weekly(self):
        weekly = window(604800, 0)
        self.assertEqual(select_display_window(payload(window(None, 80), weekly)), ("weekly", weekly))


if __name__ == "__main__":
    unittest.main()
