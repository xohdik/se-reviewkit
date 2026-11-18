import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

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


if __name__ == "__main__":
    unittest.main()
