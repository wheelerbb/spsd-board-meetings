import unittest

from name_matching import (
    strip_title, surname, match_unique, surname_match, fuzzy_match, fuzzy_surname_match,
)


class StripTitleTests(unittest.TestCase):
    def test_strips_honorific(self):
        self.assertEqual(strip_title("Ms. DeAngelis"), "DeAngelis")

    def test_strips_role_title(self):
        self.assertEqual(strip_title("Board member Feller"), "Feller")

    def test_no_title_unchanged(self):
        self.assertEqual(strip_title("Rosemarie DeAngelis"), "Rosemarie DeAngelis")

    def test_empty_and_none(self):
        self.assertEqual(strip_title(""), "")
        self.assertEqual(strip_title(None), "")


class SurnameTests(unittest.TestCase):
    def test_last_token_lowercased(self):
        self.assertEqual(surname("Rosemarie DeAngelis"), "deangelis")

    def test_strips_title_first(self):
        self.assertEqual(surname("Ms. DeAngelis"), "deangelis")

    def test_empty_returns_none(self):
        self.assertIsNone(surname(""))
        self.assertIsNone(surname(None))


class MatchUniqueTests(unittest.TestCase):
    def setUp(self):
        self.candidates = [
            {"name": "Rosemarie DeAngelis"},
            {"name": "Daniel Feller"},
            {"name": "Claire Holman"},
        ]

    def test_surname_only_matches_unique_candidate(self):
        self.assertEqual(match_unique("Ms. DeAngelis", self.candidates), "Rosemarie DeAngelis")

    def test_no_match_returns_none(self):
        self.assertIsNone(match_unique("Someone Else", self.candidates))

    def test_ambiguous_match_returns_none(self):
        candidates = [{"name": "John Smith"}, {"name": "Jane Smith"}]
        self.assertIsNone(match_unique("Smith", candidates))

    def test_raw_longer_than_candidate_does_not_match(self):
        # raw has more tokens than the candidate it would otherwise match — never matches a
        # shorter candidate against a fuller raw observation (see docstring).
        candidates = [{"name": "Feller"}]
        self.assertIsNone(match_unique("Daniel Feller", candidates))

    def test_empty_raw_returns_none(self):
        self.assertIsNone(match_unique("", self.candidates))


class FuzzyMatchTests(unittest.TestCase):
    def test_near_miss_spelling_matches(self):
        candidates = [{"term": "Kaler"}]
        self.assertEqual(fuzzy_match("Caler", candidates), "Kaler")

    def test_containment_matches(self):
        candidates = [{"term": "Kaler Elementary School"}]
        self.assertEqual(fuzzy_match("Kaler", candidates), "Kaler Elementary School")

    def test_exact_match_returns_none(self):
        candidates = [{"term": "Kaler"}]
        self.assertIsNone(fuzzy_match("Kaler", candidates))

    def test_unrelated_text_returns_none(self):
        candidates = [{"term": "Kaler Elementary School"}]
        self.assertIsNone(fuzzy_match("Budget Committee", candidates))

    def test_tied_candidates_returns_none(self):
        # Both are containment matches (ratio 1.0 each) — a genuine tie, not a guess.
        candidates = [{"term": "Kaler Elementary School"}, {"term": "Kaler Middle School"}]
        self.assertIsNone(fuzzy_match("Kaler", candidates))

    def test_short_text_returns_none(self):
        self.assertIsNone(fuzzy_match("ab", [{"term": "Kaler"}]))


class SurnameMatchTests(unittest.TestCase):
    def test_correct_surname_wrong_first_name_matches(self):
        # The original bug this whole mechanism exists for: "Elyse Holman" for "Claire Holman".
        candidates = [{"name": "Claire Holman"}, {"name": "Rosemarie DeAngelis"}]
        self.assertEqual(surname_match("Elyse Holman", candidates), "Claire Holman")

    def test_ambiguous_shared_surname_returns_none(self):
        candidates = [{"name": "John Smith"}, {"name": "Jane Smith"}]
        self.assertIsNone(surname_match("Jo Smith", candidates))

    def test_no_surname_match_returns_none(self):
        candidates = [{"name": "Claire Holman"}]
        self.assertIsNone(surname_match("Elyse Nobody", candidates))


class FuzzySurnameMatchTests(unittest.TestCase):
    def test_misheard_surname_resolves_against_full_name(self):
        candidates = [{"name": "Rosemarie DeAngelis"}, {"name": "Claire Holman"}]
        self.assertEqual(fuzzy_surname_match("Holeman", candidates), "Claire Holman")

    def test_exact_surname_returns_none_nothing_to_correct(self):
        candidates = [{"name": "Claire Holman"}]
        self.assertIsNone(fuzzy_surname_match("Holman", candidates))

    def test_unrelated_surname_returns_none(self):
        candidates = [{"name": "Claire Holman"}]
        self.assertIsNone(fuzzy_surname_match("Nobody", candidates))


if __name__ == "__main__":
    unittest.main()
