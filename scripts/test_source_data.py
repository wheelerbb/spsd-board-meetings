import os
import tempfile
import unittest
from unittest.mock import patch
import yaml

from source_data import (
    merge_documents, AMBIGUOUS, _dedupe_identical_content,
    _combine_site_and_drive_docs, _resolve_url_owner, reconcile_meetings,
)


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

    def test_new_doc_owned_by_other_date_is_not_added_or_readded(self):
        # Even when a stale site listing still offers the URL in new_docs for this date,
        # url_owner assigning it to another date must both drop it from existing_docs and
        # prevent new_docs from immediately adding it right back.
        stale_url = 'https://drive.google.com/file/d/ABC/view'
        existing = [doc(stale_url, 'Budget Workshop I Agenda', 'agenda')]
        new = [doc(stale_url, 'Budget Workshop I Agenda', 'agenda')]
        url_owner = {stale_url: '2025-03-17'}
        merged, added, removed = merge_documents(existing, new, url_owner=url_owner, date_slug='2024-03-17')
        self.assertEqual(removed, 1)
        self.assertEqual(added, 0)
        self.assertEqual(merged, [])


class ResolveUrlOwnerTests(unittest.TestCase):
    def test_no_collision_each_url_owned_by_its_one_date(self):
        combined = {
            '2024-04-08': [doc('https://drive.google.com/file/d/ABC/view', 'Agenda')],
            '2024-05-13': [doc('https://drive.google.com/file/d/XYZ/view', 'Packet')],
        }
        owner = _resolve_url_owner(combined)
        self.assertEqual(owner['https://drive.google.com/file/d/ABC/view'], '2024-04-08')
        self.assertEqual(owner['https://drive.google.com/file/d/XYZ/view'], '2024-05-13')

    def test_collision_with_no_catalog_falls_back_to_ambiguous(self):
        combined = {
            '2024-01-08': [doc('https://drive.google.com/file/d/ABC/view', 'Special Meeting Agenda')],
            '2025-01-08': [doc('https://drive.google.com/file/d/ABC/view', 'Special Meeting Agenda')],
        }
        owner = _resolve_url_owner(combined)
        self.assertIs(owner['https://drive.google.com/file/d/ABC/view'], AMBIGUOUS)

    def test_collision_with_catalog_matching_neither_falls_back_to_ambiguous(self):
        combined = {
            '2024-01-08': [doc('https://drive.google.com/file/d/ABC/view', 'Special Meeting Agenda')],
            '2025-01-08': [doc('https://drive.google.com/file/d/ABC/view', 'Special Meeting Agenda')],
        }
        catalog = {'ABC': {'meeting_slug': '2026-03-17'}}  # content resolved to some third date
        owner = _resolve_url_owner(combined, catalog)
        self.assertIs(owner['https://drive.google.com/file/d/ABC/view'], AMBIGUOUS)

    def test_collision_broken_by_catalogs_content_derived_date(self):
        # Real case: a document's content has already been dated to 2025-03-17 in the catalog
        # (sourcing/drive.py::build_meeting_map), but a stale site listing also attaches the
        # same URL to 2024-03-17 this run. Content should win.
        combined = {
            '2024-03-17': [doc('https://drive.google.com/file/d/ABC/view', 'Budget Workshop I Agenda')],
            '2025-03-17': [doc('https://drive.google.com/file/d/ABC/view', 'Budget Workshop I Agenda')],
        }
        catalog = {'ABC': {'meeting_slug': '2025-03-17'}}
        owner = _resolve_url_owner(combined, catalog)
        self.assertEqual(owner['https://drive.google.com/file/d/ABC/view'], '2025-03-17')

    def test_collision_broken_regardless_of_slug_iteration_order(self):
        combined = {
            '2025-03-17': [doc('https://drive.google.com/file/d/ABC/view', 'Budget Workshop I Agenda')],
            '2024-03-17': [doc('https://drive.google.com/file/d/ABC/view', 'Budget Workshop I Agenda')],
        }
        catalog = {'ABC': {'meeting_slug': '2025-03-17'}}
        owner = _resolve_url_owner(combined, catalog)
        self.assertEqual(owner['https://drive.google.com/file/d/ABC/view'], '2025-03-17')


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


class ReconcileMeetingsSiteAuthorityTests(unittest.TestCase):
    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)

    def tearDown(self):
        os.chdir(self._orig_cwd)
        self._tmp.cleanup()

    def _read_fm(self, slug):
        with open(os.path.join('src/meetings', f'{slug}.njk')) as f:
            parts = f.read().split('---')
        return yaml.safe_load(parts[1])

    def test_site_only_date_whose_sole_doc_lost_tiebreak_does_not_create_stub(self):
        # Exact 2024-03-17 vs 2025-03-17 bug (todo 031): SPSD site matched a 2024-03-17 date
        # near a link whose Drive catalog entry resolves to 2025-03-17. 2024-03-17 must NOT be
        # created at all, while 2025-03-17 keeps the document.
        url = 'https://drive.google.com/file/d/ABC/view'
        all_data = {
            '2024-03-17': {
                'site': {'date': '2024-03-17', 'type': 'Regular', 'docs': [doc(url, 'Budget Workshop I Agenda', 'agenda')]},
            },
            '2025-03-17': {
                'events': [{'title': 'School Board Budget Workshop'}],
                'drive': [doc(url, 'Budget Workshop I Agenda', 'agenda')],
            },
        }
        catalog = {'ABC': {'meeting_slug': '2025-03-17', 'resolved_via': 'content'}}
        reconcile_meetings(all_data, catalog=catalog)

        self.assertEqual(all_data['2024-03-17']['_authority'], None)
        self.assertEqual(all_data['2024-03-17']['_stub_action'], 'skipped')
        self.assertFalse(os.path.exists('src/meetings/2024-03-17.njk'))

        self.assertEqual(all_data['2025-03-17']['_stub_action'], 'created')
        fm = self._read_fm('2025-03-17')
        self.assertEqual([d['url'] for d in fm['docs']], [url])

    def test_legitimate_site_only_meeting_without_catalog_still_creates_stub(self):
        # A genuine site-only meeting whose doc isn't in the Drive catalog must still be created.
        url = 'https://drive.google.com/file/d/SITEONLY/view'
        all_data = {
            '2025-04-14': {
                'site': {'date': '2025-04-14', 'type': 'Regular', 'docs': [doc(url, 'Agenda', 'agenda')]},
            },
        }
        reconcile_meetings(all_data, catalog={})

        self.assertEqual(all_data['2025-04-14']['_authority'], 'site')
        self.assertEqual(all_data['2025-04-14']['_stub_action'], 'created')
        fm = self._read_fm('2025-04-14')
        self.assertEqual([d['url'] for d in fm['docs']], [url])

    def test_new_stub_with_valid_event_excludes_misattributed_site_doc(self):
        # If a real Apptegy-backed meeting also has a misattributed site doc owned by another
        # date, the stub is still created (from Apptegy), but WITHOUT the misattributed doc.
        url = 'https://drive.google.com/file/d/ABC/view'
        all_data = {
            '2024-03-18': {
                'events': [{'title': 'School Board Special Meeting'}],
                'site': {'date': '2024-03-18', 'type': 'Special', 'docs': [doc(url, 'Wrong Year Agenda', 'agenda')]},
            },
            '2025-03-17': {
                'events': [{'title': 'School Board Budget Workshop'}],
                'drive': [doc(url, 'Budget Workshop I Agenda', 'agenda')],
            },
        }
        catalog = {'ABC': {'meeting_slug': '2025-03-17', 'resolved_via': 'content'}}
        reconcile_meetings(all_data, catalog=catalog)

        self.assertEqual(all_data['2024-03-18']['_authority'], 'apptegy')
        self.assertEqual(all_data['2024-03-18']['_stub_action'], 'created')
        fm = self._read_fm('2024-03-18')
        self.assertEqual(fm['docs'], [])

    def test_existing_meeting_strips_misattributed_doc_even_when_site_reoffers_it(self):
        url = 'https://drive.google.com/file/d/ABC/view'
        os.makedirs('src/meetings', exist_ok=True)
        with open('src/meetings/2024-03-17.njk', 'w') as f:
            f.write(
                "---\nlayout: layouts/meeting.njk\nstub: true\n"
                f"docs:\n- label: Budget Workshop I Agenda\n  url: {url}\n  type: agenda\n---\n"
            )
        all_data = {
            '2024-03-17': {
                'site': {'date': '2024-03-17', 'type': 'Regular', 'docs': [doc(url, 'Budget Workshop I Agenda', 'agenda')]},
            },
            '2025-03-17': {
                'events': [{'title': 'School Board Budget Workshop'}],
                'drive': [doc(url, 'Budget Workshop I Agenda', 'agenda')],
            },
        }
        catalog = {'ABC': {'meeting_slug': '2025-03-17', 'resolved_via': 'content'}}
        reconcile_meetings(all_data, catalog=catalog)

        self.assertEqual(all_data['2024-03-17']['_stub_action'], 'updated')
        fm = self._read_fm('2024-03-17')
        self.assertEqual(fm['docs'], [])


if __name__ == '__main__':
    unittest.main()
