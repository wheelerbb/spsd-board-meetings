import unittest
from unittest.mock import patch

from extract_terms import _chunk, _apply_batch_results, _extract_batch


class ChunkTests(unittest.TestCase):
    def test_splits_into_groups_of_size(self):
        self.assertEqual(_chunk([1, 2, 3, 4, 5], 2), [[1, 2], [3, 4], [5]])

    def test_exact_multiple(self):
        self.assertEqual(_chunk([1, 2, 3, 4], 2), [[1, 2], [3, 4]])

    def test_empty_input(self):
        self.assertEqual(_chunk([], 5), [])

    def test_single_group_when_smaller_than_size(self):
        self.assertEqual(_chunk([1, 2], 5), [[1, 2]])


class ApplyBatchResultsTests(unittest.TestCase):
    def test_distributes_terms_onto_each_catalog_entry(self):
        catalog = {"f1": {}, "f2": {}}
        batch = [
            ("2026-01-08", "agenda", "f1", "2026-01-01T00:00:00Z", "text1"),
            ("2026-01-08", "packet", "f2", "2026-01-02T00:00:00Z", "text2"),
        ]
        by_file_id = {
            "f1": [{"term": "Claire Holman", "type": "person"}],
            "f2": [{"term": "South Portland High School", "type": "place"}],
        }
        _apply_batch_results(catalog, batch, by_file_id)
        self.assertEqual(catalog["f1"]["terms"], [{"term": "Claire Holman", "type": "person"}])
        self.assertEqual(catalog["f1"]["modified_time"], "2026-01-01T00:00:00Z")
        self.assertEqual(catalog["f2"]["terms"], [{"term": "South Portland High School", "type": "place"}])
        self.assertEqual(catalog["f2"]["modified_time"], "2026-01-02T00:00:00Z")

    def test_missing_file_id_in_response_leaves_entry_untouched(self):
        # The model dropping one document's entry from a batch response shouldn't crash the
        # whole batch or fabricate terms for it — just skip, leaving that entry exactly as it
        # was (so it's retried as a cache miss again next run).
        catalog = {"f1": {}, "f2": {"terms": ["stale"]}}
        batch = [
            ("2026-01-08", "agenda", "f1", "2026-01-01T00:00:00Z", "text1"),
            ("2026-01-08", "packet", "f2", "2026-01-02T00:00:00Z", "text2"),
        ]
        by_file_id = {"f1": [{"term": "Claire Holman", "type": "person"}]}  # f2 missing
        _apply_batch_results(catalog, batch, by_file_id)
        self.assertEqual(catalog["f1"]["terms"], [{"term": "Claire Holman", "type": "person"}])
        self.assertNotIn("modified_time", catalog["f2"])
        self.assertEqual(catalog["f2"]["terms"], ["stale"])


class ExtractBatchTests(unittest.TestCase):
    def test_parses_batch_response_keyed_by_file_id(self):
        batch = [
            ("2026-01-08", "agenda", "f1", "2026-01-01T00:00:00Z", "agenda text"),
            ("2026-01-08", "packet", "f2", "2026-01-02T00:00:00Z", "packet text"),
        ]
        canned_response = (
            '{"results": ['
            '{"file_id": "f1", "terms": [{"term": "Claire Holman", "type": "person", "context": "Board Member"}]},'
            '{"file_id": "f2", "terms": []}'
            ']}'
        )
        with patch('extract_terms.call_llm', return_value=(canned_response, 'gemini-2.5-pro')) as mock_call:
            result = _extract_batch(batch)
        self.assertTrue(mock_call.called)
        self.assertEqual(result, {
            "f1": [{"term": "Claire Holman", "type": "person", "context": "Board Member"}],
            "f2": [],
        })

    def test_call_failure_returns_empty_dict_not_exception(self):
        batch = [("2026-01-08", "agenda", "f1", "2026-01-01T00:00:00Z", "text")]
        with patch('extract_terms.call_llm', side_effect=RuntimeError("rate limited")):
            result = _extract_batch(batch)
        self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()
