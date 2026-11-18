import csv
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import slrkit as kit


def work(wid, title, d=None, year=2025):
    return {"id": f"https://openalex.org/{wid}", "title": title, "doi": d, "publication_year": year, "abstract_inverted_index": {"good": [1], "A": [0]}}


class MockClient:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.calls = []

    def fetch(self, params, path=""):
        self.calls.append((params, path))
        data = next(self.pages)
        if isinstance(data, Exception):
            raise data
        return data, kit.json_bytes(data)


def page(results, cursor=None, count=3):
    return {"meta": {"count": count, "next_cursor": cursor}, "results": results}


def scopus_work(eid, title="A paper", d=None):
    return {"eid": "2-s2.0-" + str(eid), "dc:title": title,
            "prism:doi": d, "prism:coverDate": "2025-06-01",
            "dc:creator": "A. Author", "prism:publicationName": "Example"}


def scopus_page(results, start, count):
    return {"search-results":{"opensearch:totalResults":str(count),
            "opensearch:startIndex":str(start), "entry":results}}


class ScopusTests(unittest.TestCase):
    def test_key_sent_only_in_header_and_not_persisted(self):
        mock = SimpleNamespace()
        response = scopus_page([scopus_work(1)],0,1)
        from unittest.mock import MagicMock
        proxy = MagicMock()
        proxy.__enter__.return_value.read.return_value = kit.json_bytes(response)
        with patch("slrkit.urllib.request.urlopen",return_value=proxy) as fetch, patch("slrkit.time.sleep"):
            client = kit.Scopus("private-test-key",insttoken="private-token")
            _, body = client.fetch({"query":"TITLE(example)","start":0})
            request = fetch.call_args.args[0]
            self.assertNotIn("private-test-key",request.full_url)
            self.assertEqual(request.get_header("X-els-apikey"),"private-test-key")
            self.assertEqual(request.get_header("X-els-insttoken"),"private-token")
            self.assertNotIn(b"private-test-key",body)
            proxy.__enter__.return_value.read.return_value=b'{"echo":"private-test-key"}'
            with self.assertRaisesRegex(RuntimeError,"credential material"):
                client.fetch({"query":"TITLE(example)"})

    def test_entitlement_and_quota_fail_closed(self):
        for status in (401,403,429):
            error = urllib.error.HTTPError("https://api.elsevier.com",status,"test",{},None)
            with patch("slrkit.urllib.request.urlopen",side_effect=error) as fetch:
                with self.assertRaisesRegex(RuntimeError,str(status)):
                    kit.Scopus("private-test-key").fetch({"query":"TITLE(example)"})
                self.assertEqual(fetch.call_count,1)

    def test_mixed_sources_and_same_eid_no_doi(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            run = base / "scopus"
            m = kit.run_create(run,"scopus_search",{"from_date":"2022-01-01","to_date":"2026-11-18"})
            mock = MockClient([scopus_page([scopus_work(1,d="10.1234/a")],0,2),
                               scopus_page([scopus_work(2)],1,2),
                               scopus_page([scopus_work(2)],0,1)])
            kit.retrieve_scopus(mock,run,m,"S1",{"count":1},None)
            kit.retrieve_scopus(mock,run,m,"S2",{"count":1},None)
            m["status"]="complete"
            kit.save_manifest(run,m)
            self.assertEqual(mock.calls[1][0]["start"],1)
            oa=base/"oa"
            kit.execute_run(oa,"search",{},[("Q",{})],MockClient([page([work("W1","A paper","10.1234/a")])]),None)
            args=SimpleNamespace(run=[oa,run],csv=[],allow_partial=False,out=base/"merged")
            kit.merge(args)
            report=json.loads((args.out/"report.json").read_text())
            self.assertEqual(report["retrieval_occurrences"],4)
            self.assertEqual(report["identifier_clusters"],2)
            self.assertEqual(report["source_overlap"]["OpenAlex"]["Scopus"],1)
            rows=list(kit.read_run(run,False))
            self.assertEqual(rows[0][1]["date_scope"],"cover_date_in_window")
            with (args.out/"records.csv").open(encoding="utf-8-sig") as f:
                merged=list(csv.DictReader(f))
            self.assertEqual({r["scopus_id"] for r in merged},{"2-s2.0-1","2-s2.0-2"})

    def test_zero_results(self):
        self.assertEqual(kit.scopus_page(scopus_page([{"error":"Result set was empty"}],0,0))[2],[])

    def test_capped_and_offset_limit(self):
        with tempfile.TemporaryDirectory() as d:
            run=Path(d)/"r"
            m=kit.run_create(run,"scopus_search",{})
            result=scopus_page([scopus_work(1)],0,6000)
            kit.retrieve_scopus(MockClient([result]),run,m,"S1",{"count":1},1)
            self.assertEqual(m["queries"][0]["status"],"capped")
            with self.assertRaisesRegex(RuntimeError,"offset limit"):
                kit.retrieve_scopus(MockClient([result]),run,m,"S2",{"count":1},None)

    def test_repeated_id_or_changed_total_fails(self):
        with tempfile.TemporaryDirectory() as d:
            run=Path(d)/"r"
            m=kit.run_create(run,"scopus_search",{})
            with self.assertRaisesRegex(RuntimeError,"repeated document IDs"):
                kit.retrieve_scopus(MockClient([scopus_page([scopus_work(1)],0,2),
                    scopus_page([scopus_work(1)],1,2)]),run,m,"S1",{"count":1},None)
            with self.assertRaisesRegex(RuntimeError,"hit count changed"):
                kit.retrieve_scopus(MockClient([scopus_page([scopus_work(1)],0,2),
                    scopus_page([scopus_work(2)],1,3)]),run,m,"S2",{"count":1},None)

    def test_scopus_identifier_conflict(self):
        rows=[(kit.normalize_scopus(scopus_work(s,d="10.1234/a")),
            {"source":"Scopus","query_id":"S","sha256":"x","row":s}) for s in (1,2)]
        _, _, conflicts, _=kit.consolidate(rows)
        self.assertEqual(conflicts[0]["scopus_ids"],"2-s2.0-1;2-s2.0-2")


class SemanticScholarTests(unittest.TestCase):
    def paper(self,n,d=None):
        return {"paperId":f"{n:040x}","title":"Example","year":2025,"externalIds":{"DOI":d},
                "authors":[{"name":"Author"}],"abstract":"Example abstract"}

    def test_bulk_pagination_and_cross_source_overlap(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d)
            run=base/"s2"
            m=kit.run_create(run,"s2_search",{"from_date":"2022-01-01","to_date":"2026-11-18"})
            a=self.paper(1,"10.1234/a")
            b=self.paper(2)
            mock=MockClient([{"total":3,"data":[a],"token":"next"},{"total":3,"data":[b]}])
            kit.retrieve_s2(mock,run,m,"T1",{},None)
            self.assertEqual(m["queries"][0]["status"],"exhausted")
            self.assertEqual(m["queries"][0]["hits_first_page"],3)
            self.assertEqual(mock.calls[1][0]["token"],"next")
            m["status"]="complete"
            kit.save_manifest(run,m)
            rows=list(kit.read_run(run,False))
            self.assertEqual(rows[0][1]["date_scope"],"year_in_window_date_unknown")
            oa=base/"oa"
            kit.execute_run(oa,"search",{},[("Q",{})],MockClient([page([work("W1","Example","10.1234/a")])]),None)
            args=SimpleNamespace(run=[oa,run],csv=[],allow_partial=False,out=base/"merged")
            kit.merge(args)
            report=json.loads((args.out/"report.json").read_text())
            self.assertEqual(report["identifier_clusters"],2)
            self.assertEqual(report["source_overlap"]["OpenAlex"]["Semantic Scholar"],1)

    def test_capped_and_repeated_token(self):
        with tempfile.TemporaryDirectory() as d:
            run=Path(d)/"r"
            m=kit.run_create(run,"s2_search",{})
            first={"total":3,"data":[self.paper(1)],"token":"next"}
            kit.retrieve_s2(MockClient([first]),run,m,"T1",{},1)
            self.assertEqual(m["queries"][0]["status"],"capped")
            with self.assertRaisesRegex(RuntimeError,"repeated continuation"):
                kit.retrieve_s2(MockClient([first,{"total":3,"data":[self.paper(2)],"token":"next"}]),run,m,"T2",{},None)

    def test_repeated_paper_id_fails(self):
        with tempfile.TemporaryDirectory() as d:
            run=Path(d)/"r"; m=kit.run_create(run,"s2_search",{})
            with self.assertRaisesRegex(RuntimeError,"repeated a paper ID"):
                kit.retrieve_s2(MockClient([{"total":2,"data":[self.paper(1)],"token":"next"},
                     {"total":2,"data":[self.paper(1)]}]),run,m,"T1",{},None)

    def test_anonymous_and_keyed_http(self):
        from unittest.mock import MagicMock
        proxy=MagicMock()
        proxy.__enter__.return_value.read.return_value=kit.json_bytes({"total":0,"data":[]})
        with patch("slrkit.urllib.request.urlopen",return_value=proxy) as fetch, patch("slrkit.time.sleep"):
            kit.SemanticScholar().fetch({"query":"example"})
            self.assertIsNone(fetch.call_args.args[0].get_header("X-api-key"))
            kit.SemanticScholar("demo-secret").fetch({"query":"example"})
            self.assertEqual(fetch.call_args.args[0].get_header("X-api-key"),"demo-secret")
            self.assertNotIn("demo-secret",fetch.call_args.args[0].full_url)
        error=urllib.error.HTTPError("https://api.semanticscholar.org",429,"test",{},None)
        with patch("slrkit.urllib.request.urlopen",side_effect=error) as fetch:
            with self.assertRaisesRegex(RuntimeError,"429"):
                kit.SemanticScholar().fetch({"query":"example"})
            self.assertEqual(fetch.call_count,1)


class ToolkitTests(unittest.TestCase):
    def test_normalization(self):
        self.assertEqual(kit.doi("https://doi.org/10.1234/ABC"), "10.1234/abc")
        self.assertEqual(kit.work_id("https://openalex.org/W123"), "W123")
        self.assertEqual(kit.abstract({"science": [1], "Open": [0]}), "Open science")
        self.assertEqual(kit.normalized_title("A—B!"), "a b")

    def test_page_and_merge(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            run = base / "run"
            client = MockClient([page([work("W1", "Example", "10.1234/a")], "next"), page([work("W2", "Other")], count=2)])
            kit.execute_run(run, "search", {"test": True}, [("Q1", {"search": "sample"})], client, None)
            manifest = json.loads((run / "manifest.json").read_text())
            self.assertEqual(manifest["status"], "complete")
            self.assertEqual(len(manifest["pages"]), 2)
            self.assertEqual(client.calls[1][0]["cursor"], "next")
            institutional = base / "export.csv"
            institutional.write_text("Title,Year,DOI,Abstract\nExample,2025,10.1234/a,More text\n", encoding="utf-8")
            args = SimpleNamespace(run=[run], csv=[f"Scopus={institutional}"], allow_partial=False, out=base / "out")
            kit.merge(args)
            report = json.loads((args.out / "report.json").read_text())
            self.assertEqual(report["retrieval_occurrences"], 3)
            self.assertEqual(report["identifier_clusters"], 2)
            self.assertEqual(report["source_overlap"]["OpenAlex"]["Scopus"], 1)
            self.assertIsNone(report["inclusion_count"])
            with (args.out / "title_abstract_screening.csv").open(encoding="utf-8-sig") as f:
                self.assertTrue(all(not row["decision"] for row in csv.DictReader(f)))

    def test_capped_run_is_not_complete(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d) / "run"
            kit.execute_run(run, "search", {}, [("Q", {})], MockClient([page([work("W1", "A")], "next")]), 1)
            self.assertEqual(json.loads((run / "manifest.json").read_text())["status"], "partial_capped")
            with self.assertRaises(ValueError):
                list(kit.read_run(run, False))
            self.assertEqual(len(list(kit.read_run(run, True))), 1)

    def test_raw_hash_check(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d) / "run"
            kit.execute_run(run, "search", {}, [("Q", {})], MockClient([page([work("W1", "A")])]), None)
            (run / "raw/Q-000001.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "checksum"):
                list(kit.read_run(run, False))

    def test_failed_run_is_not_complete(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d) / "run"
            with self.assertRaises(RuntimeError):
                kit.execute_run(run, "search", {}, [("Q", {})], MockClient([RuntimeError("test failure")]), None)
            self.assertEqual(json.loads((run / "manifest.json").read_text())["status"], "failed_or_interrupted")

    def test_title_matches_not_automatically_merged(self):
        items = [(kit.normalize_work(work(f"W{i}", "Same title", f"10.1234/{i}")), {"source": "OpenAlex", "query_id": "Q", "raw_path": "p", "row": i, "sha256": "a"}) for i in (1, 2)]
        rows, prov, conflicts, pairs = kit.consolidate(items)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(len(conflicts), 0)

    def test_identifier_conflict_flag(self):
        items = [(kit.normalize_work(work("W1", "Same title", f"10.1234/{i}")), {"source": "OpenAlex", "query_id": "Q", "raw_path": "p", "row": i, "sha256": "a"}) for i in (1, 2)]
        rows, _, conflicts, _ = kit.consolidate(items)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(conflicts), 1)

    def test_repeated_cursor_fails(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d) / "run"
            with self.assertRaisesRegex(RuntimeError, "Repeated cursor"):
                kit.execute_run(run, "search", {}, [("Q", {})], MockClient([page([], "same"), page([], "same")]), None)

    def test_identifierless_repeated_file_rows(self):
        with tempfile.TemporaryDirectory() as d:
            file = Path(d) / "export.csv"
            file.write_text("Title,Year\nNo identifier,2025\n", encoding="utf-8")
            a = list(kit.read_csv("Scopus", file))
            b = list(kit.read_csv("WoS", file))
            rows, prov, _, pairs = kit.consolidate(a + a + b)
            self.assertEqual(len(rows), 2)
            self.assertEqual(len({r["record_id"] for r in rows}), 2)
            self.assertEqual(len(prov), 3)

    def test_semantic_is_bounded_and_does_not_use_cursor(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d) / "semantic"
            result = work("W1", "Example")
            result["publication_date"] = "2026-12-01"
            client = MockClient([{"meta": {"count": 50}, "results": [result]}])
            kit.execute_run(run, "search", {"from_date": "2022-01-01", "to_date": "2026-11-18"}, [("Q05", {"search.semantic": "example", "filter": "publication_year:2022-2026"})], client, None)
            request = client.calls[0][0]
            self.assertNotIn("cursor", request)
            self.assertEqual(request["per_page"], 50)
            self.assertEqual(json.loads((run / "manifest.json").read_text())["queries"][0]["status"], "ranked_limit")
            self.assertEqual(list(kit.read_run(run, False))[0][1]["date_scope"], "outside_window")

    def test_failed_pilot_preserves_successful_pages(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d) / "pilot"
            mock = MockClient([page([work("W1", "Prior")]), RuntimeError("simulated 400")])
            with self.assertRaises(RuntimeError):
                kit.execute_run(run, "search", {}, [("Q1", {}), ("Q5", {"search.semantic": "example"})], mock, 1)
            self.assertEqual(len(list(kit.read_run(run, True))), 1)
            with self.assertRaises(ValueError):
                list(kit.read_run(run, False))


def atom_page(ids,total,start=0,related=""):
    records="".join(f'<entry><id>http://arxiv.org/abs/{aid}v2</id><title>Example paper</title><published>2025-01-01T00:00:00Z</published><summary>Some evidence</summary><author><name>A. Author</name></author><arxiv:doi>{related}</arxiv:doi></entry>' for aid in ids)
    return f'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/" xmlns:arxiv="http://arxiv.org/schemas/atom"><opensearch:totalResults>{total}</opensearch:totalResults><opensearch:startIndex>{start}</opensearch:startIndex>{records}</feed>'.encode()


class ArxivTests(unittest.TestCase):
    def test_identity_and_related_doi(self):
        self.assertEqual(kit.arxiv_id("https://arxiv.org/pdf/2501.12345v2.pdf"),"2501.12345")
        self.assertEqual(kit.arxiv_id("10.48550/arXiv.2501.12345"),"2501.12345")
        self.assertEqual(kit.arxiv_id("10.1234/example"),"")
        row=kit.arxiv_page(atom_page(["2501.12345"],1,related="10.1234/example"))[2][0]
        self.assertEqual(row["doi"],"")
        self.assertEqual(row["arxiv_related_doi"],"10.1234/example")
        self.assertEqual(kit.arxiv_page(atom_page([],0))[2],[])
        self.assertIn('ti:"coding agent" OR abs:"coding agent"',kit.arxiv_query('"coding agent" AND testing'))
        with self.assertRaises(ValueError):
            kit.arxiv_query('ti:agent')

    def test_paging_and_merge(self):
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as d:
            base=Path(d)
            run=base/"arxiv"
            m=kit.run_create(run,"arxiv_search",{"from_date":"2022-01-01","to_date":"2026-11-18"})
            client=MagicMock()
            client.fetch.side_effect=[atom_page(["2501.12345"],2,related="10.1234/example"),atom_page(["2501.12346"],2,1)]
            kit.retrieve_arxiv(client,run,m,"A1",{"max_results":1})
            m["status"]="complete"
            kit.save_manifest(run,m)
            self.assertEqual(client.fetch.call_args.args[0]["start"],1)
            self.assertEqual(m["queries"][0]["status"],"exhausted")
            oa=base/"oa"
            kit.execute_run(oa,"search",{},[("Q",{})],MockClient([page([work("W1","Example paper","10.48550/arxiv.2501.12345"),work("W2","Example paper","10.1234/example")])]),None)
            args=SimpleNamespace(run=[oa,run],csv=[],allow_partial=False,out=base/"merged")
            kit.merge(args)
            report=json.loads((args.out/"report.json").read_text())
            self.assertEqual(report["retrieval_occurrences"],4)
            self.assertEqual(report["identifier_clusters"],3)
            self.assertEqual(report["source_overlap"]["OpenAlex"]["arXiv"],1)
            self.assertEqual(report["date_scope_occurrences"]["in_window"],2)
            self.assertEqual(report["title_only_version_candidates"],3)
            (run/m["pages"][0]["path"]).write_bytes(b"modified")
            with self.assertRaisesRegex(ValueError,"checksum"):
                list(kit.read_run(run,False))

    def test_cap_and_pagination_failures(self):
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as d:
            run=Path(d)/"arxiv"
            m=kit.run_create(run,"arxiv_search",{})
            client=MagicMock()
            client.fetch.return_value=atom_page(["2501.12345"],2)
            kit.retrieve_arxiv(client,run,m,"A1",{"max_results":1},1)
            self.assertEqual(m["queries"][0]["status"],"capped")
            client.fetch.side_effect=[atom_page(["2501.12345"],2),atom_page(["2501.12345"],2,1)]
            with self.assertRaisesRegex(RuntimeError,"repeated"):
                kit.retrieve_arxiv(client,run,m,"A2",{"max_results":1})
            client.fetch.side_effect=[atom_page(["2501.12345"],2),atom_page(["2501.12346"],3,1)]
            with self.assertRaisesRegex(RuntimeError,"pagination"):
                kit.retrieve_arxiv(client,run,m,"A3",{"max_results":1})


def crossref_work(d="10.1234/test",parts=(2025,6,1)):
    return {"DOI":d,"title":["A <i>verified</i> paper"],"published":{"date-parts":[list(parts)]},"abstract":"<jats:p>Valid &amp; tested.</jats:p>","author":[{"given":"A.","family":"Author"}],"container-title":["Example journal"],"type":"journal-article","score":54.1}


def crossref_page(rows,total):
    return {"status":"ok","message":{"items":rows,"total-results":total}}


class CrossrefTests(unittest.TestCase):
    def test_normalization(self):
        row=kit.normalize_crossref(crossref_work())
        self.assertEqual(row["title"],"A verified paper")
        self.assertEqual(row["abstract"],"Valid & tested.")
        self.assertEqual(row["publication_date"],"2025-06-01")
        self.assertEqual(kit.normalize_crossref(crossref_work(parts=(2025,)))["publication_date"],"")
        self.assertEqual(kit.normalize_crossref(crossref_work(parts=(2025,)))["year"],"2025")

    def test_bounded_search_merge(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d)
            run=base/"crossref"
            m=kit.run_create(run,"crossref_search",{"from_date":"2022-01-01","to_date":"2026-11-18"})
            client=MockClient([crossref_page([crossref_work()],561390)])
            kit.retrieve_crossref(client,run,m,"C1",{"rows":1})
            self.assertEqual(m["queries"][0]["status"],"ranked_limit")
            self.assertEqual(m["queries"][0]["hits_first_page"],561390)
            m["status"]="complete"
            kit.save_manifest(run,m)
            oa=base/"oa"
            kit.execute_run(oa,"search",{},[("Q",{})],MockClient([page([work("W1","A verified paper","10.1234/test")])]),None)
            args=SimpleNamespace(run=[oa,run],csv=[],allow_partial=False,out=base/"merged")
            kit.merge(args)
            report=json.loads((args.out/"report.json").read_text())
            self.assertEqual(report["retrieval_occurrences"],2)
            self.assertEqual(report["identifier_clusters"],1)
            self.assertEqual(report["source_overlap"]["OpenAlex"]["Crossref"],1)
            self.assertEqual(report["date_scope_occurrences"]["in_window"],1)
            self.assertIsNone(report["inclusion_count"])

    def test_bad_responses_and_rate_limit(self):
        with self.assertRaisesRegex(RuntimeError,"successful message"):
            kit.crossref_page({"status":"failed"})
        with tempfile.TemporaryDirectory() as d:
            run=Path(d)/"r"
            m=kit.run_create(run,"crossref_search",{})
            for rows,total in [([crossref_work(),crossref_work()],2),([],2)]:
                with self.assertRaisesRegex(RuntimeError,"inconsistent or duplicate"):
                    kit.retrieve_crossref(MockClient([crossref_page(rows,total)]),run,m,"C1",{"rows":2})
        with patch("slrkit.urllib.request.urlopen",side_effect=urllib.error.HTTPError("https://api.crossref.org",429,"test",{},None)) as fetch:
            with self.assertRaisesRegex(RuntimeError,"429"):
                kit.Crossref().fetch({"rows":1})
            self.assertEqual(fetch.call_count,1)


class DblpTests(unittest.TestCase):
    def test_matcher(self):
        m=kit.title_matcher('("coding agent" OR "SWE-bench") AND (verification OR testing)')
        self.assertTrue(m(" coding agent testing "))
        self.assertTrue(m(" swe bench verification "))
        self.assertFalse(m(" coding agent generation "))
        self.assertFalse(m(" agent verification "))
        with self.assertRaises(ValueError):
            kit.title_matcher("agent AND (verification")

    def test_stream_and_merge(self):
        import gzip
        with tempfile.TemporaryDirectory() as d:
            base=Path(d); snapshot=base/"dblp.xml.gz"; dtd=base/"dblp.dtd"; cfg=base/"config.json"
            dtd.write_text('<!ENTITY eacute "&#233;">')
            xml=b'<?xml version="1.0"?><!DOCTYPE dblp SYSTEM "dblp.dtd"><dblp><article key="journals/x/A"><author>Ren&eacute;</author><title>Coding agent <i>verification</i></title><year>2025</year><ee>https://doi.org/10.1234/a</ee></article><inproceedings key="conf/x/B"><title>Coding agent verification</title><year>2026</year></inproceedings><article key="journals/x/Old"><title>Coding agent verification</title><year>2020</year></article><www key="home/x"><title>Coding agent verification</title><year>2025</year></www></dblp>'
            with gzip.open(snapshot,"wb") as f:
                f.write(xml)
            cfg.write_text(json.dumps({"from_date":"2022-01-01","to_date":"2026-11-18","queries":[{"id":"Q1","text":"agent AND verification"},{"id":"Q2","text":"\"coding agent\""},{"id":"Q3","mode":"semantic","text":"ignored"}]}))
            run=base/"dblp"
            kit.dblp_search(SimpleNamespace(config=cfg,snapshot=snapshot,dtd=dtd,snapshot_citation="example snapshot",out=run,only=None))
            m=json.loads((run/"manifest.json").read_text())
            self.assertEqual(m["scanned_publications"],3)
            rows=list(kit.read_run(run,False))
            self.assertEqual(len(rows),4)
            self.assertEqual(rows[0][0]["authors"],"René")
            self.assertEqual(rows[0][1]["source"],"DBLP")
            self.assertEqual(rows[0][1]["date_scope"],"year_in_window_date_unknown")
            oa=base/"oa"
            kit.execute_run(oa,"search",{},[("Q",{})],MockClient([page([work("W1","Coding agent verification","10.1234/a")])]),None)
            args=SimpleNamespace(run=[oa,run],csv=[],allow_partial=False,out=base/"merged")
            kit.merge(args)
            report=json.loads((args.out/"report.json").read_text())
            self.assertEqual(report["retrieval_occurrences"],5)
            self.assertEqual(report["identifier_clusters"],2)
            self.assertEqual(report["source_overlap"]["OpenAlex"]["DBLP"],1)

    def test_external_entity_rejection(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d); snapshot=base/"dblp.xml"; dtd=base/"dblp.dtd"
            dtd.write_text('<!ENTITY eacute "&#233;">')
            snapshot.write_text('<!DOCTYPE dblp SYSTEM "https://example.invalid/evil.dtd"><dblp/>')
            with self.assertRaisesRegex(ValueError,"Unexpected external"):
                kit.stream_dblp(snapshot,dtd,lambda *args: None)


if __name__ == "__main__":
    unittest.main()
