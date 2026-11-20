import csv
import json
import tempfile
import unittest
from pathlib import Path

from review_calibration import SCREEN_META, SCREEN_RESP, CLAIM_META, CLAIM_RESP, freeze_manifest, audit


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.a=Path(self.tmp.name)/'a';self.b=Path(self.tmp.name)/'b'
        for directory in (self.a,self.b):
            directory.mkdir()
            for name,(key,cols) in SCREEN_META.items():
                self.write_screen(directory,name,key,cols)
            for name in ('calibration.jsonl','remaining.jsonl'):
                (directory/name).write_text(json.dumps(dict({k:'synthetic' for k in CLAIM_META},claim_id=name))+'\n',encoding='utf-8')
        self.frozen=freeze_manifest(self.a)

    def write_screen(self, directory, name, key, cols, **response):
        with (directory/name).open('w',encoding='utf-8',newline='') as file:
            w=csv.DictWriter(file,fieldnames=(*cols,*SCREEN_RESP));w.writeheader()
            w.writerow(dict({k:'synthetic' for k in cols},**response))

    def respond(self, directory, name, reviewer, decision='include', rationale='synthetic reason'):
        key,cols=SCREEN_META[name]
        self.write_screen(directory,name,key,cols,reviewer_id=reviewer,decision=decision,
                          reason_code='POTENTIAL_SCOPE' if decision=='include' else 'OUTSIDE_SOFTWARE',
                          rationale=rationale,reviewed_utc='2025-01-01T12:00:00Z')

    def test_blank_reviews_not_complete(self):
        report,s,c=audit(self.a,self.b,self.frozen)
        self.assertEqual(report['complete_eligibility_pairs'],0)
        self.assertIsNone(report['human_screening_final_included'])
        self.assertEqual((len(s),len(c)),(2,1))

    def test_complete_disagreement_and_invalid_queue(self):
        name='screening_original.csv'; self.respond(self.a,name,'A');self.respond(self.b,name,'B','exclude')
        self.respond(self.a,'screening_supplementary.csv','A',rationale='')
        report,s,_=audit(self.a,self.b,self.frozen)
        self.assertEqual(report['complete_eligibility_pairs'],1)
        self.assertEqual(len(s),2)
        self.assertEqual(len(report['screening']['screening_supplementary.csv']['invalid']),1)

    def test_reviewer_swap_across_files_rejected(self):
        self.respond(self.a,'screening_original.csv','A');self.respond(self.b,'screening_original.csv','B')
        self.respond(self.a,'screening_supplementary.csv','B');self.respond(self.b,'screening_supplementary.csv','A')
        with self.assertRaisesRegex(ValueError,'across packets'):
            audit(self.a,self.b,self.frozen)

    def test_tampered_manifest_rejected(self):
        with self.assertRaisesRegex(ValueError,'manifest was modified'):
            audit(self.a,self.b,dict(self.frozen,manifest_sha256='0'*64))
