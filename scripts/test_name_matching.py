import unittest

from name_matching import (
    strip_title, surname, match_unique, surname_match, fuzzy_match, fuzzy_surname_match,
    is_complete_person_name, is_subset_name, is_bare_surname, is_nickname_variant,
    is_boundary_variant,
)


class StripTitleTests(unittest.TestCase):
    def test_strips_honorific(self):
        self.assertEqual(strip_title("Ms. DeAngelis"), "DeAngelis")

    def test_strips_role_title(self):
        self.assertEqual(strip_title("Board member Feller"), "Feller")

    def test_strips_bare_member(self):
        # Without this, "Member Smith" and "Patti Smith" look like a two-token name mismatch
        # ("member" vs "patti") rather than the legitimate role-prefixed reference it is — see
        # todos/019.
        self.assertEqual(strip_title("Member Smith"), "Smith")

    def test_strips_superintendent(self):
        self.assertEqual(strip_title("Superintendent Atkinson Duina"), "Atkinson Duina")

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

    def test_hyphenated_surname_matches_spaced_candidate(self):
        # A hyphen is a token separator, not part of one token — "Atkinson-Duina" should
        # trailing-match a candidate spelled "Atkinson Duina" the same way two whitespace-
        # separated tokens would.
        candidates = [{"name": "Dr. Atkinson Duina"}]
        self.assertEqual(match_unique("Atkinson-Duina", candidates), "Dr. Atkinson Duina")


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

    def test_acronym_candidate_never_fuzzy_matches(self):
        # Regression: "ice" inside "service" used to pass the old raw-substring containment
        # check, and "does"/"doe" clears a flat 0.8 ratio bar (0.86) despite being an ordinary
        # English word, not a misspelling of the acronym. See todos/019.
        self.assertIsNone(fuzzy_match("service", [{"term": "ICE"}]))
        self.assertIsNone(fuzzy_match("does", [{"term": "DOE"}]))

    def test_word_boundary_containment_rejects_mid_word_substring(self):
        # "and" is a substring of "band", but never a whole token of it.
        self.assertIsNone(fuzzy_match("and", [{"term": "Band Committee"}]))

    def test_moderate_length_near_miss_below_scaled_floor_returns_none(self):
        # "Activitas" vs "activities": ratio 0.84 clears a flat 0.8 bar but is a 2-3 character
        # edit away, not the single-character typo the length-scaled floor is meant to allow.
        self.assertIsNone(fuzzy_match("activities", [{"term": "Activitas"}]))

    def test_boundary_variant_never_fuzzy_matches(self):
        # Regression: "main"/"maine" (0.89) and "august"/"augusta" (0.92) both clear the
        # length-scaled ratio floor, but both are an unrelated word's own derivational/
        # inflectional form (an adjective, a month name), not a mishearing. See todos/019.
        self.assertIsNone(fuzzy_match("main", [{"term": "Maine"}]))
        self.assertIsNone(fuzzy_match("August", [{"term": "Augusta"}]))

    def test_acronym_still_matches_via_containment(self):
        # An acronym is only barred from the ratio path (see test_acronym_candidate_never_fuzzy_
        # matches above) — whole-token containment still applies, since an acronym can
        # legitimately be the leading fragment of its own fuller extraction.
        candidates = [{"term": "KLGC, Relations With Law Enforcement Personnel"}]
        self.assertEqual(fuzzy_match("KLGC", candidates), "KLGC, Relations With Law Enforcement Personnel")


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


class IsCompletePersonNameTests(unittest.TestCase):
    def test_first_and_last_is_complete(self):
        self.assertTrue(is_complete_person_name("Claire Holman"))

    def test_surname_only_is_not_complete(self):
        self.assertFalse(is_complete_person_name("Holman"))

    def test_title_plus_surname_is_not_complete(self):
        self.assertFalse(is_complete_person_name("Ms. Holman"))

    def test_title_plus_first_and_last_is_complete(self):
        self.assertTrue(is_complete_person_name("Dr. Claire Holman"))

    def test_empty_is_not_complete(self):
        self.assertFalse(is_complete_person_name(""))


class IsSubsetNameTests(unittest.TestCase):
    def test_surname_is_subset_of_full_name(self):
        self.assertTrue(is_subset_name("Holman", "Claire Holman"))

    def test_title_plus_surname_is_subset_of_full_name(self):
        self.assertTrue(is_subset_name("Ms. Holman", "Claire Holman"))

    def test_order_independent(self):
        self.assertTrue(is_subset_name("Claire Holman", "Holman"))

    def test_different_first_name_same_length_is_not_subset(self):
        # Both sides assert a first name, and they disagree — a mishearing, not a shortform.
        self.assertFalse(is_subset_name("Elyse Holman", "Claire Holman"))

    def test_unrelated_surname_is_not_subset(self):
        self.assertFalse(is_subset_name("Smith", "Claire Holman"))

    def test_empty_is_not_subset(self):
        self.assertFalse(is_subset_name("", "Claire Holman"))


class IsBareSurnameTests(unittest.TestCase):
    def test_surname_alone_is_bare(self):
        self.assertTrue(is_bare_surname("Smith"))

    def test_title_plus_surname_is_not_bare(self):
        self.assertFalse(is_bare_surname("Ms. Holman"))

    def test_first_and_last_is_not_bare(self):
        self.assertFalse(is_bare_surname("Claire Holman"))

    def test_empty_is_not_bare(self):
        self.assertFalse(is_bare_surname(""))


class IsNicknameVariantTests(unittest.TestCase):
    def test_short_prefix_nickname_is_a_variant(self):
        self.assertTrue(is_nickname_variant("Johanna Prince", "Dr. Jo Prince"))

    def test_order_independent(self):
        self.assertTrue(is_nickname_variant("Dr. Jo Prince", "Johanna Prince"))

    def test_non_prefix_nickname_is_not_a_variant(self):
        # "Bill" isn't a literal prefix of "William" — declines rather than guessing, see
        # is_nickname_variant's docstring.
        self.assertFalse(is_nickname_variant("Bill Smith", "William Smith"))

    def test_different_surname_is_not_a_variant(self):
        self.assertFalse(is_nickname_variant("Jo Prince", "Jo Smith"))

    def test_identical_first_name_is_not_a_variant(self):
        self.assertFalse(is_nickname_variant("Jo Prince", "Jo Prince"))

    def test_bare_surname_is_not_a_variant(self):
        self.assertFalse(is_nickname_variant("Prince", "Jo Prince"))


class IsBoundaryVariantTests(unittest.TestCase):
    def test_prefix_relationship_is_a_boundary_variant(self):
        self.assertTrue(is_boundary_variant("main", "maine"))

    def test_suffix_relationship_is_a_boundary_variant(self):
        self.assertTrue(is_boundary_variant("mainers", "maine"))

    def test_other_prefix_direction(self):
        self.assertTrue(is_boundary_variant("august", "augusta"))

    def test_mid_word_edit_is_not_a_boundary_variant(self):
        self.assertFalse(is_boundary_variant("holeman", "holman"))

    def test_leading_letter_substitution_is_not_a_boundary_variant(self):
        self.assertFalse(is_boundary_variant("caler", "kaler"))

    def test_identical_strings_are_not_a_variant(self):
        self.assertFalse(is_boundary_variant("maine", "maine"))

    def test_empty_is_not_a_variant(self):
        self.assertFalse(is_boundary_variant("", "maine"))


if __name__ == "__main__":
    unittest.main()
