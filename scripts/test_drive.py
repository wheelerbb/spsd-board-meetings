import unittest

from sourcing.drive import extract_date_from_content


class ExtractDateFromContentTests(unittest.TestCase):
    def test_squished_header_date_beats_later_wellformed_reference(self):
        # Real pattern: PDF extraction drops the space in the header's own date ("MAY13") while
        # leaving normal spacing on a later, merely-referenced date ("April 8, 2024").
        text = (
            "BOARDOFEDUCATIONMEETINGAGENDA\nREGULARMEETING\nMONDAY, MAY13, 2024\n6:00P.M.\n"
            "...Consent Agenda...\n"
            "1. Approval of the minutes from the Regular Meeting held on Monday, April 8, 2024."
        )
        self.assertEqual(extract_date_from_content(text), "2024-05-13")

    def test_minutes_preamble_picks_own_date_not_approval_date(self):
        # Real pattern: minutes state their own meeting date, then the future meeting where
        # they'll be approved — both properly spaced, own-date must win despite squished joins.
        text = (
            "BOARDOFEDUCATION\nSOUTHPORTLAND, MAINE\n"
            "Thefollowingisabrief summary...at itsRegular MeetingonMonday, March11, 2024. "
            "Pleasenotethat theminuteswill not befinal until approvedat thenext Regular "
            "MeetingonApril 8, 2024."
        )
        self.assertEqual(extract_date_from_content(text), "2024-03-11")

    def test_normal_wellformed_header(self):
        text = "Regular Meeting\nMonday, June 10, 2024\n6:00 PM\nAgenda..."
        self.assertEqual(extract_date_from_content(text), "2024-06-10")

    def test_date_past_header_window_not_used(self):
        text = "x" * 700 + " Monday, June 10, 2024"
        self.assertIsNone(extract_date_from_content(text))

    def test_no_date_returns_none(self):
        self.assertIsNone(extract_date_from_content("No dates here at all."))

    def test_empty_text_returns_none(self):
        self.assertIsNone(extract_date_from_content(""))
        self.assertIsNone(extract_date_from_content(None))


if __name__ == "__main__":
    unittest.main()
