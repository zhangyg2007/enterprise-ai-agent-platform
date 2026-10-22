import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

from enterprise_ai.document_samples import generate_corpus


class SampleTests(unittest.TestCase):
    def test_reproducible_complete_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp)/'a', Path(tmp)/'b'
            first = generate_corpus(a, formats='md')
            self.assertEqual(first, generate_corpus(b, formats='md'))
            self.assertEqual(len(first['documents']), 30)
            self.assertEqual({d['tenant_id'] for d in first['documents']}, {1, 2})
            self.assertEqual({d['domain'] for d in first['documents']}, {'crm', 'hcm', 'scm'})
            for d in first['documents']:
                self.assertEqual(hashlib.sha256((a/d['source_path']).read_bytes()).hexdigest(), d['sha256'])
                self.assertEqual((a/d['source_path']).read_bytes(), (b/d['source_path']).read_bytes())
            with self.assertRaises(FileExistsError):
                generate_corpus(a, formats='md')

    def test_bad_options_do_not_create_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)/'out'
            for options in ({'count': 0}, {'count': True}, {'count': 31}, {'formats': 'html'}):
                with self.assertRaises(ValueError):
                    generate_corpus(out, **options)
                self.assertFalse(out.exists())

    @unittest.skipUnless(all(importlib.util.find_spec(n) for n in ('docx','pypdf','reportlab')), 'optional format libraries')
    def test_mixed_bytes_and_roundtrip(self):
        from enterprise_ai.document_parse import parse_document
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp)/'a', Path(tmp)/'b'
            first = generate_corpus(a)
            self.assertEqual(first, generate_corpus(b))
            self.assertEqual({Path(d['source_path']).suffix for d in first['documents']}, {'.md','.pdf','.docx'})
            for d in first['documents']:
                path = a/d['source_path']
                blocks = parse_document(path.read_bytes(), path.suffix)
                self.assertIn('合成', '\n'.join(s['text'] for s in blocks))
                self.assertIn(d['document_id'], '\n'.join(s['text'] for s in blocks))


if __name__ == '__main__':
    unittest.main()
