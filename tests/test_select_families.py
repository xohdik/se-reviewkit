import json
import tempfile
import unittest
from pathlib import Path

from select_families import select, main


def frame():
    return dict(eligibility_complete=True,family_adjudication_complete=True,frame_frozen=True,
                protocol_author_approved=True,coordinator='synthetic',freeze_date='2025-01-01',
                adjudication_evidence_sha256='a'*64,
                families=[dict(family_id=f'f{i}',mechanism='test' if i<7 else 'proof',venue_stratum='synthetic',
                    preferred_report_id=f'r{i}',eligibility_adjudication_locator='fixture:1',
                    family_adjudication_locator='fixture:2') for i in range(10)])


class FamilySelectionTests(unittest.TestCase):
    def test_exact_proportional_allocation(self):
        result=select(frame(),'frozen-seed',4)
        self.assertEqual(result['selected_family_count'],4)
        self.assertEqual({r['mechanism']:r['selected'] for r in result['strata']},{'test':3,'proof':1})
        self.assertTrue(all(r['replacement_allowed'] is False for r in result['selected_families']))

    def test_order_independence_and_census(self):
        a=frame(); b=frame();b['families'].reverse()
        self.assertEqual(select(a,'seed',4),select(b,'seed',4))
        self.assertEqual(select(a,'seed',25)['selected_family_count'],10)

    def test_gates(self):
        for key in ('eligibility_complete','family_adjudication_complete','frame_frozen','protocol_author_approved'):
            a=frame();a[key]=False
            with self.assertRaisesRegex(ValueError,'STOP'):
                select(a,'seed',4)

    def test_invalid_date_digest_and_duplicate(self):
        for key,value in [('freeze_date','2025-02-30'),('adjudication_evidence_sha256','not hash'),('coordinator','')]:
            a=frame();a[key]=value
            with self.assertRaises(ValueError):
                select(a,'seed',4)
        a=frame();a['families'].append(a['families'][0])
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            select(a,'seed',4)

    def test_cli_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            src=Path(temp)/'frame.json';out=Path(temp)/'selection.json'
            src.write_text(json.dumps(frame()),encoding='utf-8'); args=[str(src),str(out),'--seed','seed','--target','4']
            main(args); before=out.read_bytes()
            self.assertEqual(len(json.loads(before)['frame_sha256']),64)
            with self.assertRaises(SystemExit):
                main(args)
            self.assertEqual(out.read_bytes(),before)
