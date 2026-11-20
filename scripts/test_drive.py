import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sourcing.drive import (
    extract_date_from_content, text_cache_fresh, get_or_extract_text,
    build_meeting_map, TEXT_EXTRACTOR_VERSION,
)

TODAY = datetime.utcnow().strftime('%Y-%m-%d')
YESTERDAY = (datetime.utcnow() - timedelta(days=1)).strftime('%Y-%m-%d')
DOC_MIME = 'application/vnd.google-apps.document'
PDF_MIME = 'application/pdf'


class ExtractDateFromContentTests(unittest.TestCase):
    def test_squished_header_date_beats_later_wellformed_reference(self):
        # Real pattern: PDF extraction drops the space in the header's own date ("MAY13") while
        # leaving normal spacing on a later, merely-referenced date ("April 8, 2024").
        text = (
            "BOARDOFEDUCATIONMEETINGAGENDA\nREGULARMEETING\nMONDAY, MAY13, 2024\n6:00P.M.\n"
            "...Consent Agenda...\n"
            "1. Approval of the minutes from the Regular Meeting held on Monday, April 8, 2024."
        )
        self.assertEqual(extract_date_from_content(text), "2024-05-13")

    def test_minutes_preamble_picks_own_date_not_approval_date(self):
        # Real pattern: minutes state their own meeting date, then the future meeting where
        # they'll be approved — both properly spaced, own-date must win despite squished joins.
        text = (
            "BOARDOFEDUCATION\nSOUTHPORTLAND, MAINE\n"
            "Thefollowingisabrief summary...at itsRegular MeetingonMonday, March11, 2024. "
            "Pleasenotethat theminuteswill not befinal until approvedat thenext Regular "
            "MeetingonApril 8, 2024."
        )
        self.assertEqual(extract_date_from_content(text), "2024-03-11")

    def test_normal_wellformed_header(self):
        text = "Regular Meeting\nMonday, June 10, 2024\n6:00 PM\nAgenda..."
        self.assertEqual(extract_date_from_content(text), "2024-06-10")

    def test_date_past_header_window_not_used(self):
        text = "x" * 700 + " Monday, June 10, 2024"
        self.assertIsNone(extract_date_from_content(text))

    def test_no_date_returns_none(self):
        self.assertIsNone(extract_date_from_content("No dates here at all."))

    def test_empty_text_returns_none(self):
        self.assertIsNone(extract_date_from_content(""))
        self.assertIsNone(extract_date_from_content(None))


class TextCacheFreshTests(unittest.TestCase):
    def test_exact_modified_time_match_is_fresh(self):
        entry = {'modified_time': 'v1', 'extractor_version': TEXT_EXTRACTOR_VERSION}
        self.assertTrue(text_cache_fresh(entry, PDF_MIME, 'v1'))

    def test_changed_modified_time_on_uploaded_file_is_not_fresh(self):
        entry = {'modified_time': 'v1', 'extractor_version': TEXT_EXTRACTOR_VERSION}
        self.assertFalse(text_cache_fresh(entry, PDF_MIME, 'v2'))

    def test_native_doc_edited_today_still_fresh(self):
        # Native Doc modifiedTime bumped by an autosave mid-draft -- today's extraction should
        # still be trusted rather than re-running (and, for terms, re-billing) on every pipeline
        # run while someone is mid-edit.
        entry = {'modified_time': 'v1', 'extractor_version': TEXT_EXTRACTOR_VERSION, 'extracted_at': TODAY}
        self.assertTrue(text_cache_fresh(entry, DOC_MIME, 'v2'))

    def test_native_doc_edited_yesterday_is_stale(self):
        entry = {'modified_time': 'v1', 'extractor_version': TEXT_EXTRACTOR_VERSION, 'extracted_at': YESTERDAY}
        self.assertFalse(text_cache_fresh(entry, DOC_MIME, 'v2'))

    def test_native_doc_grace_does_not_apply_to_uploaded_files(self):
        entry = {'modified_time': 'v1', 'extractor_version': TEXT_EXTRACTOR_VERSION, 'extracted_at': TODAY}
        self.assertFalse(text_cache_fresh(entry, PDF_MIME, 'v2'))

    def test_stale_extractor_version_is_never_fresh(self):
        entry = {'modified_time': 'v1', 'extractor_version': TEXT_EXTRACTOR_VERSION - 1, 'extracted_at': TODAY}
        self.assertFalse(text_cache_fresh(entry, PDF_MIME, 'v1'))
        self.assertFalse(text_cache_fresh(entry, DOC_MIME, 'v1'))


class GetOrExtractTextUnsupportedMimeTests(unittest.TestCase):
    def test_slides_file_never_attempts_extraction(self):
        catalog = {'f1': {'mime_type': 'application/vnd.google-apps.presentation'}}
        text, updates = get_or_extract_text('gs://bucket', object(), catalog, 'f1')
        self.assertIsNone(text)
        self.assertIsNone(updates)

    def test_sheets_file_never_attempts_extraction(self):
        catalog = {'f1': {'mime_type': 'application/vnd.google-apps.spreadsheet'}}
        text, updates = get_or_extract_text(None, None, catalog, 'f1')
        self.assertIsNone(text)
        self.assertIsNone(updates)


class BuildMeetingMapManualOverrideTests(unittest.TestCase):
    def _drive_file(self, file_id='f1', name='REVISED 01.08.25 Special Meeting Agenda.pdf',
                    mime_type=PDF_MIME, modified='2025-01-08T12:00:00Z'):
        return {
            'id': file_id,
            'name': name,
            'mimeType': mime_type,
            'webViewLink': f'https://drive.google.com/file/d/{file_id}/view',
            'createdTime': '2025-01-07T12:00:00Z',
            'modifiedTime': modified,
        }

    def test_manual_override_survives_extractor_version_bump_and_refreshes_text(self):
        # Real case (todo 031): document's own header says 2024, but it belongs to 2025-01-08.
        # A TEXT_EXTRACTOR_VERSION bump forces re-extraction; text_blob/extractor_version must
        # refresh, while meeting_slug, resolved_via, and resolved_via_note stay untouched.
        catalog = {
            'f1': {
                'meeting_slug': '2025-01-08',
                'resolved_via': 'manual',
                'resolved_via_note': 'Document header erroneously says 2024',
                'modified_time': '2025-01-08T12:00:00Z',
                'extractor_version': TEXT_EXTRACTOR_VERSION - 1,
                'text_blob': 'official_docs/old.txt',
            }
        }
        wrong_content = "Special Meeting\nWednesday, January 8, 2024\n6:00 PM\nAgenda"
        with patch('sourcing.drive.read_file_text', return_value=wrong_content) as mock_read, \
             patch('sourcing.drive.cache_text', return_value='official_docs/new.txt') as mock_cache:
            build_meeting_map([self._drive_file()], catalog, bucket_uri='gs://bucket', service=object())

        mock_read.assert_called_once()
        mock_cache.assert_called_once()
        self.assertEqual(catalog['f1']['meeting_slug'], '2025-01-08')
        self.assertEqual(catalog['f1']['resolved_via'], 'manual')
        self.assertEqual(catalog['f1']['resolved_via_note'], 'Document header erroneously says 2024')
        self.assertEqual(catalog['f1']['text_blob'], 'official_docs/new.txt')
        self.assertEqual(catalog['f1']['extractor_version'], TEXT_EXTRACTOR_VERSION)
        self.assertEqual(catalog['f1']['processing'], 'text_extracted')

    def test_manual_override_survives_modified_time_change(self):
        catalog = {
            'f1': {
                'meeting_slug': '2025-01-08',
                'resolved_via': 'manual',
                'modified_time': '2025-01-08T12:00:00Z',
                'extractor_version': TEXT_EXTRACTOR_VERSION,
            }
        }
        wrong_content = "Special Meeting\nWednesday, January 8, 2024\n6:00 PM\nAgenda"
        with patch('sourcing.drive.read_file_text', return_value=wrong_content), \
             patch('sourcing.drive.cache_text', return_value='official_docs/v2.txt'):
            build_meeting_map(
                [self._drive_file(modified='2025-01-09T12:00:00Z')],
                catalog, bucket_uri='gs://bucket', service=object()
            )

        self.assertEqual(catalog['f1']['meeting_slug'], '2025-01-08')
        self.assertEqual(catalog['f1']['resolved_via'], 'manual')
        self.assertEqual(catalog['f1']['modified_time'], '2025-01-09T12:00:00Z')
        self.assertEqual(catalog['f1']['text_blob'], 'official_docs/v2.txt')

    def test_manual_override_survives_on_non_extractable_file(self):
        catalog = {
            'f1': {
                'meeting_slug': '2025-01-08',
                'resolved_via': 'manual',
                'modified_time': '2025-01-08T12:00:00Z',
            }
        }
        build_meeting_map(
            [self._drive_file(name='01.08.24 Handout.pdf', modified='2025-01-09T12:00:00Z')],
            catalog, bucket_uri='gs://bucket', service=object()
        )
        self.assertEqual(catalog['f1']['meeting_slug'], '2025-01-08')
        self.assertEqual(catalog['f1']['resolved_via'], 'manual')
        self.assertEqual(catalog['f1']['modified_time'], '2025-01-09T12:00:00Z')

    def test_non_manual_entry_still_updates_date_on_reextraction(self):
        catalog = {
            'f1': {
                'meeting_slug': '2024-01-08',
                'resolved_via': 'content',
                'modified_time': '2025-01-08T12:00:00Z',
                'extractor_version': TEXT_EXTRACTOR_VERSION - 1,
            }
        }
        fixed_content = "Special Meeting\nWednesday, January 8, 2025\n6:00 PM\nAgenda"
        with patch('sourcing.drive.read_file_text', return_value=fixed_content), \
             patch('sourcing.drive.cache_text', return_value='official_docs/new.txt'):
            build_meeting_map([self._drive_file()], catalog, bucket_uri='gs://bucket', service=object())

        self.assertEqual(catalog['f1']['meeting_slug'], '2025-01-08')
        self.assertEqual(catalog['f1']['resolved_via'], 'content')


if __name__ == "__main__":
    unittest.main()
