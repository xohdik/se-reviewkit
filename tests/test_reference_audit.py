import unittest
import hashlib
import tempfile
from pathlib import Path
from reference_audit import audit


def record(i,doi='10.1000/test',title='Example title'):
    return dict(citekey=f'key{i}',component=f'component{i}',doi=doi,title=title,year=2026,authors=['Author'],archive='run',page='raw/1.json',row=i,page_sha256='a'*64)


class ReferenceAuditTests(unittest.TestCase):
    def test_clean(self):
        r=audit([record(1),record(2,'10.1000/other','Other title')]);self.assertEqual(r['errors'],[]);self.assertEqual(r['duplicate_dois'],{});self.assertIsNone(r['final_inclusions'])

    def test_doi_forms(self):
        r=audit([record(1),record(2,'https://doi.org/10.1000/TEST','Other title')]);self.assertEqual(r['duplicate_dois'],{'10.1000/test':['key1','key2']})

    def test_title_collision_not_merge(self):
        r=audit([record(1),record(2,'10.1000/other','EXAMPLE—TITLE')]);self.assertEqual(len(r['duplicate_titles']),1);self.assertEqual(r['automatic_merges'],0)

    def test_bad_provenance(self):
        a=record(1);a['page_sha256']='bad';a['row']=0;a['authors']=[];r=audit([a]);self.assertEqual({e['field'] for e in r['errors']},{'authors','row','page_sha256'})

    def test_duplicate_component_and_key(self):
        a=record(1);r=audit([a,a]);self.assertTrue(r['duplicate_citekeys']);self.assertTrue(r['duplicate_components'])

    def test_explicit_missing_doi(self):
        a=record(1,doi=None);self.assertTrue(audit([a])['errors'])
        a['doi_status']='not_assigned';self.assertFalse(audit([a])['errors'])

    def test_private_archive_checksum_and_path_safety(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);target=root/'run'/'raw'/'1.json';target.parent.mkdir(parents=True)
            target.write_bytes(b'{"synthetic":true}')
            a=record(1);a['page_sha256']=hashlib.sha256(target.read_bytes()).hexdigest()
            self.assertEqual(audit([a],root)['archive_pages_hashed'],1)
            self.assertFalse(audit([a],root)['errors'])
            target.write_bytes(b'changed')
            self.assertEqual(audit([a],root)['errors'][0]['issue'],'digest_mismatch')
            a['page']='../../outside.json'
            self.assertEqual(audit([a],root)['errors'][0]['issue'],'archive_page_unavailable_or_unsafe')
