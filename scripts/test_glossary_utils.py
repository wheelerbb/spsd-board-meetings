import json
import os
import tempfile
import unittest

from glossary_utils import (
    render_glossary_hints, to_replacement_map, replacements_for_meeting, save_glossary,
    load_glossary, discover_term_candidates, discover_recurring_speaker_candidates,
    apply_discovered_terms,
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


class SaveGlossaryTests(unittest.TestCase):
    def test_round_trips_through_load_glossary(self):
        entries = [
            {"correct": "Kaler", "aliases": ["Caler"], "note": "Kaler Elementary School"},
            {"correct": "Ishmeel Daniels", "aliases": ["Judd Daniels"], "auto_discovered": True, "scope": ["2026-09-14"]},
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


class DiscoverRecurringSpeakerCandidatesTests(unittest.TestCase):
    def _meeting(self, slug, speakers):
        return {"slug": slug, "timeline": [
            {"topic": "Public Comment", "desc": "", "children": [{"speaker": s, "text": ""} for s in speakers]}
        ]}

    def test_majority_over_minority_produces_candidate(self):
        meetings = [
            self._meeting("2026-03-23", ["Ishmeel Daniels"]),
            self._meeting("2026-04-02", ["Ishmeel Daniels"]),
            self._meeting("2026-09-14", ["Judd Daniels"]),
        ]
        obs = discover_recurring_speaker_candidates(meetings)
        self.assertEqual(len(obs), 1)
        self.assertEqual(obs[0]["correct"], "Ishmeel Daniels")
        self.assertEqual(obs[0]["alias"], "Judd Daniels")
        self.assertEqual(obs[0]["slug"], "2026-09-14")

    def test_tied_spellings_produce_nothing(self):
        meetings = [
            self._meeting("2026-03-23", ["Ishmeel Daniels"]),
            self._meeting("2026-09-14", ["Judd Daniels"]),
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
        self.assertTrue(entries[0]["auto_discovered"])

    def test_second_distinct_meeting_promotes_to_global(self):
        entries = [{"correct": "Ishmeel Daniels", "aliases": ["Judd Daniels"], "auto_discovered": True,
                    "scope": ["2026-09-14"]}]
        apply_discovered_terms(entries, [
            {"correct": "Ishmeel Daniels", "alias": "Judd Daniels", "type": "person", "slug": "2026-04-02"},
        ])
        self.assertNotIn("scope", entries[0])

    def test_repeat_observation_same_meeting_does_not_duplicate_scope(self):
        entries = [{"correct": "Ishmeel Daniels", "aliases": ["Judd Daniels"], "auto_discovered": True,
                    "scope": ["2026-09-14"]}]
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


if __name__ == "__main__":
    unittest.main()
