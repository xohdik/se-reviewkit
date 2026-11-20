"""Strict, source-preserving two-reviewer calibration audit for SE-ReviewKit.

This tool never supplies reviewer decisions, adjudicates eligibility, or derives final
inclusion counts. It reports paired agreement only where both independent decisions
are complete and preserves each unresolved disagreement for human adjudication.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

SCREEN_META = {
    "screening_original.csv": ("record_id", ("record_id", "title", "year", "type", "doi", "url", "abstract")),
    "screening_supplementary.csv": ("component", ("component", "title", "year", "venue", "type", "url", "abstract")),
}
SCREEN_RESP = ("reviewer_id", "decision", "reason_code", "rationale", "reviewed_utc")
CLAIM_META = ("claim_id", "family", "source", "version", "locator", "configuration", "metric", "target_population")
CLAIM_RESP = ("source_access", "observed_reported_values", "observed_denominator", "denominator_kind", "source_locator_confirmed", "scope_confirmed", "artifact_availability", "source_evidence_notes", "reviewer_id", "reviewed_utc")
LABELS = ("include", "exclude", "uncertain")
REASON_CODES = {
    "include": {"POTENTIAL_SCOPE"},
    "uncertain": {"INSUFFICIENT_METADATA", "SCOPE_AMBIGUOUS", "VERSION_CHECK", "OTHER_CLARIFY"},
    "exclude": {"OUTSIDE_SOFTWARE", "NO_AGENT_SCOPE", "NO_VERIFICATION_RELEVANCE", "SECONDARY_ONLY"},
}
CLAIM_ENUM = {
    "source_access": {"accessible", "blocked", "version_mismatch"},
    "denominator_kind": {"explicit", "inferred", "not_applicable", "unknown"},
    "source_locator_confirmed": {"yes", "no", "uncertain"},
    "scope_confirmed": {"yes", "no", "uncertain"},
    "artifact_availability": {"available", "unavailable", "not_assessed", "not_applicable"},
}


def checksum(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def csv_records(path: Path, key: str) -> dict[str, dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        if key not in headers or len(headers) != len(set(headers)):
            raise ValueError(f"{path}: missing {key} or duplicate columns")
        rows = list(reader)
    result = {}
    for row in rows:
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f"{path}: malformed CSV row")
        ident = (row.get(key) or "").strip()
        if not ident or ident in result:
            raise ValueError(f"{path}: missing or duplicate {key}: {ident}")
        result[ident] = row
    return result


def claim_records(path: Path) -> dict[str, dict]:
    result = {}
    with path.open(encoding="utf-8-sig") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{n}: expected object")
            ident = row.get("claim_id")
            if not isinstance(ident, str) or not ident or ident in result:
                raise ValueError(f"{path}:{n}: duplicate or missing claim_id")
            result[ident] = row
    return result


def metadata(row: dict, fields: tuple[str, ...]) -> dict:
    return {k: row.get(k) for k in fields}


def freeze_manifest(folder: Path) -> dict:
    """Frozen source-metadata fingerprint, deliberately excluding response fields."""
    result = {"format": 1, "screening": {}, "claims": {}}
    for name, (key, fields) in SCREEN_META.items():
        records = csv_records(folder/name, key)
        result["screening"][name] = {ident: checksum(metadata(row, fields)) for ident, row in records.items()}
    for name in ("calibration.jsonl", "remaining.jsonl"):
        records = claim_records(folder/name)
        result["claims"][name] = {ident: checksum(metadata(row, CLAIM_META)) for ident, row in records.items()}
    result["manifest_sha256"] = checksum({k: v for k, v in result.items() if k != "manifest_sha256"})
    return result


def valid_time(stamp: Any) -> bool:
    if not isinstance(stamp, str) or not stamp.strip():
        return False
    try:
        parsed = dt.datetime.fromisoformat(stamp.strip().replace("Z", "+00:00"))
        return parsed.tzinfo is not None and parsed.utcoffset() is not None
    except ValueError:
        return False


def screen_state(row: dict) -> tuple[str, list[str]]:
    """Distinguish blank, complete and malformed without silently discarding entries."""
    dec = (row.get("decision") or "").strip().lower()
    touched = any(str(row.get(c) or "").strip() for c in SCREEN_RESP)
    if not touched:
        return "blank", []
    errors = []
    if dec not in LABELS:
        errors.append("invalid_decision")
    elif (row.get("reason_code") or "").strip() not in REASON_CODES[dec]:
        errors.append("invalid_reason_for_decision")
    for key in ("reviewer_id", "rationale"):
        if not (row.get(key) or "").strip():
            errors.append("missing_"+key)
    if not valid_time(row.get("reviewed_utc")):
        errors.append("missing_or_invalid_reviewed_utc")
    return ("invalid" if errors else "complete"), errors


def claim_state(row: dict) -> tuple[str, list[str]]:
    touched = any(row.get(k) not in (None, "") for k in CLAIM_RESP)
    if not touched:
        return "blank", []
    err = []
    for key, allowed in CLAIM_ENUM.items():
        if not isinstance(row.get(key), str) or row.get(key) not in allowed:
            err.append("invalid_" + key)
    n = row.get("observed_denominator")
    if n is not None and (type(n) is not int or n <= 0):
        err.append("invalid_denominator")
    if row.get("denominator_kind") in ("not_applicable", "unknown") and n is not None:
        err.append("contradictory_denominator_kind")
    if row.get("denominator_kind") in ("explicit", "inferred") and n is None:
        err.append("missing_denominator")
    if row.get("source_access") == "accessible" and not (isinstance(row.get("observed_reported_values"), str) and row["observed_reported_values"].strip()):
        err.append("missing_observed_values")
    for key in ("source_evidence_notes", "reviewer_id"):
        if not (isinstance(row.get(key), str) and row[key].strip()):
            err.append("missing_" + key)
    if not valid_time(row.get("reviewed_utc")):
        err.append("missing_or_invalid_reviewed_utc")
    return ("invalid" if err else "complete"), err


def kappa(matrix: dict[str, dict[str, int]]) -> dict:
    n = sum(sum(row.values()) for row in matrix.values())
    if not n:
        return {"complete_pairs": 0, "observed_agreement": None, "cohens_kappa": None}
    agree = sum(matrix[k][k] for k in LABELS)
    exp = sum(sum(matrix[k].values()) * sum(matrix[i][k] for i in LABELS) for k in LABELS) / n**2
    obs = agree / n
    return {"complete_pairs": n, "observed_agreement": obs, "cohens_kappa": (obs-exp)/(1-exp) if exp < 1 else None}


def audit(a_dir: Path, b_dir: Path, frozen: dict) -> tuple[dict, list[dict], list[dict]]:
    if frozen.get("manifest_sha256") != checksum({k:v for k,v in frozen.items() if k!="manifest_sha256"}):
        raise ValueError("frozen manifest was modified")
    report = {"status": "awaiting_independent_human_review", "automatic_eligibility_decisions": 0, "final_inclusions": None, "screening": {}, "source_claims": {}}
    screen_queue = []
    claim_queue = []
    all_a, all_b = set(), set()
    for name, (key, fields) in SCREEN_META.items():
        aa = csv_records(a_dir/name, key); bb = csv_records(b_dir/name, key)
        expected = frozen["screening"][name]
        if set(aa) != set(bb) or set(aa) != set(expected):
            raise ValueError(f"{name}: record universe differs from frozen manifest")
        for ident in expected:
            if checksum(metadata(aa[ident], fields)) != expected[ident] or checksum(metadata(bb[ident], fields)) != expected[ident]:
                raise ValueError(f"{name}/{ident}: frozen metadata was modified")
        mx = {i:{j:0 for j in LABELS} for i in LABELS}
        counts = Counter(); invalid = []; review_a=set(); review_b=set()
        for ident in sorted(expected):
            ra, ea = screen_state(aa[ident]); rb, eb = screen_state(bb[ident]);counts[ra+"_A"] += 1; counts[rb+"_B"] += 1
            if ra == "invalid" or rb == "invalid":
                invalid.append({"id":ident, "A":ea,"B":eb})
            if ra == "complete":review_a.add(aa[ident]["reviewer_id"].strip())
            if rb == "complete":review_b.add(bb[ident]["reviewer_id"].strip())
            if ra == "complete" and rb == "complete":
                l=aa[ident]["decision"].strip().lower();r=bb[ident]["decision"].strip().lower();mx[l][r]+=1
                reason="agreement" if l == r else "disagreement"
                if l != r:
                    screen_queue.append({"dataset":name,"record_id":ident,"reviewer_A":l,"reviewer_B":r,"coordinator_decision":"","coordinator_reason":"","adjudicator_id":"","adjudicated_utc":""})
            else:
                reason="invalid" if ra == "invalid" or rb == "invalid" else "pending"
            if reason in ("pending", "invalid"):screen_queue.append({"dataset":name,"record_id":ident,"reviewer_A":aa[ident].get("decision", ""),"reviewer_B":bb[ident].get("decision", ""),"coordinator_decision":"","coordinator_reason":"","adjudicator_id":"","adjudicated_utc":""})
        if review_a & review_b:
            raise ValueError(f"{name}: same reviewer_id used in both packets")
        if len(review_a) > 1 or len(review_b) > 1:
            raise ValueError(f"{name}: multiple reviewer IDs in one packet")
        all_a.update(review_a); all_b.update(review_b)
        report["screening"][name] = {"records":len(expected),"states":dict(counts),"invalid":invalid,"agreement_matrix":mx,**kappa(mx),"unresolved_queue_count":sum(r["dataset"]==name for r in screen_queue)}
    for name in ("calibration.jsonl", "remaining.jsonl"):
        aa=claim_records(a_dir/name); bb=claim_records(b_dir/name)
        expected=frozen["claims"][name]
        if set(aa)!=set(bb) or set(aa)!=set(expected):raise ValueError(f"{name}: frozen claim universe mismatch")
        counts=Counter(); invalid=[]; complete_pairs=0; unequal=[]; readers_a=set();readers_b=set()
        fields=("source_access","observed_reported_values","observed_denominator","denominator_kind","source_locator_confirmed","scope_confirmed","artifact_availability")
        for ident in sorted(expected):
            if checksum(metadata(aa[ident],CLAIM_META))!=expected[ident] or checksum(metadata(bb[ident],CLAIM_META))!=expected[ident]:raise ValueError(f"{name}/{ident}: claim metadata changed")
            sa,ea=claim_state(aa[ident]);sb,eb=claim_state(bb[ident]);counts[sa+"_A"]+=1;counts[sb+"_B"]+=1
            if sa=="invalid" or sb=="invalid":invalid.append({"id":ident,"A":ea,"B":eb})
            if sa=="complete":readers_a.add(aa[ident]["reviewer_id"].strip())
            if sb=="complete":readers_b.add(bb[ident]["reviewer_id"].strip())
            if sa==sb=="complete":
                complete_pairs+=1
                diff=[f for f in fields if aa[ident].get(f)!=bb[ident].get(f)]
                if diff:
                    unequal.append({"claim_id":ident,"different_fields":diff})
                    claim_queue.append({"batch":name,"claim_id":ident,"different_fields":";".join(diff),"adjudicated_values_json":"","source_locator":"","adjudicator_id":"","adjudicated_utc":""})
            elif name=="calibration.jsonl" or sa=="invalid" or sb=="invalid":
                claim_queue.append({"batch":name,"claim_id":ident,"different_fields":"INVALID_REVIEW" if "invalid" in (sa,sb) else "PENDING_INDEPENDENT_REVIEWS","adjudicated_values_json":"","source_locator":"","adjudicator_id":"","adjudicated_utc":""})
        if readers_a & readers_b or len(readers_a)>1 or len(readers_b)>1:raise ValueError(f"{name}: reviewer identity not independent")
        all_a.update(readers_a); all_b.update(readers_b)
        report["source_claims"][name]={"claims":len(expected),"states":dict(counts),"complete_pairs":complete_pairs,"unequal_complete_pairs":len(unequal),"differences":unequal,"invalid":invalid}
    if all_a & all_b or len(all_a) > 1 or len(all_b) > 1:
        raise ValueError("Reviewer identity must remain distinct and consistent across packets")
    report["complete_eligibility_pairs"] = sum(v["complete_pairs"] for v in report["screening"].values())
    report["complete_source_calibration_pairs"] = report["source_claims"]["calibration.jsonl"]["complete_pairs"]
    report["human_screening_final_included"] = None
    report["note"] = "All unresolved entries require independent human decisions or adjudication. The tool never supplies consensus or study inclusion."
    return report,screen_queue,claim_queue


def write_csv(path: Path, rows: list[dict], headers: list[str]):
    with path.open("w",encoding="utf-8-sig",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=headers);writer.writeheader();writer.writerows(rows)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="command",required=True)
    f=sub.add_parser("freeze"); f.add_argument("reviewer_a",type=Path);f.add_argument("--out",type=Path,required=True)
    a=sub.add_parser("audit");a.add_argument("reviewer_a",type=Path);a.add_argument("reviewer_b",type=Path);a.add_argument("--frozen",type=Path,required=True);a.add_argument("--out",type=Path,required=True)
    args=p.parse_args()
    if args.command=="freeze":
        data=freeze_manifest(args.reviewer_a)
        with args.out.open('x', encoding='utf-8') as f:
            json.dump(data,f,indent=2,ensure_ascii=False); f.write('\n')
        print("FROZEN",sum(len(v) for v in data["screening"].values()),"screening records");return
    result,sq,cq=audit(args.reviewer_a,args.reviewer_b,json.loads(args.frozen.read_text(encoding="utf-8")))
    args.out.mkdir(parents=True,exist_ok=False)
    (args.out/"audit_report.json").write_text(json.dumps(result,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    write_csv(args.out/"screening_adjudication_queue.csv",sq,["dataset","record_id","reviewer_A","reviewer_B","coordinator_decision","coordinator_reason","adjudicator_id","adjudicated_utc"])
    write_csv(args.out/"claim_adjudication_queue.csv",cq,["batch","claim_id","different_fields","adjudicated_values_json","source_locator","adjudicator_id","adjudicated_utc"])
    print(json.dumps({"complete_eligibility_pairs":result["complete_eligibility_pairs"],"complete_source_calibration_pairs":result["complete_source_calibration_pairs"],"unresolved_screening":len(sq),"unresolved_claims":len(cq),"status":result["status"]},indent=2))

if __name__=="__main__":
    main()
