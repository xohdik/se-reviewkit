import unittest
from screening_agreement import evaluate


def rows(labels,reviewer):
    return [{'component':str(i),'decision':x,'reviewer_id':reviewer if x else '', 'reason_code':'test' if x else ''} for i,x in enumerate(labels)]


class ScreeningAgreementTests(unittest.TestCase):
    def test_known_matrix(self):
        a=rows(['include','exclude','uncertain',''], 'A'); b=rows(['include','uncertain','uncertain','exclude'], 'B')
        r=evaluate(a,b);self.assertEqual(r['complete_pairs'],3);self.assertAlmostEqual(r['observed_agreement'],2/3);self.assertAlmostEqual(r['cohens_kappa'],.5);self.assertEqual(r['disagreement_ids'],['1']);self.assertEqual(r['pending_ids'],['3']);self.assertIsNone(r['final_inclusions'])
    def test_empty_and_degenerate(self):
        self.assertIsNone(evaluate(rows([''],'A'),rows([''],'B'))['cohens_kappa']);self.assertIsNone(evaluate(rows(['exclude'],'A'),rows(['exclude'],'B'))['cohens_kappa'])
    def test_independent_matching(self):
        a=rows(['include','exclude'],'A');b=rows(['exclude','include'],'B'); self.assertEqual(evaluate(a,list(reversed(b)))['complete_pairs'],2)
        for wrong in (rows(['include'],'B'),rows(['include','exclude'],'A'),rows(['include','bogus'],'B')):
            with self.assertRaises(ValueError):evaluate(a,wrong)
    def test_requires_reason(self):
        a=rows(['include'],'A');a[0]['reason_code']=''
        with self.assertRaises(ValueError):evaluate(a,rows(['include'],'B'))


if __name__=='__main__':unittest.main()
