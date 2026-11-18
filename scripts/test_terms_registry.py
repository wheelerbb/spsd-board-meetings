import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from terms_registry import ingest_document_terms, canonical_person, persons, entities_of_type


class IngestDocumentTermsTests(unittest.TestCase):
    def test_new_entity_created(self):
        registry = {}
        mutated = ingest_document_terms(
            registry, "file1", "2026-04-02", "agenda",
            [{"term": "Ishmeel Daniels", "type": "person", "context": "public comment"}],
        )
        self.assertTrue(mutated)
        entity = registry["person:Ishmeel Daniels"]
        self.assertEqual(entity["term"], "Ishmeel Daniels")
        self.assertEqual(entity["context"], "public comment")
        self.assertEqual(len(entity["sources"]), 1)
        self.assertEqual(entity["first_seen"], "2026-04-02")
        self.assertEqual(entity["last_seen"], "2026-04-02")

    def test_second_mention_widens_lineage_not_duplicates(self):
        registry = {}
        ingest_document_terms(registry, "file1", "2026-03-23", "agenda",
                               [{"term": "Ishmeel Daniels", "type": "person", "context": "candidate"}])
        ingest_document_terms(registry, "file2", "2026-04-02", "agenda",
                               [{"term": "Ishmeel Daniels", "type": "person", "context": "candidate"}])
        self.assertEqual(len(registry), 1)
        entity = registry["person:Ishmeel Daniels"]
        self.assertEqual(len(entity["sources"]), 2)
        self.assertEqual(entity["first_seen"], "2026-03-23")
        self.assertEqual(entity["last_seen"], "2026-04-02")

    def test_shorter_fragment_merges_into_existing_fuller_entity(self):
        registry = {}
        ingest_document_terms(registry, "file1", "2026-01-08", "minutes",
                               [{"term": "Rosemarie DeAngelis", "type": "person", "context": "Board Chair"}])
        ingest_document_terms(registry, "file2", "2026-02-12", "minutes",
                               [{"term": "DeAngelis", "type": "person", "context": "moved to approve"}])
        self.assertEqual(len(registry), 1)
        entity = registry["person:Rosemarie DeAngelis"]
        self.assertEqual(len(entity["sources"]), 2)

    def test_fuller_later_mention_upgrades_canonical_spelling(self):
        registry = {}
        ingest_document_terms(registry, "file1", "2026-01-08", "minutes",
                               [{"term": "DeAngelis", "type": "person"}])
        ingest_document_terms(registry, "file2", "2026-02-12", "minutes",
                               [{"term": "Rosemarie DeAngelis", "type": "person"}])
        self.assertEqual(len(registry), 1)
        self.assertIn("person:Rosemarie DeAngelis", registry)
        self.assertEqual(registry["person:Rosemarie DeAngelis"]["term"], "Rosemarie DeAngelis")

    def test_complete_name_wins_over_longer_titled_fragment(self):
        # "Ms. Holman" (title+surname) is longer in characters than "Claire Holman", but the
        # complete first+last form must still win as canonical — a document stating only
        # "Ms. Holman" never asserted there's no first name. See todos/019.
        registry = {}
        ingest_document_terms(registry, "file1", "2026-01-08", "agenda",
                               [{"term": "Ms. Holman", "type": "person"}])
        ingest_document_terms(registry, "file2", "2026-02-12", "minutes",
                               [{"term": "Claire Holman", "type": "person"}])
        self.assertEqual(len(registry), 1)
        self.assertIn("person:Claire Holman", registry)
        self.assertEqual(registry["person:Claire Holman"]["term"], "Claire Holman")

    def test_titled_surname_merges_with_titled_full_name(self):
        # Regression: _person_match used to strip the title only off the incoming term, never
        # off an already-registered candidate — so "Mr. Feller" and "Mr. Daniel Feller" (both
        # still carrying "Mr.") split into two disconnected entities instead of merging. Found
        # via a live trailing-5-meetings iteration, see todos/019.
        registry = {}
        ingest_document_terms(registry, "file1", "2026-10-14", "minutes",
                               [{"term": "Mr. Feller", "type": "person"}])
        ingest_document_terms(registry, "file2", "2026-10-01", "minutes",
                               [{"term": "Mr. Daniel Feller", "type": "person"}])
        self.assertEqual(len(registry), 1)
        entity = registry["person:Mr. Daniel Feller"]
        self.assertEqual(len(entity["sources"]), 2)

    def test_titled_nickname_merges_via_fuzzy_fallback(self):
        # Same asymmetry one layer down: the leftover "Mr. " prefix dragged the fuzzy-match
        # ratio for "Mr. Mitchell Ouellet" vs. "Mitch Ouellet" to 0.79 (just under the 0.8 bar);
        # stripping the title before the fallback call brings it to 0.90.
        registry = {}
        ingest_document_terms(registry, "file1", "2026-10-14", "minutes",
                               [{"term": "Mitch Ouellet", "type": "person"}])
        ingest_document_terms(registry, "file2", "2026-10-01", "minutes",
                               [{"term": "Mr. Mitchell Ouellet", "type": "person"}])
        self.assertEqual(len(registry), 1)

    def test_hyphenated_surname_merges_with_spaced_form(self):
        # Regression: "Atkinson-Duina" tokenized as one token via plain .split(), so it never
        # trailing-matched "Atkinson Duina" (two tokens) even after the title-stripping fix
        # above. Found in the same live iteration as the Feller/Risch cases, see todos/019.
        registry = {}
        ingest_document_terms(registry, "file1", "2026-10-01", "minutes",
                               [{"term": "Dr. Atkinson Duina", "type": "person"}])
        ingest_document_terms(registry, "file2", "2026-10-14", "minutes",
                               [{"term": "Ms. Angela Atkinson-Duina", "type": "person"}])
        self.assertEqual(len(registry), 1)

    def test_cross_type_exact_string_reconciles_to_first_type(self):
        # Regression: Gemini's own type classification isn't perfectly consistent call-to-call —
        # the exact same literal string extracted as a different `type` in a later document used
        # to fork into a second, disconnected entity (registry key is `type:term`). "Taj" was
        # genuinely a person in the source text but got typed "organization" in one document.
        registry = {}
        ingest_document_terms(registry, "file1", "2026-11-09", "packet",
                               [{"term": "Taj", "type": "person", "context": "thanked for hosting"}])
        ingest_document_terms(registry, "file2", "2026-10-14", "minutes",
                               [{"term": "Taj", "type": "organization"}])
        self.assertEqual(len(registry), 1)
        self.assertEqual(registry["person:Taj"]["type"], "person")
        self.assertEqual(len(registry["person:Taj"]["sources"]), 2)

    def test_cross_type_ambiguous_match_stays_separate(self):
        # Safe-degrade: if two entities under the same string but different types already
        # coexist (only possible from registry state built before this reconciliation existed —
        # ingest_document_terms itself always collapses a new cross-type duplicate into the one
        # prior match before a second can accumulate), a third mention under yet another type
        # can't tell which of the two it corroborates, so it becomes its own entity rather than
        # guessing.
        registry = {
            "person:Taj": {"term": "Taj", "type": "person", "context": "", "sources": [],
                           "first_seen": "2026-10-01", "last_seen": "2026-10-01"},
            "place:Taj": {"term": "Taj", "type": "place", "context": "", "sources": [],
                          "first_seen": "2026-10-14", "last_seen": "2026-10-14"},
        }
        ingest_document_terms(registry, "file3", "2026-11-09", "packet", [{"term": "Taj", "type": "organization"}])
        self.assertEqual(len(registry), 3)

    def test_hedge_phrased_context_is_not_accumulated(self):
        # Gemini's own uncertainty about itself ("likely a board member") isn't a role fact and
        # shouldn't accumulate into context the way a real distinct fact does. See todos/019.
        registry = {}
        ingest_document_terms(registry, "file1", "2026-10-14", "minutes",
                               [{"term": "Patti Smith", "type": "person",
                                 "context": "Listed under Call to Order, likely a board member"}])
        ingest_document_terms(registry, "file2", "2026-10-01", "minutes",
                               [{"term": "Patti Smith", "type": "person", "context": "Board Member"}])
        self.assertEqual(registry["person:Patti Smith"]["context"], "Board Member")

    def test_case_variant_place_consolidates_into_one_entity(self):
        # Regression: an ALL-CAPS agenda-header extraction and a mixed-case minutes-body
        # extraction of the same place name used to fork into two disconnected entities, since
        # registry keys were case-sensitive and fuzzy_match treats an exact-after-lowercasing
        # match as "nothing to correct". See todos/021. Confirmed live:
        # place:South Portland, Maine vs place:SOUTH PORTLAND, MAINE.
        registry = {}
        ingest_document_terms(registry, "file1", "2024-01-08", "minutes",
                               [{"term": "South Portland, Maine", "type": "place"}])
        ingest_document_terms(registry, "file2", "2024-02-12", "agenda",
                               [{"term": "SOUTH PORTLAND, MAINE", "type": "place"}])
        self.assertEqual(len(registry), 1)
        entity = registry["place:South Portland, Maine"]
        self.assertEqual(entity["term"], "South Portland, Maine")
        self.assertEqual(len(entity["sources"]), 2)

    def test_case_variant_matches_regardless_of_which_casing_came_first(self):
        # Mirror of the above with the ALL-CAPS form registered first — the fix must not depend
        # on which side arrives first.
        registry = {}
        ingest_document_terms(registry, "file1", "2024-02-12", "agenda",
                               [{"term": "BOARD WORKSHOP", "type": "program"}])
        ingest_document_terms(registry, "file2", "2024-01-08", "minutes",
                               [{"term": "Board Workshop", "type": "program"}])
        self.assertEqual(len(registry), 1)
        self.assertIn("program:BOARD WORKSHOP", registry)
        self.assertEqual(registry["program:BOARD WORKSHOP"]["term"], "BOARD WORKSHOP")
        self.assertEqual(len(registry["program:BOARD WORKSHOP"]["sources"]), 2)

    def test_place_fragments_consolidate_via_containment(self):
        registry = {}
        ingest_document_terms(registry, "file1", "2026-01-08", "agenda",
                               [{"term": "Kaler Elementary School", "type": "place"}])
        ingest_document_terms(registry, "file2", "2026-02-12", "agenda",
                               [{"term": "Kaler", "type": "place"}])
        self.assertEqual(len(registry), 1)

    def test_distinct_people_stay_separate(self):
        registry = {}
        ingest_document_terms(registry, "file1", "2026-01-08", "minutes",
                               [{"term": "Daniel Feller", "type": "person"}])
        ingest_document_terms(registry, "file2", "2026-02-12", "minutes",
                               [{"term": "Claire Holman", "type": "person"}])
        self.assertEqual(len(registry), 2)

    def test_no_terms_not_mutated(self):
        registry = {}
        self.assertFalse(ingest_document_terms(registry, "file1", "2026-01-08", "agenda", []))
        self.assertEqual(registry, {})

    def test_concurrent_ingestion_does_not_race(self):
        # Regression test for the 2026-11-13 production bug: post_process.py's ThreadPoolExecutor
        # calls ingest_document_terms for many meetings concurrently against the same registry
        # dict. Before the lock, this intermittently raised "dictionary changed size during
        # iteration" from entities_of_type's list comprehension over registry.values() racing
        # another thread's registry.pop()/registry[key]=... — silently dropping that meeting's
        # terms (caught by post_process.py's broad except). Run many distinct new entities
        # concurrently; with the lock, every one must land with no exception.
        registry = {}
        # Genuinely-distinct surnames (random hex, not sequential numbers) so none is a substring
        # or near-fuzzy-match of another — otherwise ingest_document_terms's own (correct)
        # consolidation logic would legitimately merge them, which isn't what this test checks.
        names = [f"Person {uuid.uuid4().hex}" for _ in range(200)]
        with ThreadPoolExecutor(max_workers=16) as executor:
            futures = [
                executor.submit(
                    ingest_document_terms, registry, f"file{i}", f"2026-01-{i % 28 + 1:02d}", "agenda",
                    [{"term": name, "type": "person", "context": "c"}],
                )
                for i, name in enumerate(names)
            ]
            for future in as_completed(futures):
                future.result()  # re-raises if the worker hit an exception
        self.assertEqual(len(registry), 200)


class CanonicalPersonTests(unittest.TestCase):
    def setUp(self):
        self.registry = {}
        ingest_document_terms(self.registry, "file1", "2026-01-08", "minutes",
                               [{"term": "Rosemarie DeAngelis", "type": "person"},
                                {"term": "Claire Holman", "type": "person"}])

    def test_surname_resolves_to_canonical(self):
        self.assertEqual(canonical_person("Ms. DeAngelis", self.registry), "Rosemarie DeAngelis")

    def test_surname_level_mishearing_resolves_via_fuzzy_fallback(self):
        self.assertEqual(canonical_person("Holeman", self.registry), "Claire Holman")

    def test_unknown_name_returns_none(self):
        self.assertIsNone(canonical_person("Someone Unrelated", self.registry))

    def test_empty_registry_returns_none(self):
        self.assertIsNone(canonical_person("DeAngelis", {}))


class EntitiesOfTypeTests(unittest.TestCase):
    def test_filters_by_type(self):
        registry = {
            "person:A": {"term": "A", "type": "person"},
            "place:B": {"term": "B", "type": "place"},
        }
        self.assertEqual(len(entities_of_type(registry, "person")), 1)
        self.assertEqual(len(persons(registry)), 1)


if __name__ == "__main__":
    unittest.main()
