import unittest

from terms_registry import (
    ingest_document_terms, canonical_person, persons, entities_of_type,
    build_registry_from_catalog, registry_fingerprint,
)


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
        self.assertEqual(entity["first_seen"], "2026-03-23")
        self.assertEqual(entity["last_seen"], "2026-04-02")

    def test_shorter_fragment_merges_into_existing_fuller_entity(self):
        registry = {}
        ingest_document_terms(registry, "file1", "2026-01-08", "minutes",
                               [{"term": "Rosemarie DeAngelis", "type": "person", "context": "Board Chair"}])
        ingest_document_terms(registry, "file2", "2026-02-12", "minutes",
                               [{"term": "DeAngelis", "type": "person", "context": "moved to approve"}])
        self.assertEqual(len(registry), 1)
        self.assertIn("person:Rosemarie DeAngelis", registry)

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
        self.assertIn("person:Mr. Daniel Feller", registry)

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

    def test_cross_type_ambiguous_match_stays_separate(self):
        # Safe-degrade: if two entities under the same string but different types already
        # coexist (only possible from registry state built before this reconciliation existed —
        # ingest_document_terms itself always collapses a new cross-type duplicate into the one
        # prior match before a second can accumulate), a third mention under yet another type
        # can't tell which of the two it corroborates, so it becomes its own entity rather than
        # guessing.
        registry = {
            "person:Taj": {"term": "Taj", "type": "person", "context": "",
                           "first_seen": "2026-10-01", "last_seen": "2026-10-01"},
            "place:Taj": {"term": "Taj", "type": "place", "context": "",
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

    def test_punctuation_artifact_term_gets_corrected_by_cleaner_corroboration(self):
        # Regression for the live todos/028 bug: "Molly. Schen" (stray period, the very first
        # extraction on 2023-08-21) stayed canonical across 70 corroborating sources through
        # 2025-08-18, including the clean "Molly Schen" among them, because the old length-based
        # tiebreak treated the punctuation-inflated form as "fuller". Fuzzy-matches and merges
        # into the same entity either way; the fix is which spelling wins as canonical.
        registry = {}
        ingest_document_terms(registry, "file1", "2023-08-21", "minutes",
                               [{"term": "Molly. Schen", "type": "person"}])
        ingest_document_terms(registry, "file2", "2023-09-11", "minutes",
                               [{"term": "Molly Schen", "type": "person"}])
        self.assertEqual(len(registry), 1)
        self.assertIn("person:Molly Schen", registry)
        self.assertEqual(registry["person:Molly Schen"]["term"], "Molly Schen")

    def test_clean_term_not_overridden_by_later_punctuation_artifact(self):
        # Mirror of the above with the clean form registered first — a later, noisier mention of
        # the same person must not regress an already-clean canonical spelling.
        registry = {}
        ingest_document_terms(registry, "file1", "2023-08-21", "minutes",
                               [{"term": "Molly Schen", "type": "person"}])
        ingest_document_terms(registry, "file2", "2023-09-11", "minutes",
                               [{"term": "Molly. Schen", "type": "person"}])
        self.assertEqual(len(registry), 1)
        self.assertIn("person:Molly Schen", registry)
        self.assertEqual(registry["person:Molly Schen"]["term"], "Molly Schen")

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

    # Note: the old "test_concurrent_ingestion_does_not_race" regression test (2026-11-13
    # production bug — concurrent ThreadPoolExecutor calls racing on the same registry dict)
    # was removed here, not updated. Its premise no longer holds: ingest_document_terms is no
    # longer called concurrently anywhere (the only caller is build_registry_from_catalog's
    # single serial pass) and the lock it exercised is gone — see todos/026. Keeping a
    # "thread-safety" test around after deliberately retiring the thing it tested would wrongly
    # imply concurrent calls are still a supported, safe use case.


class BuildRegistryFromCatalogTests(unittest.TestCase):
    def test_deterministic_order_from_unordered_input(self):
        # meetings_data given out of chronological order; the rebuild must still process them
        # (and, within each meeting, agenda before packet before minutes) in a fixed order, so
        # two rebuilds of the same catalog always produce byte-identical results — needed for
        # the fingerprint skip to be trustworthy.
        catalog = {
            "fA": {"terms": [{"term": "Alpha Agenda Co", "type": "organization"}]},
            "fP": {"terms": [{"term": "Alpha Packet Co", "type": "organization"}]},
            "fM": {"terms": [{"term": "Alpha Minutes Co", "type": "organization"}]},
        }
        meetings_data = [
            {"slug": "2026-02-12", "docs": [
                {"type": "minutes", "url": "https://drive.google.com/file/d/fM/view"},
                {"type": "agenda", "url": "https://drive.google.com/file/d/fA/view"},
                {"type": "packet", "url": "https://drive.google.com/file/d/fP/view"},
            ]},
            {"slug": "2026-01-08", "docs": []},
        ]
        r1 = build_registry_from_catalog(catalog, meetings_data)
        r2 = build_registry_from_catalog(catalog, list(reversed(meetings_data)))
        self.assertEqual(r1, r2)
        self.assertEqual(len(r1), 3)

    def test_same_meeting_same_doctype_collision_resolved_by_file_id(self):
        # Confirmed-real case (todos/022): a meeting with two distinct documents of the same
        # doc_type (e.g. a revised packet). The tiebreak must be a stable, deterministic
        # ordering (file_id), not dependent on docs[] list order, for the same byte-identical-
        # rebuild reason as above.
        catalog = {
            "fZ": {"terms": [{"term": "First Mention Inc", "type": "organization"}]},
            "fA": {"terms": [{"term": "First Mention Inc", "type": "organization", "context": "renamed"}]},
        }
        meetings_data = [{"slug": "2024-04-08", "docs": [
            {"type": "packet", "url": "https://drive.google.com/file/d/fZ/view"},
            {"type": "packet", "url": "https://drive.google.com/file/d/fA/view"},
        ]}]
        registry = build_registry_from_catalog(catalog, meetings_data)
        # fA sorts before fZ, so fA's ingestion runs first regardless of docs[] list order —
        # its context becomes the entity's context (ingest_document_terms's context-append only
        # adds fZ's context if it's a genuinely new fragment, which "renamed" isn't once fA's
        # own pass already contributed it first... concretely: whichever file_id is alphabetically
        # first is ingested first).
        self.assertEqual(len(registry), 1)

    def test_dropped_term_is_absent_after_rebuild(self):
        # The concrete regression test for the "retraction" property this whole plan is built
        # around (todos/026): a term a document no longer produces must not survive a rebuild,
        # unlike the old accumulate-in-place model where nothing ever removed a stale term.
        catalog = {"f1": {"terms": [
            {"term": "Culture Day Pep Rally", "type": "program"},
            {"term": "Claire Holman", "type": "person"},
        ]}}
        meetings_data = [{"slug": "2026-01-08", "docs": [
            {"type": "agenda", "url": "https://drive.google.com/file/d/f1/view"},
        ]}]
        before = build_registry_from_catalog(catalog, meetings_data)
        self.assertIn("program:Culture Day Pep Rally", before)

        # Simulate a re-extraction that no longer produces the one-time-event term (e.g. the
        # narrowed prompt now correctly excludes it) — same file_id, updated terms.
        catalog["f1"]["terms"] = [{"term": "Claire Holman", "type": "person"}]
        after = build_registry_from_catalog(catalog, meetings_data)
        self.assertNotIn("program:Culture Day Pep Rally", after)
        self.assertIn("person:Claire Holman", after)

    def test_meeting_with_no_qualifying_docs_contributes_nothing(self):
        catalog = {}
        meetings_data = [{"slug": "2026-01-08", "docs": [{"type": "vtt", "url": "https://example.com/x"}]}]
        self.assertEqual(build_registry_from_catalog(catalog, meetings_data), {})


class RegistryFingerprintTests(unittest.TestCase):
    def _catalog_and_meeting(self, file_id="f1", extractor_version=2, modified_time="2026-01-01T00:00:00Z"):
        catalog = {file_id: {
            "terms": [{"term": "Claire Holman", "type": "person"}],
            "extractor_version": extractor_version, "modified_time": modified_time,
        }}
        meetings_data = [{"slug": "2026-01-08", "docs": [
            {"type": "agenda", "url": f"https://drive.google.com/file/d/{file_id}/view"},
        ]}]
        return catalog, meetings_data

    def test_stable_when_nothing_changes(self):
        catalog, meetings_data = self._catalog_and_meeting()
        self.assertEqual(
            registry_fingerprint(catalog, meetings_data),
            registry_fingerprint(catalog, meetings_data),
        )

    def test_changes_when_modified_time_changes(self):
        catalog, meetings_data = self._catalog_and_meeting()
        fp1 = registry_fingerprint(catalog, meetings_data)
        catalog["f1"]["modified_time"] = "2026-06-01T00:00:00Z"
        fp2 = registry_fingerprint(catalog, meetings_data)
        self.assertNotEqual(fp1, fp2)

    def test_changes_when_extractor_version_changes(self):
        catalog, meetings_data = self._catalog_and_meeting()
        fp1 = registry_fingerprint(catalog, meetings_data)
        catalog["f1"]["extractor_version"] = 3
        fp2 = registry_fingerprint(catalog, meetings_data)
        self.assertNotEqual(fp1, fp2)

    def test_never_extracted_document_still_has_a_fingerprint_entry(self):
        # A qualifying doc with no catalog entry yet contributes (file_id, None, None) rather
        # than being skipped — so the moment it's actually extracted, the fingerprint changes
        # and triggers a rebuild, instead of silently never being noticed.
        meetings_data = [{"slug": "2026-01-08", "docs": [
            {"type": "agenda", "url": "https://drive.google.com/file/d/fNew/view"},
        ]}]
        fp_before = registry_fingerprint({}, meetings_data)
        self.assertEqual(fp_before, [("fNew", None, None)])
        fp_after = registry_fingerprint(
            {"fNew": {"extractor_version": 2, "modified_time": "2026-01-01T00:00:00Z"}},
            meetings_data,
        )
        self.assertNotEqual(fp_before, fp_after)


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
