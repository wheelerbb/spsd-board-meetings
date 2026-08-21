import unittest
from unittest.mock import patch

from source_data import merge_documents, AMBIGUOUS, _dedupe_identical_content, _combine_site_and_drive_docs


def doc(url, label, type_='packet'):
    return {'type': type_, 'label': label, 'url': url}


class CombineSiteAndDriveDocsTests(unittest.TestCase):
    def test_drive_type_wins_on_collision(self):
        site_docs = [doc('https://drive.google.com/file/d/ABC/view', 'August Meeting Packet', 'minutes')]
        drive_docs = [doc('https://drive.google.com/file/d/ABC/view', 'August 2026 Board Meeting Packet - Revised', 'packet')]
        combined = _combine_site_and_drive_docs(site_docs, drive_docs)
        self.assertEqual(len(combined), 1)
        self.assertEqual(combined[0]['type'], 'packet')
        self.assertEqual(combined[0]['label'], 'August 2026 Board Meeting Packet - Revised')

    def test_drive_only_url_included(self):
        drive_docs = [doc('https://drive.google.com/file/d/XYZ/view', 'Agenda', 'agenda')]
        combined = _combine_site_and_drive_docs([], drive_docs)
        self.assertEqual(combined, drive_docs)

    def test_site_only_url_included_as_fallback(self):
        site_docs = [doc('https://drive.google.com/file/d/QRS/view', 'Newly Posted Doc', 'misc')]
        combined = _combine_site_and_drive_docs(site_docs, [])
        self.assertEqual(combined, site_docs)


class MergeDocumentsTests(unittest.TestCase):
    def test_same_date_collision_new_wins(self):
        existing = [doc('https://drive.google.com/file/d/ABC/view', 'April Meeting Summary', 'pdf')]
        new = [doc('https://drive.google.com/file/d/ABC/view', 'School Board Minutes', 'minutes')]
        merged, added, removed = merge_documents(existing, new, url_owner={'https://drive.google.com/file/d/ABC/view': '2024-04-08'}, date_slug='2024-04-08')
        self.assertEqual(added, 0)
        self.assertEqual(removed, 0)
        self.assertEqual(merged, [doc('https://drive.google.com/file/d/ABC/view', 'School Board Minutes', 'minutes')])

    def test_cross_date_reassignment_removed(self):
        existing = [
            doc('https://drive.google.com/file/d/ABC/view', 'School Board Agenda', 'agenda'),
            doc('https://drive.google.com/file/d/XYZ/view', 'Revised Meeting Packet', 'packet'),
        ]
        new = [doc('https://drive.google.com/file/d/ABC/view', 'School Board Agenda', 'agenda')]
        url_owner = {
            'https://drive.google.com/file/d/ABC/view': '2024-04-08',
            'https://drive.google.com/file/d/XYZ/view': '2024-05-13',  # claimed by a different meeting
        }
        merged, added, removed = merge_documents(existing, new, url_owner=url_owner, date_slug='2024-04-08')
        self.assertEqual(removed, 1)
        self.assertEqual([d['url'] for d in merged], ['https://drive.google.com/file/d/ABC/view'])

    def test_ambiguous_url_left_alone(self):
        existing = [doc('https://drive.google.com/file/d/ABC/view', 'Meeting Packet', 'packet')]
        url_owner = {'https://drive.google.com/file/d/ABC/view': AMBIGUOUS}
        merged, added, removed = merge_documents(existing, [], url_owner=url_owner, date_slug='2024-04-08')
        self.assertEqual(removed, 0)
        self.assertEqual(len(merged), 1)

    def test_absent_url_left_alone(self):
        existing = [doc('https://drive.google.com/file/d/ABC/view', 'Meeting Packet', 'packet')]
        # url_owner covers other dates but says nothing about this URL at all.
        url_owner = {'https://drive.google.com/file/d/OTHER/view': '2024-05-13'}
        merged, added, removed = merge_documents(existing, [], url_owner=url_owner, date_slug='2024-04-08')
        self.assertEqual(removed, 0)
        self.assertEqual(len(merged), 1)

    def test_no_url_owner_behaves_purely_additively(self):
        existing = [doc('https://drive.google.com/file/d/ABC/view', 'Agenda', 'agenda')]
        new = [doc('https://drive.google.com/file/d/XYZ/view', 'Packet', 'packet')]
        merged, added, removed = merge_documents(existing, new)
        self.assertEqual(added, 1)
        self.assertEqual(removed, 0)
        self.assertEqual(len(merged), 2)


def _fake_read_cached_blob(text_by_blob):
    def _read(bucket_uri, blob_name, max_chars=None):
        return text_by_blob.get(blob_name)
    return _read


class DedupeIdenticalContentTests(unittest.TestCase):
    def test_same_type_identical_text_drops_duplicate(self):
        docs = [
            doc('https://drive.google.com/file/d/A/view', 'School Board Minutes', 'minutes'),
            doc('https://drive.google.com/file/d/B/view', 'School Board Minutes', 'minutes'),
        ]
        catalog = {'A': {'text_blob': 'blobA'}, 'B': {'text_blob': 'blobB'}}
        texts = {'blobA': 'same content', 'blobB': 'same content'}
        with patch('source_data.drive.read_cached_blob', _fake_read_cached_blob(texts)):
            result, removed = _dedupe_identical_content(docs, catalog, 'gs://bucket')
        self.assertEqual(removed, 1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['url'], 'https://drive.google.com/file/d/A/view')

    def test_same_type_different_text_keeps_both(self):
        docs = [
            doc('https://drive.google.com/file/d/A/view', 'January Minutes', 'minutes'),
            doc('https://drive.google.com/file/d/B/view', 'February Minutes', 'minutes'),
        ]
        catalog = {'A': {'text_blob': 'blobA'}, 'B': {'text_blob': 'blobB'}}
        texts = {'blobA': 'january content', 'blobB': 'february content'}
        with patch('source_data.drive.read_cached_blob', _fake_read_cached_blob(texts)):
            result, removed = _dedupe_identical_content(docs, catalog, 'gs://bucket')
        self.assertEqual(removed, 0)
        self.assertEqual(len(result), 2)

    def test_different_type_identical_text_never_merged(self):
        docs = [
            doc('https://drive.google.com/file/d/A/view', 'Agenda', 'agenda'),
            doc('https://drive.google.com/file/d/B/view', 'Packet', 'packet'),
        ]
        catalog = {'A': {'text_blob': 'blobA'}, 'B': {'text_blob': 'blobB'}}
        texts = {'blobA': 'same content', 'blobB': 'same content'}
        with patch('source_data.drive.read_cached_blob', _fake_read_cached_blob(texts)):
            result, removed = _dedupe_identical_content(docs, catalog, 'gs://bucket')
        self.assertEqual(removed, 0)
        self.assertEqual(len(result), 2)

    def test_no_bucket_uri_is_noop(self):
        docs = [
            doc('https://drive.google.com/file/d/A/view', 'Minutes', 'minutes'),
            doc('https://drive.google.com/file/d/B/view', 'Minutes', 'minutes'),
        ]
        with patch('source_data.drive.read_cached_blob') as mock_read:
            result, removed = _dedupe_identical_content(docs, {}, '')
        mock_read.assert_not_called()
        self.assertEqual(removed, 0)
        self.assertEqual(result, docs)

    def test_single_doc_of_type_skips_lookup_entirely(self):
        docs = [doc('https://drive.google.com/file/d/A/view', 'Minutes', 'minutes')]
        with patch('source_data.drive.read_cached_blob') as mock_read:
            result, removed = _dedupe_identical_content(docs, {'A': {'text_blob': 'blobA'}}, 'gs://bucket')
        mock_read.assert_not_called()
        self.assertEqual(removed, 0)
        self.assertEqual(result, docs)


if __name__ == '__main__':
    unittest.main()
