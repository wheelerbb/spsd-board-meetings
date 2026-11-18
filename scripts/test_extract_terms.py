import unittest
from unittest.mock import patch, MagicMock

from googleapiclient.errors import HttpError

from extract_terms import _chunk, _apply_batch_results, _extract_batch, _ensure_cache_metadata


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


def _svc(execute_return=None, execute_side_effect=None):
    svc = MagicMock()
    get = svc.files.return_value.get
    if execute_side_effect is not None:
        get.return_value.execute.side_effect = execute_side_effect
    else:
        get.return_value.execute.return_value = execute_return
    return svc


def _http_error(status, message="error"):
    resp = MagicMock(status=status)
    content = ('{"error": {"message": "%s"}}' % message).encode('utf-8')
    return HttpError(resp, content)


class EnsureCacheMetadataTests(unittest.TestCase):
    def test_trusts_catalog_metadata_without_a_drive_call(self):
        # source_data.py's folder scan already keeps modified_time current for every in-scope
        # file — re-fetching it here would just duplicate a call made moments ago in the same job.
        catalog = {"f1": {"modified_time": "2026-01-01T00:00:00Z", "mime_type": "application/pdf"}}
        svc = _svc()
        result = _ensure_cache_metadata(catalog, svc, "f1", "agenda")
        self.assertEqual(result, "2026-01-01T00:00:00Z")
        svc.files.return_value.get.assert_not_called()
        self.assertEqual(catalog["f1"]["doc_type"], "agenda")

    def test_fetches_from_drive_when_catalog_entry_incomplete(self):
        catalog = {}
        svc = _svc(execute_return={
            "modifiedTime": "2026-02-01T00:00:00Z",
            "mimeType": "application/pdf",
            "createdTime": "2025-01-01T00:00:00Z",
        })
        result = _ensure_cache_metadata(catalog, svc, "f2", "packet")
        self.assertEqual(result, "2026-02-01T00:00:00Z")
        self.assertEqual(catalog["f2"]["mime_type"], "application/pdf")
        self.assertEqual(catalog["f2"]["doc_type"], "packet")

    def test_404_flags_no_access_and_returns_none(self):
        catalog = {}
        svc = _svc(execute_side_effect=_http_error(404, "File not found"))
        result = _ensure_cache_metadata(catalog, svc, "f3", "minutes")
        self.assertIsNone(result)
        self.assertEqual(catalog["f3"]["access"], "no_access")

    def test_no_access_entry_skips_drive_call_on_a_later_run(self):
        catalog = {"f3": {"access": "no_access"}}
        svc = _svc()
        result = _ensure_cache_metadata(catalog, svc, "f3", "minutes")
        self.assertIsNone(result)
        svc.files.return_value.get.assert_not_called()

    def test_non_404_error_warns_but_does_not_flag_no_access(self):
        catalog = {}
        svc = _svc(execute_side_effect=_http_error(500, "Server error"))
        result = _ensure_cache_metadata(catalog, svc, "f4", "agenda")
        self.assertIsNone(result)
        self.assertNotIn("f4", catalog)


if __name__ == "__main__":
    unittest.main()
