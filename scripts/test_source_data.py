import unittest

from source_data import merge_documents, AMBIGUOUS


def doc(url, label, type_='packet'):
    return {'type': type_, 'label': label, 'url': url}


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


if __name__ == '__main__':
    unittest.main()
