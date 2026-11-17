import json
import os
import tempfile
import unittest

from glossary_utils import (
    render_glossary_hints, to_replacement_map, replacements_for_meeting, apply_replacements,
    save_glossary, load_glossary, discover_term_candidates, discover_recurring_speaker_candidates,
    dedupe_observations, apply_discovered_terms,
)


class RenderGlossaryHintsTests(unittest.TestCase):
    def test_never_includes_note(self):
        entries = [{"correct": "Kaler", "aliases": ["Caler"], "note": "Kaler Elementary School"}]
        hints = render_glossary_hints(entries)
        self.assertIn("Kaler", hints)
        self.assertIn("Caler", hints)
        self.assertNotIn("Elementary School", hints)

    def test_scoped_entry_still_hinted(self):
        # Hints are spelling-only and timeless-safe regardless of scope — only the destructive
        # find/replace pass (replacements_for_meeting) respects scope, see todos/019.
        entries = [{"correct": "Ishmeel Daniels", "aliases": ["Judd Daniels"], "scope": ["2026-09-14"]}]
        self.assertIn("Ishmeel Daniels", render_glossary_hints(entries))

    def test_misspellings_hinted_alongside_aliases(self):
        entries = [{"correct": "Kaler", "aliases": ["Ms. Kaler"], "misspellings": ["Caler"]}]
        hints = render_glossary_hints(entries)
        self.assertIn("Ms. Kaler", hints)
        self.assertIn("Caler", hints)


class ReplacementsForMeetingTests(unittest.TestCase):
    def test_unscoped_entry_applies_everywhere(self):
        entries = [{"correct": "Kaler", "aliases": ["Caler"]}]
        self.assertEqual(replacements_for_meeting(entries, "2026-01-01"), {"Caler": "Kaler"})
        self.assertEqual(replacements_for_meeting(entries, "2026-12-31"), {"Caler": "Kaler"})

    def test_scoped_entry_applies_only_to_its_slug(self):
        entries = [{"correct": "Ishmeel Daniels", "aliases": ["Judd Daniels"], "scope": ["2026-09-14"]}]
        self.assertEqual(replacements_for_meeting(entries, "2026-09-14"), {"Judd Daniels": "Ishmeel Daniels"})
        self.assertEqual(replacements_for_meeting(entries, "2026-01-01"), {})

    def test_to_replacement_map_ignores_scope(self):
        entries = [{"correct": "Ishmeel Daniels", "aliases": ["Judd Daniels"], "scope": ["2026-09-14"]}]
        self.assertEqual(to_replacement_map(entries), {"Judd Daniels": "Ishmeel Daniels"})

    def test_misspellings_also_get_replaced(self):
        # A misspelling is still something to correct in generated text — the split from aliases
        # is for readability in the file, not a difference in whether it's enforced.
        entries = [{"correct": "Kaler", "aliases": ["Ms. Kaler"], "misspellings": ["Caler"]}]
        self.assertEqual(to_replacement_map(entries), {"Ms. Kaler": "Kaler", "Caler": "Kaler"})


class ApplyReplacementsTests(unittest.TestCase):
    def test_standalone_word_is_replaced(self):
        self.assertEqual(apply_replacements("The main item tonight.", {"main": "UMaine"}),
                          "The UMaine item tonight.")

    def test_substring_inside_longer_word_is_not_corrupted(self):
        # Regression for todos/024: a raw content.replace() would also rewrite "domain" and
        # "maintenance" when "main" is an alias — word-boundary-safe replacement must not.
        content = "The domain stays the same; routine maintenance continues."
        self.assertEqual(apply_replacements(content, {"main": "UMaine"}), content)

    def test_multi_word_alias_still_replaced(self):
        self.assertEqual(
            apply_replacements("Report from Maine Construction Academy today.",
                                {"Maine Construction Academy": "Maine Constriction Academy"}),
            "Report from Maine Constriction Academy today.")

    def test_multiple_occurrences_all_replaced(self):
        content = "main item, the main concern, and another main point."
        self.assertEqual(apply_replacements(content, {"main": "UMaine"}),
                          "UMaine item, the UMaine concern, and another UMaine point.")

    def test_no_replacements_is_a_noop(self):
        self.assertEqual(apply_replacements("Unchanged content.", {}), "Unchanged content.")


class SaveGlossaryTests(unittest.TestCase):
    def test_round_trips_through_load_glossary(self):
        entries = [
            {"correct": "Kaler", "aliases": ["Caler"], "note": "Kaler Elementary School"},
            {"correct": "Ishmeel Daniels", "aliases": ["Judd Daniels"], "type": "person", "scope": ["2026-09-14"]},
        ]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "glossary.json")
            save_glossary(entries, path)
            self.assertEqual(load_glossary(path), entries)
            with open(path) as f:
                text = f.read()
            # One compact object per line, matching the file's existing hand-authored style.
            self.assertIn('{ "correct": "Kaler", "aliases": ["Caler"], "note": "Kaler Elementary School" }', text)


class DiscoverTermCandidatesTests(unittest.TestCase):
    def _meeting(self, slug, timeline):
        return {"slug": slug, "timeline": timeline}

    def test_surname_match_with_differing_first_name(self):
        registry = {
            "person:Claire Holman": {"term": "Claire Holman", "type": "person", "context": "At-Large"},
        }
        meeting = self._meeting("2026-09-14", [
            {"topic": "Finance Committee Report (Elyse Holman)", "desc": "", "children": []},
        ])
        obs = discover_term_candidates(meeting, registry)
        self.assertEqual(len(obs), 1)
        self.assertEqual(obs[0]["correct"], "Claire Holman")
        self.assertEqual(obs[0]["alias"], "Elyse Holman")
        self.assertEqual(obs[0]["note"], "At-Large")
        self.assertEqual(obs[0]["slug"], "2026-09-14")
        self.assertEqual(obs[0]["kind"], "misspelling")  # conflicting first names, not a shortform

    def test_nickname_full_name_is_a_legitimate_alias_not_a_misspelling(self):
        # "Johanna Prince" is her correct full given name, not a mishearing of "Jo Prince" — see
        # is_nickname_variant. Regression for the real 2026-11-16 "Dr. Jo Prince" entry.
        registry = {"person:Dr. Jo Prince": {"term": "Dr. Jo Prince", "type": "person",
                                               "context": "Assistant Superintendent"}}
        meeting = self._meeting("2026-11-02", [
            {"topic": "Report (Johanna Prince)", "desc": "", "children": []},
        ])
        obs = discover_term_candidates(meeting, registry)
        self.assertEqual(len(obs), 1)
        self.assertEqual(obs[0]["correct"], "Dr. Jo Prince")
        self.assertEqual(obs[0]["alias"], "Johanna Prince")
        self.assertEqual(obs[0]["kind"], "alias")

    def test_bare_surname_mention_produces_no_observation(self):
        # "Holman" alone resolves fine against the registry, but it isn't a misspelling to
        # correct and it's too generic to persist as an alias (a later meeting's unrelated
        # "Holman" would get blindly rewritten too) — see is_bare_surname.
        registry = {"person:Claire Holman": {"term": "Claire Holman", "type": "person"}}
        meeting = self._meeting("2026-09-14", [
            {"topic": "Report (Holman)", "desc": "", "children": []},
        ])
        self.assertEqual(discover_term_candidates(meeting, registry), [])

    def test_joint_attribution_parenthetical_does_not_conflate_two_people(self):
        # Regression for the 2026-11-16 bug: a topic jointly attributed to two co-presenters must
        # not be treated as one 5-token raw name that surname-matches one of them while silently
        # absorbing the other's name as unrelated leading noise.
        registry = {
            "person:Lizeth Rios Blas": {"term": "Lizeth Rios Blas", "type": "person"},
        }
        meeting = self._meeting("2026-11-09", [
            {"topic": "Student Representatives Report (Sarah Lupien, Lizeth Rios Blas)",
             "desc": "", "children": []},
        ])
        # Exact match for "Lizeth Rios Blas" produces nothing (identical); "Sarah Lupien" has no
        # registry match at all. Conflated, this used to produce a false alias on "Lizeth Rios
        # Blas" for the string "Sarah Lupien, Lizeth Rios Blas".
        self.assertEqual(discover_term_candidates(meeting, registry), [])

    def test_joint_attribution_parenthetical_resolves_each_name_independently(self):
        registry = {
            "person:Sarah Lupien": {"term": "Sarah Lupien", "type": "person"},
            "person:Lizeth Rios Blas": {"term": "Lizeth Rios Blas", "type": "person"},
        }
        meeting = self._meeting("2026-11-09", [
            {"topic": "Student Representatives Report (Sara Lupien, Lizeth Rios Blas)",
             "desc": "", "children": []},
        ])
        obs = discover_term_candidates(meeting, registry)
        self.assertEqual(len(obs), 1)
        self.assertEqual(obs[0]["correct"], "Sarah Lupien")
        self.assertEqual(obs[0]["alias"], "Sara Lupien")

    def test_ampersand_joined_speaker_field_does_not_conflate_two_people(self):
        # Regression found during Stage 3 (todos/024): a timeline child's own `speaker` field,
        # unlike the topic parenthetical above, held a joined co-presenter string
        # ("Gretchen McCloy & Bryna Latham") that went unsplit, becoming a false alias for
        # whichever one person surname_match happened to anchor on.
        registry = {"person:Bryna Latham": {"term": "Bryna Latham", "type": "person"}}
        meeting = self._meeting("2026-06-10", [
            {"topic": "Community Partnerships Update", "desc": "", "children": [
                {"speaker": "Gretchen McCloy & Bryna Latham", "text": ""},
            ]},
        ])
        # Exact match for "Bryna Latham" produces nothing; "Gretchen McCloy" has no registry
        # match. Conflated, this used to produce a false alias on "Bryna Latham" for the whole
        # joined string.
        self.assertEqual(discover_term_candidates(meeting, registry), [])

    def test_complete_name_promoted_over_title_only_registry_term(self):
        # The registry's own term is title+surname only — it never asserted there's no first
        # name, just never recorded one. A mention that supplies a complete name should become
        # `correct` instead of being "corrected" down to the less complete registry spelling.
        registry = {"person:Ms. Holman": {"term": "Ms. Holman", "type": "person",
                                           "context": "Board of Education member"}}
        meeting = self._meeting("2026-09-14", [
            {"topic": "Report (Claire Holman)", "desc": "", "children": []},
        ])
        obs = discover_term_candidates(meeting, registry)
        self.assertEqual(len(obs), 1)
        self.assertEqual(obs[0]["correct"], "Claire Holman")
        self.assertEqual(obs[0]["alias"], "Ms. Holman")
        self.assertEqual(obs[0]["kind"], "alias")

    def test_identical_name_produces_nothing(self):
        registry = {"person:Claire Holman": {"term": "Claire Holman", "type": "person"}}
        meeting = self._meeting("2026-09-14", [
            {"topic": "Finance Committee Report (Claire Holman)", "desc": "", "children": []},
        ])
        self.assertEqual(discover_term_candidates(meeting, registry), [])

    def test_place_mention_in_free_text(self):
        registry = {"place:Kaler Elementary School": {"term": "Kaler Elementary School", "type": "place"}}
        meeting = self._meeting("2026-09-14", [
            {"topic": "Budget", "desc": "Discussion of Caler Elementary School repairs.", "children": []},
        ])
        obs = discover_term_candidates(meeting, registry)
        self.assertTrue(any(o["correct"] == "Kaler Elementary School" for o in obs))
        self.assertTrue(all(o["kind"] == "misspelling" for o in obs))

    def test_generic_trailing_phrase_does_not_false_match(self):
        # Regression test for the 2026-11-13 glossary.json corruption: whole-string fuzzy ratio
        # between "for elementary school" and "kaler elementary school" is 0.86 (>= the 0.8
        # threshold) purely because of the shared generic tail — the leading-token anchor must
        # reject this before fuzzy_match ever runs, or every generic "...elementary school(s)"
        # phrase in the corpus becomes a false "Kaler" alias. See todos/019 work log.
        registry = {"place:Kaler Elementary School": {"term": "Kaler Elementary School", "type": "place"}}
        meeting = self._meeting("2026-09-14", [
            {"topic": "Budget", "desc": "We discussed closing an elementary school this year.", "children": []},
        ])
        self.assertEqual(discover_term_candidates(meeting, registry), [])

    def test_trailing_token_mismatch_does_not_false_match(self):
        # Regression for the 2026-11-14 false-alias explosion: a shared leading phrase ("Elementary
        # Reconfiguration") used to let a wholly unrelated trailing word ("plan") slip through on
        # whole-string ratio alone. Every token position must now agree. See todos/019.
        registry = {"organization:Elementary Reconfiguration Committee":
                    {"term": "Elementary Reconfiguration Committee", "type": "organization"}}
        meeting = self._meeting("2026-09-14", [
            {"topic": "Update", "desc": "The elementary reconfiguration plan was presented tonight.",
             "children": []},
        ])
        self.assertEqual(discover_term_candidates(meeting, registry), [])

    def test_acronym_entity_not_matched_against_common_word(self):
        # "ice" is a substring of "service" and "DOE" fuzzy-ratios 0.86 against "does" — neither
        # is a real misspelling of the acronym. See todos/019.
        registry = {
            "organization:ICE": {"term": "ICE", "type": "organization"},
            "organization:DOE": {"term": "DOE", "type": "organization"},
        }
        meeting = self._meeting("2026-09-14", [
            {"topic": "Policy", "desc": "The district does not provide this service.", "children": []},
        ])
        self.assertEqual(discover_term_candidates(meeting, registry), [])

    def test_possessive_suffix_is_not_treated_as_alias(self):
        # "Portland's" is the correct word "Portland" plus a grammatical suffix, not a misspelling
        # — flagging it as an alias would strip the possessive when "corrected" in rendered text.
        registry = {"place:Portland": {"term": "Portland", "type": "place"}}
        meeting = self._meeting("2026-09-14", [
            {"topic": "Facilities", "desc": "Portland's approach was used as an example.", "children": []},
        ])
        self.assertEqual(discover_term_candidates(meeting, registry), [])

    def test_ampersand_variant_is_not_treated_as_alias(self):
        # "&" reads the same as "and" — a stylistic variant of the same committee name, not a
        # misspelling needing correction (same category as the possessive-suffix case above).
        registry = {"organization:Buildings and Grounds Committee":
                    {"term": "Buildings and Grounds Committee", "type": "organization"}}
        meeting = self._meeting("2026-09-14", [
            {"topic": "Report", "desc": "The Buildings & Grounds Committee met Tuesday.", "children": []},
        ])
        self.assertEqual(discover_term_candidates(meeting, registry), [])

    def test_month_name_is_not_treated_as_misspelled_place(self):
        # Regression for the real 2026-11-16 "Augusta"/"August" entry: "the August survey" refers
        # to the month, not the place — "august" is an exact prefix of "augusta" and scores 0.92
        # via ratio, well above the floor, so this relies on is_boundary_variant, not the floor.
        registry = {"place:Augusta": {"term": "Augusta", "type": "place"}}
        meeting = self._meeting("2026-09-14", [
            {"topic": "Budget", "desc": "He criticizes the August survey as flawed.", "children": []},
        ])
        self.assertEqual(discover_term_candidates(meeting, registry), [])

    def test_demonym_is_not_treated_as_misspelled_place(self):
        # Regression for the real 2026-11-16 "Maine"/"Mainers" entry: "Mainers" (people from
        # Maine) is a real word derived from "Maine", not a mishearing of it.
        registry = {"place:Maine": {"term": "Maine", "type": "place"}}
        meeting = self._meeting("2026-09-14", [
            {"topic": "Policy", "desc": "The ordinance protects new Mainers.", "children": []},
        ])
        self.assertEqual(discover_term_candidates(meeting, registry), [])

    def test_unrelated_short_word_is_not_treated_as_misspelled_place(self):
        registry = {"place:Maine": {"term": "Maine", "type": "place"}}
        meeting = self._meeting("2026-09-14", [
            {"topic": "Agenda", "desc": "This is the main item on tonight's agenda.", "children": []},
        ])
        self.assertEqual(discover_term_candidates(meeting, registry), [])

    def test_common_english_word_is_not_treated_as_misspelled_entity(self):
        # Regression for the Stage 3 full-corpus run (todos/024): a registry entity that happens
        # to also be an ordinary short word ("State") fuzzy-matched unrelated real words that
        # merely sit a short edit-distance away ("start"/"stage"/"taste"/"slate") -- distinct from
        # the boundary-variant case above, since none of these is an exact prefix/suffix of
        # "state". Confirmed real impact: 326 of 1781 preview replacement occurrences across 58 of
        # 99 meetings traced to exactly this gap.
        registry = {"organization:State": {"term": "State", "type": "organization"}}
        for word in ["start", "stage", "taste", "slate"]:
            meeting = self._meeting("2026-09-14", [
                {"topic": "Budget", "desc": f"The committee will {word} the review next week.", "children": []},
            ])
            self.assertEqual(discover_term_candidates(meeting, registry), [],
                              f"{word!r} should not be flagged as a misspelling of 'State'")

    def test_place_name_still_matches_genuine_non_word_misspelling(self):
        # The dictionary-word guard must not swallow legitimate corrections -- "machine" is a
        # real word and correctly rejected, but a genuine OCR/mishearing artifact that ISN'T a
        # real word (same shape as the existing "Caler"/"Kaler" case) must still match.
        registry = {"place:Maine": {"term": "Maine", "type": "place"}}
        meeting = self._meeting("2026-09-14", [
            {"topic": "Budget", "desc": "This is a machine problem, not a Mayne issue.", "children": []},
        ])
        obs = discover_term_candidates(meeting, registry)
        self.assertEqual(len(obs), 1)
        self.assertEqual(obs[0]["alias"], "Mayne")

    def test_repeated_mention_in_one_meeting_is_deduped(self):
        registry = {
            "person:Ms. Angela Atkinson-Duina": {"term": "Ms. Angela Atkinson-Duina", "type": "person"},
        }
        meeting = self._meeting("2026-09-14", [
            {"topic": "Item 1", "desc": "", "children": [
                {"speaker": "Dr. Atkinson Duina", "text": ""},
                {"speaker": "Dr. Atkinson Duina", "text": ""},
            ]},
            {"topic": "Item 2", "desc": "", "children": [{"speaker": "Dr. Atkinson Duina", "text": ""}]},
        ])
        obs = discover_term_candidates(meeting, registry)
        self.assertEqual(len(obs), 1)

    def test_note_uses_longest_context_fragment(self):
        registry = {"person:Mr. Daniel Feller": {
            "term": "Mr. Daniel Feller", "type": "person",
            "context": "Board Member; Board of Education, B&G Committee Chairman",
        }}
        meeting = self._meeting("2026-09-14", [
            {"topic": "Report (Member Feller)", "desc": "", "children": []},
        ])
        obs = discover_term_candidates(meeting, registry)
        self.assertEqual(obs[0]["note"], "Board of Education, B&G Committee Chairman")


class DedupeObservationsTests(unittest.TestCase):
    def test_same_pair_from_two_discovery_mechanisms_collapses_to_one(self):
        # discover_term_candidates (registry match) and discover_recurring_speaker_candidates
        # (cross-meeting fallback) can independently flag the exact same alias for the same
        # meeting; apply_discovered_terms is idempotent either way, but the combined list itself
        # should stay duplicate-free.
        obs = [
            {"correct": "Eleni Richardson", "alias": "Richardson", "type": "person", "slug": "2026-11-02"},
            {"correct": "Eleni Richardson", "alias": "Richardson", "type": "person",
             "note": "recurring speaker; 3x vs 1x", "slug": "2026-11-02"},
        ]
        self.assertEqual(len(dedupe_observations(obs)), 1)

    def test_same_alias_different_meeting_is_not_deduped(self):
        obs = [
            {"correct": "Mr. Daniel Feller", "alias": "Feller", "type": "person", "slug": "2026-11-02"},
            {"correct": "Mr. Daniel Feller", "alias": "Feller", "type": "person", "slug": "2026-10-14"},
        ]
        self.assertEqual(len(dedupe_observations(obs)), 2)


class DiscoverRecurringSpeakerCandidatesTests(unittest.TestCase):
    def _meeting(self, slug, speakers):
        return {"slug": slug, "timeline": [
            {"topic": "Public Comment", "desc": "", "children": [{"speaker": s, "text": ""} for s in speakers]}
        ]}

    def test_conflicting_first_name_produces_nothing_even_with_strong_majority(self):
        # Regression for the 2026-11-16 Melinda Hall / Angelica Hall bug: two complete spellings
        # sharing a surname can only differ by a conflicting first name, which is indistinguishable
        # here between "misheard" and "two different real people" — never resolved by this
        # no-corroboration fallback, no matter how lopsided the frequency. See todos/019.
        meetings = [
            self._meeting("2026-03-23", ["Ishmeel Daniels"]),
            self._meeting("2026-04-02", ["Ishmeel Daniels"]),
            self._meeting("2026-09-14", ["Judd Daniels"]),
        ]
        self.assertEqual(discover_recurring_speaker_candidates(meetings), [])

    def test_two_distinct_people_sharing_a_surname_are_not_merged(self):
        # The real case that exposed the bug: a parent/community organizer (Melinda Hall, 2
        # meetings) and an unrelated teacher (Angelica Hall, 1 meeting) who merely share a surname.
        meetings = [
            self._meeting("2026-10-26", ["Melinda Hall"]),
            self._meeting("2026-11-09", ["Melinda Hall"]),
            self._meeting("2026-11-02", ["Angelica Hall"]),
        ]
        self.assertEqual(discover_recurring_speaker_candidates(meetings), [])

    def test_tied_spellings_produce_nothing(self):
        meetings = [
            self._meeting("2026-03-23", ["Ishmeel Daniels"]),
            self._meeting("2026-09-14", ["Judd Daniels"]),
        ]
        self.assertEqual(discover_recurring_speaker_candidates(meetings), [])

    def test_complete_name_wins_over_more_frequent_titled_form(self):
        # "Ms. Holman" alone is more frequent here, but a title+surname form never outranks a
        # complete first+last spelling — see is_complete_person_name. (Unlike a bare surname,
        # "Ms. Holman" isn't filtered out by is_bare_surname, so it's still a useful regression
        # case for the completeness ranking itself.)
        meetings = [
            self._meeting("2026-10-14", ["Ms. Holman"]),
            self._meeting("2026-10-26", ["Ms. Holman"]),
            self._meeting("2026-11-02", ["Claire Holman"]),
            self._meeting("2026-11-09", ["Claire Holman"]),
        ]
        obs = discover_recurring_speaker_candidates(meetings)
        self.assertEqual(len(obs), 1)
        self.assertEqual(obs[0]["correct"], "Claire Holman")
        self.assertEqual(obs[0]["alias"], "Ms. Holman")
        self.assertEqual(obs[0]["kind"], "alias")  # title+surname is a legitimate shortform

    def test_bare_surname_minority_produces_no_candidate(self):
        # Same completeness gap as above, but the minority spelling is a bare surname rather than
        # a titled one — too generic to persist as an alias, see is_bare_surname.
        meetings = [
            self._meeting("2026-10-14", ["Holman"]),
            self._meeting("2026-10-26", ["Holman"]),
            self._meeting("2026-11-02", ["Claire Holman"]),
            self._meeting("2026-11-09", ["Claire Holman"]),
        ]
        self.assertEqual(discover_recurring_speaker_candidates(meetings), [])


class ApplyDiscoveredTermsTests(unittest.TestCase):
    def test_new_entity_appended_with_scope(self):
        entries = []
        mutated = apply_discovered_terms(entries, [
            {"correct": "Ishmeel Daniels", "alias": "Judd Daniels", "note": "", "type": "person", "slug": "2026-09-14"},
        ])
        self.assertTrue(mutated)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["scope"], ["2026-09-14"])
        self.assertNotIn("auto_discovered", entries[0])  # no provenance tag — every entry works the same way

    def test_second_distinct_meeting_promotes_to_global(self):
        entries = [{"correct": "Ishmeel Daniels", "aliases": ["Judd Daniels"], "scope": ["2026-09-14"]}]
        apply_discovered_terms(entries, [
            {"correct": "Ishmeel Daniels", "alias": "Judd Daniels", "type": "person", "slug": "2026-04-02"},
        ])
        self.assertNotIn("scope", entries[0])

    def test_repeat_observation_same_meeting_does_not_duplicate_scope(self):
        entries = [{"correct": "Ishmeel Daniels", "aliases": ["Judd Daniels"], "scope": ["2026-09-14"]}]
        apply_discovered_terms(entries, [
            {"correct": "Ishmeel Daniels", "alias": "Judd Daniels", "type": "person", "slug": "2026-09-14"},
        ])
        self.assertEqual(entries[0]["scope"], ["2026-09-14"])

    def test_hand_curated_entry_merged_into_not_skipped(self):
        # "Remove the protection, not the data" — no entry is ever permanently immune to
        # automated merging, regardless of how it originated (see todos/019).
        entries = [{"correct": "Claire Holman", "aliases": ["Elyse Holman"], "note": "At-Large board member"}]
        mutated = apply_discovered_terms(entries, [
            {"correct": "Claire Holman", "alias": "Clare Holman", "type": "person", "slug": "2026-09-14"},
        ])
        self.assertTrue(mutated)
        self.assertEqual(len(entries), 1)
        self.assertIn("Clare Holman", entries[0]["aliases"])
        self.assertNotIn("scope", entries[0])  # stays global — it was never scoped to begin with

    def test_unscoped_entry_just_gets_new_alias(self):
        entries = [{"correct": "Kaler", "aliases": ["Caler"]}]
        mutated = apply_discovered_terms(entries, [
            {"correct": "Kaler", "alias": "Kayler", "type": "place", "slug": "2026-09-14"},
        ])
        self.assertTrue(mutated)
        self.assertEqual(set(entries[0]["aliases"]), {"Caler", "Kayler"})
        self.assertNotIn("scope", entries[0])

    def test_garbled_concatenated_correct_is_rejected(self):
        # Regression test for the 2026-11-13 corruption: a missing-space PDF-text-extraction
        # artifact ("SouthPortlandWineCompany") must never become a glossary `correct` value —
        # render_glossary_hints would then tell Gemini that garbled spelling IS correct.
        entries = []
        mutated = apply_discovered_terms(entries, [
            {"correct": "SouthPortlandWineCompany", "alias": "South Portland Wine Company",
             "type": "organization", "slug": "2026-09-14"},
        ])
        self.assertFalse(mutated)
        self.assertEqual(entries, [])

    def test_normal_single_word_correct_is_not_rejected(self):
        entries = []
        mutated = apply_discovered_terms(entries, [
            {"correct": "Forecaster", "alias": "The Forecaster", "type": "organization", "slug": "2026-09-14"},
        ])
        self.assertTrue(mutated)
        self.assertEqual(len(entries), 1)

    def test_misspelling_kind_files_under_misspellings_not_aliases(self):
        entries = []
        apply_discovered_terms(entries, [
            {"correct": "Kaler", "alias": "Caler", "kind": "misspelling", "type": "place", "slug": "2026-09-14"},
        ])
        self.assertEqual(entries[0].get("misspellings"), ["Caler"])
        self.assertNotIn("Caler", entries[0].get("aliases", []))

    def test_alias_kind_files_under_aliases(self):
        entries = []
        apply_discovered_terms(entries, [
            {"correct": "Claire Holman", "alias": "Holman", "kind": "alias", "type": "person", "slug": "2026-09-14"},
        ])
        self.assertEqual(entries[0].get("aliases"), ["Holman"])
        self.assertNotIn("misspellings", entries[0])

    def test_missing_kind_defaults_to_aliases(self):
        entries = []
        apply_discovered_terms(entries, [
            {"correct": "Claire Holman", "alias": "Holman", "type": "person", "slug": "2026-09-14"},
        ])
        self.assertEqual(entries[0].get("aliases"), ["Holman"])

    def test_variant_already_in_other_field_is_not_duplicated(self):
        # Same raw string shouldn't end up filed under both lists for one entry.
        entries = [{"correct": "Kaler", "aliases": ["Ms. Kaler"]}]
        mutated = apply_discovered_terms(entries, [
            {"correct": "Kaler", "alias": "Ms. Kaler", "kind": "misspelling", "type": "place", "slug": "2026-09-14"},
        ])
        self.assertFalse(mutated)
        self.assertNotIn("misspellings", entries[0])


if __name__ == "__main__":
    unittest.main()
