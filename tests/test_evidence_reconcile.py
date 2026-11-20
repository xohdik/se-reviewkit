import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from evidence_reconcile import census, check_arithmetic, main


def fixture():
    mapping = [dict(report_id='r1', group_id='g1', working_representative='r1'),
               dict(report_id='r2', group_id='g1', working_representative='r1'),
               dict(report_id='r3', group_id='g2', working_representative='r3')]
    groups = [dict(group_id='g1', working_included_reports=['r1', 'r2'], working_representative='r1'),
              dict(group_id='g2', working_included_reports=['r3'], working_representative='r3')]
    outcome = dict(outcome_ordinal=1, unit_id='r1', working_group_id='g1', source_url='https://example.invalid/paper',
                   exact_version='v1', locator='Table 1', metric='accuracy', population='synthetic tasks',
                   descriptive_endpoint_group='performance', rq_routes=['RQ1','RQ2'])
    outcomes = [outcome, dict(outcome, outcome_ordinal=2, unit_id='r2', rq_routes=['RQ2']),
                dict(outcome, outcome_ordinal=3, unit_id='r3', working_group_id='g2',
                     descriptive_endpoint_group='cost', rq_routes=['RQ2'])]
    return mapping, groups, outcomes


class EvidenceReconciliationTests(unittest.TestCase):
    def test_overlapping_rqs_distinct_families(self):
        result = census(*fixture())
        self.assertEqual(result['working_reports'], 3)
        self.assertEqual(result['working_groups'], 2)
        self.assertEqual(result['rq_census'][1]['rows'], 3)
        self.assertEqual(result['rq_census'][1]['groups'], 2)
        self.assertEqual(sum(row['rows'] for row in result['endpoint_census']), 3)
        self.assertIsNone(result['final_included_studies'])
        self.assertIsNone(result['confirmatory_result'])

    def test_bad_group_rejected(self):
        m,g,o = fixture(); o[0]['working_group_id']='g2'
        with self.assertRaisesRegex(ValueError,'inconsistent'):
            census(m,g,o)

    def test_duplicate_and_noninteger_ordinals_rejected(self):
        for ordinal in (True,1,0,1.5):
            m,g,o = fixture(); o[1]['outcome_ordinal']=ordinal
            with self.assertRaises(ValueError):
                census(m,g,o)

    def test_duplicate_members_and_rq_rejected(self):
        m,g,o = fixture(); g[0]['working_included_reports'].append('r1')
        with self.assertRaisesRegex(ValueError,'membership'):
            census(m,g,o)
        m,g,o = fixture(); o[0]['rq_routes'].append('RQ1')
        with self.assertRaisesRegex(ValueError,'RQ routes'):
            census(m,g,o)

    def test_report_census_order_stable(self):
        m,g,o=fixture()
        self.assertEqual(census(m,g,o),census(m[::-1],g[::-1],o[::-1]))

    def test_invalid_source_rejected(self):
        for value in ('file:///tmp/paper','https://user:password@example.invalid/a',''):
            m,g,o=fixture(); o[0]['source_url']=value
            with self.assertRaises(ValueError):
                census(m,g,o)

    def test_arithmetic(self):
        record=dict(outcome_ordinal=1,numerator=3,denominator=4,recomputed_percent_or_pp=75,
                    source_url='https://example.invalid/a',source_version='v1',locator='Table 2')
        self.assertEqual(check_arithmetic([record],{1}),1)
        for change in ({'denominator':0},{'outcome_ordinal':2},{'recomputed_percent_or_pp':74},
                       {'recomputed_percent_or_pp':float('nan')},{'denominator':True}):
            with self.assertRaises(ValueError):
                check_arithmetic([dict(record,**change)],{1})

    def test_cli_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); args=[]
            for name,value in zip(('mapping','groups','outcomes'),fixture()):
                p=root/(name+'.json');p.write_text(json.dumps(value),encoding='utf-8');args+=['--'+name,str(p)]
            output=root/'report.json'; args+=['--out',str(output)]
            main(args); before=output.read_bytes()
            self.assertEqual(len(json.loads(before)['input_sha256']),3)
            with self.assertRaises(SystemExit):
                main(args)
            self.assertEqual(output.read_bytes(),before)
