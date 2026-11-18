import unittest
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
