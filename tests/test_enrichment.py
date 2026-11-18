import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import slrkit as kit


class Client:
    key = "synthetic-api-key"

    def __init__(self, pages):
        self.pages=iter(pages); self.calls=[]

    def fetch(self, params):
        self.calls.append(params)
        data=next(self.pages)
        if isinstance(data, Exception): raise data
        return data,kit.json_bytes(data)


def record(rid, d="", w="", title="Test paper", year="2025", ab="", **extra):
    return dict(record_id=rid,doi=d,openalex_id=w,title=title,year=year,abstract=ab,dblp_doi_candidates="",**extra)


def work(d,w="W1",title="Test paper",year=2025,ab=True):
    return dict(id="https://openalex.org/"+w,doi=d,title=title,publication_year=year,abstract_inverted_index={"Recovered":[0],"text":[1]} if ab else None)


def page(rows): return {"meta":{"count":len(rows)},"results":rows}


def read(path):
    with path.open(encoding="utf-8-sig",newline="") as f: return list(csv.DictReader(f))


class EnrichmentTests(unittest.TestCase):
    def setup_inputs(self,base,rows,conflicts=()):
        merged=base/"merged"; merged.mkdir()
        kit.csv_write(merged/"records.csv",rows,list(rows[0]))
        kit.csv_write(merged/"identifier_conflicts.csv",[{"record_id":r} for r in conflicts],["record_id"])
        return SimpleNamespace(merged=merged,out=base/"out",limit=None,batch_size=25,crossref=False,delay=1.1)

    def test_exact_and_guarded_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            rows=[record("a","10.1234/a"),record("b","10.1234/b",title="Different title"),record("c","10.1234/c",year="2026"),record("d","10.1234/d",w="W5"),record("e","10.1234/e"),record("f","10.1234/f"),record("g","10.1234/g"),record("h"),record("i","10.1234/i",ab="Original"),record("j",w="W10")]
            args=self.setup_inputs(Path(d),rows,("g",))
            mock=Client([page([work("10.1234/a"),work("10.1234/b"),work("10.1234/c"),work("10.1234/d",w="W6"),work("10.1234/e",ab=False),work("10.1234/f"),work("10.1234/f",w="W8")]),page([work("10.1234/j",w="W10")])])
            original=(args.merged/"records.csv").read_bytes()
            with patch("slrkit.OpenAlex",return_value=mock),patch("slrkit.key",return_value=mock.key): kit.enrich_abstracts(args)
            self.assertEqual((args.merged/"records.csv").read_bytes(),original)
            statuses={r["record_id"]:r["status"] for r in read(args.out/"abstract_recovery.csv")}
            self.assertEqual(statuses,{"a":"recovered","b":"title_mismatch","c":"year_mismatch","d":"identifier_mismatch","e":"abstract_absent","f":"multiple_exact_matches","g":"identity_conflict","h":"no_safe_identifier","j":"recovered"})
            enriched={r["record_id"]:r for r in read(args.out/"enriched_records.csv")}
            self.assertEqual(len(enriched),len(rows)); self.assertEqual(enriched["i"]["abstract"],"Original")
            self.assertEqual(enriched["a"]["abstract"],"Recovered text");self.assertEqual(enriched["b"]["abstract"],"")
            self.assertEqual(json.loads((args.out/"report.json").read_text())["recovered"],2)
            self.assertNotIn(mock.key,(args.out/"manifest.json").read_text())
            with self.assertRaisesRegex(ValueError,"not a retrieval run"): list(kit.read_run(args.out,False))

    def test_pilot_and_crossref_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            args=self.setup_inputs(Path(d),[record("a","10.1234/a"),record("b","10.1234/b"),record("c","10.1234/c")]);args.limit=2;args.crossref=True
            oa=Client([page([work("10.1234/a",ab=False)])])
            cr=Client([{"status":"ok","message":{"total-results":1,"items":[{"DOI":"10.1234/a","title":["Test paper"],"published":{"date-parts":[[2025]]},"abstract":"<p>Abstract recovered.</p>"}]}}])
            with patch("slrkit.OpenAlex",return_value=oa),patch("slrkit.Crossref",return_value=cr),patch("slrkit.key",return_value=oa.key): kit.enrich_abstracts(args)
            rr={r["record_id"]:r for r in read(args.out/"abstract_recovery.csv")}
            self.assertEqual(rr["a"]["recovered_abstract"],"Abstract recovered.")
            self.assertEqual(rr["a"]["openalex_status"],"abstract_absent");self.assertEqual(rr["a"]["crossref_status"],"recovered")
            self.assertEqual(rr["c"]["status"],"not_attempted_limit")
            self.assertEqual(json.loads((args.out/"manifest.json").read_text())["status"],"pilot_complete")

    def test_incomplete_batch_stops_and_preserves_pending(self):
        with tempfile.TemporaryDirectory() as d:
            args=self.setup_inputs(Path(d),[record("a","10.1234/a"),record("b","10.1234/b")]);args.batch_size=1
            mock=Client([page([work("10.1234/a")]),{"meta":{"count":2},"results":[work("10.1234/b")]}])
            with patch("slrkit.OpenAlex",return_value=mock),patch("slrkit.key",return_value=mock.key):
                with self.assertRaisesRegex(RuntimeError,"Incomplete"): kit.enrich_abstracts(args)
            report=json.loads((args.out/"report.json").read_text())
            self.assertEqual(report["status"],"failed_or_interrupted");self.assertEqual(report["recovered"],1);self.assertEqual(report["status_counts"]["pending"],1)

    def test_conflicts_and_filter_control_characters(self):
        for d in ("10.1234/a|b","10.1234/a,b","10.1234/a+b"):
            self.assertEqual(kit.enrichment_identity(record("a",d),set())[2],"no_safe_identifier")
        ambiguous=record("a","10.1234/a");ambiguous["dblp_doi_candidates"]="10.1234/a;10.1234/b"
        self.assertEqual(kit.enrichment_identity(ambiguous,set())[2],"identity_conflict")


if __name__ == "__main__": unittest.main()
