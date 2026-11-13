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
