"""SE-ReviewKit: auditable retrieval and screening handoff (Python 3.10+)."""
import argparse
import csv
import datetime as dt
import getpass
import gzip
import hashlib
import html
import json
import math
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from xml.parsers import expat
from collections import Counter, defaultdict
from pathlib import Path

VERSION = "0.6.1"
API = "https://api.openalex.org/works"
SCOPUS_API = "https://api.elsevier.com/content/search/scopus"
S2_API = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"
S2_FIELDS = "paperId,externalIds,title,year,publicationDate,authors,venue,publicationTypes,abstract,url"
ARXIV_API = "https://export.arxiv.org/api/query"
CROSSREF_API = "https://api.crossref.org/works"
ATOM = {"a":"http://www.w3.org/2005/Atom","o":"http://a9.com/-/spec/opensearch/1.1/","x":"http://arxiv.org/schemas/atom"}
FIELDS = "id,doi,title,publication_year,publication_date,authorships,primary_location,type,abstract_inverted_index,referenced_works,open_access"
COLS = ["record_id", "title", "year", "publication_date", "doi", "openalex_id", "authors", "venue", "type", "abstract", "url", "sources", "query_ids", "occurrences"]


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(data):
    return (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def write_json(path, obj):
    path.write_bytes(json_bytes(obj))


def doi(value):
    value = str(value or "").strip().lower()
    value = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", value)
    return value if re.fullmatch(r"10\.\d{4,9}/\S+", value) else ""


def work_id(value):
    value = str(value or "").strip().rsplit("/", 1)[-1].upper()
    return value if re.fullmatch(r"W\d+", value) else ""


def normalized_title(value):
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(re.findall(r"[^\W_]+", value, flags=re.UNICODE))


def abstract(index):
    if not isinstance(index, dict):
        return ""
    words = {}
    for word, positions in index.items():
        for pos in positions:
            if isinstance(pos, int) and 0 <= pos < 100000:
                words[pos] = word
    return " ".join(words[p] for p in sorted(words))


def normalize_work(work):
    loc = work.get("primary_location") or {}
    source = loc.get("source") or {}
    return {
        "title": work.get("title") or work.get("display_name") or "",
        "year": str(work.get("publication_year") or ""),
        "publication_date": work.get("publication_date") or "",
        "doi": doi(work.get("doi")), "openalex_id": work_id(work.get("id")),
        "authors": "; ".join((a.get("author") or {}).get("display_name") or "" for a in (work.get("authorships") or [])),
        "venue": source.get("display_name") or "", "type": work.get("type") or "",
        "abstract": abstract(work.get("abstract_inverted_index")),
        "url": loc.get("landing_page_url") or work.get("id") or "",
    }


def scopus_id(value):
    value = str(value or "").strip()
    match = re.fullmatch(r"(?:2-s2\.0-|SCOPUS_ID:)?(\d+)", value, re.I)
    return "2-s2.0-" + match.group(1) if match else ""


def normalize_scopus(work):
    sid = scopus_id(work.get("eid")) or scopus_id(work.get("dc:identifier"))
    date = work.get("prism:coverDate") or ""
    authors = work.get("author") or []
    if isinstance(authors, dict):
        authors = [authors]
    names = "; ".join(str(a.get("authname") or a.get("surname") or "") for a in authors if isinstance(a, dict))
    return {"title": work.get("dc:title") or "", "year": date[:4] if re.match(r"\d{4}", date) else "",
        "publication_date": date, "doi": doi(work.get("prism:doi")), "openalex_id": "",
        "scopus_id": sid, "authors": names.strip("; ") or work.get("dc:creator") or "",
        "venue": work.get("prism:publicationName") or "", "type": work.get("subtypeDescription") or work.get("prism:aggregationType") or "",
        "abstract": work.get("dc:description") or "", "url": ("https://doi.org/" + doi(work.get("prism:doi"))) if doi(work.get("prism:doi")) else
        ("https://www.scopus.com/record/display.uri?eid=" + sid if sid else "")}


def s2_id(value):
    value = str(value or "").strip().lower()
    return value if re.fullmatch(r"[0-9a-f]{40}",value) else ""


def normalize_s2(work):
    return {"title":work.get("title") or "", "year":str(work.get("year") or ""),
        "publication_date":work.get("publicationDate") or "", "doi":doi((work.get("externalIds") or {}).get("DOI")),
        "openalex_id":"", "s2_id":s2_id(work.get("paperId")),
        "authors":"; ".join(a.get("name") or "" for a in (work.get("authors") or [])),
        "venue":work.get("venue") or "", "type":"; ".join(work.get("publicationTypes") or []),
        "abstract":work.get("abstract") or "", "url":work.get("url") or ""}


def arxiv_id(value):
    value=str(value or "").strip().lower()
    value=re.sub(r"^(https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/|https?://(?:dx\.)?doi\.org/10\.48550/arxiv\.|10\.48550/arxiv\.|arxiv:\s*)","",value)
    value=re.sub(r"(?:v\d+)?(?:\.pdf)?$","",value)
    return value if re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z][a-z.\-]+/\d{7})",value) else ""


def arxiv_query(text):
    # The shared pilot Boolean grammar is intentionally restricted.
    parts=re.findall(r'"[^"]+"|\(|\)|\bAND\b|\bOR\b|[A-Za-z0-9_.-]+',text)
    if re.sub(r"\s+","", "".join(parts))!=re.sub(r"\s+","",text):
        raise ValueError("Unsupported arXiv translation syntax; use quoted phrases and AND/OR.")
    return " ".join(t if t in ("(",")","AND","OR") else f"(ti:{t} OR abs:{t})" for t in parts)


def title_matcher(text):
    tokens=re.findall(r'"[^"]+"|\(|\)|\bAND\b|\bOR\b|[A-Za-z0-9_.-]+',text)
    if re.sub(r"\s+","","".join(tokens))!=re.sub(r"\s+","",text):
        raise ValueError("Unsupported Boolean title query syntax.")
    pos=0
    def atom():
        nonlocal pos
        if pos>=len(tokens):
            raise ValueError("Incomplete Boolean title query.")
        t=tokens[pos]; pos+=1
        if t=="(":
            node=disjunction()
            if pos>=len(tokens) or tokens[pos]!=")":
                raise ValueError("Unbalanced Boolean title query.")
            pos+=1
            return node
        if t in ("AND","OR","NOT",")"):
            raise ValueError("Unexpected Boolean operator.")
        words=normalized_title(t)
        if not words:
            raise ValueError("Empty Boolean title term.")
        needle=" "+words+" "
        return lambda title: needle in title
    def conjunction():
        nonlocal pos
        nodes=[atom()]
        while pos<len(tokens) and tokens[pos]=="AND":
            pos+=1; nodes.append(atom())
        return lambda title: all(n(title) for n in nodes)
    def disjunction():
        nonlocal pos
        nodes=[conjunction()]
        while pos<len(tokens) and tokens[pos]=="OR":
            pos+=1; nodes.append(conjunction())
        return lambda title: any(n(title) for n in nodes)
    matcher=disjunction()
    if pos!=len(tokens):
        raise ValueError("Unsupported or incomplete Boolean title expression.")
    return matcher


def stream_dblp(path,dtd,on_record,segment_limit=64*1024*1024):
    # Only the explicitly supplied local DBLP DTD may be resolved; no network.
    dtd_bytes=dtd.read_bytes()
    if not dtd_bytes or len(dtd_bytes)>1000000:
        raise ValueError("Invalid DBLP DTD size.")
    parser=None
    depth=0; current=None; field=None; chunks=[]; count=0; loaded=False
    encoding="UTF-8"
    has_dtd=False
    kinds={"article","inproceedings","proceedings","book","incollection","phdthesis","mastersthesis","data"}
    fields={"title","year","author","editor","journal","booktitle","ee"}
    def external(context,base,system,public):
        nonlocal loaded
        if loaded or context is not None or not re.fullmatch(r"dblp(?:-\d{4}-\d{2}-\d{2})?\.dtd",system or ""):
            raise ValueError("Unexpected external XML entity; only the designated local DBLP DTD is accepted.")
        loaded=True
        child=parser.ExternalEntityParserCreate(context)
        child.ExternalEntityRefHandler=lambda *args: 0
        child.Parse(dtd_bytes,True)
        return 1
    def xml_decl(version,declared,standalone):
        nonlocal encoding
        if declared:
            if declared.lower() not in ("utf-8","us-ascii","ascii","iso-8859-1","latin-1"):
                raise ValueError("Unsupported encoding for segmented DBLP snapshot parsing.")
            encoding=declared
    def doctype(name,system,public,internal):
        nonlocal has_dtd
        if name!="dblp" or internal or public or not re.fullmatch(r"dblp(?:-\d{4}-\d{2}-\d{2})?\.dtd",system or ""):
            raise ValueError("Unexpected external XML entity or unsupported DBLP DOCTYPE.")
        has_dtd=True
    def start(name,attrs):
        nonlocal depth,current,field,chunks
        depth+=1
        if depth==1 and name!="dblp":
            raise ValueError("Not a DBLP XML snapshot.")
        if depth==2 and name in kinds:
            current={"kind":name,"key":attrs.get("key",""),"fields":defaultdict(list)}
        if depth==3 and current is not None and name in fields:
            field=name; chunks=[]
    def chars(value):
        if field is not None:
            chunks.append(value)
    def end(name):
        nonlocal depth,current,field,count
        if depth==3 and current is not None and field==name:
            current["fields"][field].append(" ".join("".join(chunks).split())); field=None
        if depth==2 and current is not None:
            count+=1; on_record(current,count); current=None
        depth-=1
    def create_parser():
        nonlocal parser,loaded
        loaded=False
        parser=expat.ParserCreate()
        parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_ALWAYS)
        parser.StartElementHandler=start; parser.EndElementHandler=end; parser.CharacterDataHandler=chars
        parser.ExternalEntityRefHandler=external
        parser.XmlDeclHandler=xml_decl; parser.StartDoctypeDeclHandler=doctype
    create_parser()
    # Windows Expat builds have had a 2 GiB total-input counter limit. Reset
    # the parser well below this bound, ONLY at verified top-level boundaries.
    tags=sorted(kinds|{"www","person","reference"})
    boundary=re.compile(rb"</(?:"+b"|".join(t.encode() for t in tags)+rb")\s*>")
    segment_bytes=0; segments=1
    with gzip.open(path,"rb") if path.suffix.lower()==".gz" else path.open("rb") as source:
        try:
            while True:
                data=source.read(4*1024*1024)
                if not data:
                    break
                while data:
                    split=boundary.search(data,max(0,segment_limit-segment_bytes)) if segment_bytes+len(data)>segment_limit else None
                    if split is None:
                        parser.Parse(data,False)
                        segment_bytes+=len(data)
                        if segment_bytes>segment_limit+64*1024*1024:
                            raise RuntimeError("No complete DBLP publication boundary found within parser safety window.")
                        break
                    stop=split.end()
                    parser.Parse(data[:stop],False)
                    if depth!=1 or current is not None or field is not None:
                        raise RuntimeError("DBLP segment boundary is not a complete top-level record.")
                    parser.Parse(b"</dblp>",True)
                    create_parser()
                    header=(f'<?xml version="1.0" encoding="{encoding}"?>'+ ('<!DOCTYPE dblp SYSTEM "dblp.dtd">' if has_dtd else '')+'<dblp>').encode("ascii")
                    parser.Parse(header,False)
                    segment_bytes=len(header); segments+=1
                    data=data[stop:]
            parser.Parse(b"",True)
        except expat.ExpatError as exc:
            raise RuntimeError(f"DBLP XML parse error in segment {segments}: {exc}") from None
    if not count:
        raise ValueError("DBLP snapshot contains no publication records.")
    return count


def file_digest(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""):
            h.update(block)
    return h.hexdigest()


def dblp_search(args):
    config=json.loads(args.config.read_text(encoding="utf-8-sig"))
    start,end=config["from_date"],config["to_date"]
    if dt.date.fromisoformat(start)>dt.date.fromisoformat(end):
        raise ValueError("Invalid date range.")
    queries=[q for q in config["queries"] if q.get("mode","boolean")=="boolean" and (not args.only or q["id"] in args.only)]
    if not queries or len({q["id"] for q in queries})!=len(queries) or (args.only and set(args.only)!={q["id"] for q in queries}):
        raise ValueError("Need distinct known Boolean query IDs.")
    tests={q["id"]:title_matcher(q["text"]) for q in queries}
    matches={qid:[] for qid in tests}; seen=set()
    config.update(queries=queries,retrieval_kind="offline_exact_title_boolean_year_only",snapshot_parser="segmented_expat_64MiB",snapshot_name=args.snapshot.name,snapshot_sha256=file_digest(args.snapshot),dtd_sha256=file_digest(args.dtd),snapshot_citation=args.snapshot_citation)
    manifest=run_create(args.out,"dblp_search",config)
    def accept(record,index):
        if index%500000==0:
            print(f"DBLP: scanned {index:,} publications",flush=True)
        f=record["fields"]; year=(f.get("year") or [""])[0]; title=(f.get("title") or [""])[0]
        if not title or not re.fullmatch(r"\d{4}",year) or not start[:4]<=year<=end[:4]:
            return
        normalized=" "+normalized_title(title)+" "
        qids=[qid for qid,matcher in tests.items() if matcher(normalized)]
        if not qids:
            return
        k=record["key"]
        if not k or k in seen:
            raise RuntimeError("Missing or repeated DBLP record key among matching publications.")
        seen.add(k)
        ds=sorted({doi(x) for x in f.get("ee",[]) if doi(x)})
        row={"title":title,"year":year,"publication_date":"","doi":ds[0] if len(ds)==1 else "","openalex_id":"","dblp_id":k,"dblp_doi_candidates":";".join(ds),"authors":"; ".join(f.get("author") or f.get("editor") or []),"venue":(f.get("journal") or f.get("booktitle") or [""])[0],"type":record["kind"],"abstract":"","url":"https://dblp.org/rec/"+k}
        for qid in qids:
            matches[qid].append(row)
    try:
        manifest["scanned_publications"]=stream_dblp(args.snapshot,args.dtd,accept)
        if file_digest(args.snapshot)!=config["snapshot_sha256"] or file_digest(args.dtd)!=config["dtd_sha256"]:
            raise RuntimeError("Snapshot or DTD changed during scan.")
        for qid,rows in matches.items():
            name=f"raw/{qid}-000001.json"; body=json_bytes({"results":rows})
            (args.out/name).write_bytes(body)
            manifest["pages"].append({"query_id":qid,"path":name,"sha256":digest(body),"retrieved_utc":now(),"rows":len(rows)})
            manifest["queries"].append({"query_id":qid,"status":"exhausted","retrieved_occurrences":len(rows),"hits_first_page":len(rows),"pages":1})
            print(f"{qid}: {len(rows)} title-matched records",flush=True)
        manifest["status"]="complete"
    except (Exception,KeyboardInterrupt):
        manifest["status"]="failed_or_interrupted"
        raise
    finally:
        manifest["finished_utc"]=now(); save_manifest(args.out,manifest)


def arxiv_page(body):
    try:
        root=ET.fromstring(body)
        total=int(root.findtext("o:totalResults",namespaces=ATOM))
        start=int(root.findtext("o:startIndex",namespaces=ATOM))
    except (ET.ParseError,TypeError,ValueError):
        raise RuntimeError("Invalid arXiv Atom feed or pagination metadata.") from None
    entries=root.findall("a:entry",ATOM)
    rows=[]
    for entry in entries:
        field=lambda tag: " ".join((entry.findtext(tag,default="",namespaces=ATOM) or "").split())
        url=field("a:id")
        aid=arxiv_id(url)
        title=field("a:title")
        if not aid or not title:
            raise RuntimeError("arXiv returned an error entry or invalid paper identifier.")
        published=field("a:published")[:10]
        related=doi(field("x:doi"))
        direct=related if related.startswith("10.48550/arxiv.") and arxiv_id(related)==aid else ""
        rows.append({"title":title,"year":published[:4],"publication_date":published,"doi":direct,"openalex_id":"",
            "arxiv_id":aid,"arxiv_version_url":url,"arxiv_related_doi":related if related!=direct else "",
            "authors":"; ".join(" ".join((a.findtext("a:name",default="",namespaces=ATOM) or "").split()) for a in entry.findall("a:author",ATOM)),
            "venue":"arXiv","type":"preprint","abstract":field("a:summary"),"url":url})
    if total<0 or start<0:
        raise RuntimeError("Invalid arXiv pagination counts.")
    return total,start,rows


class Arxiv:
    def __init__(self,delay=3.1,timeout=60):
        self.delay,self.timeout=delay,timeout

    def fetch(self,params):
        url=ARXIV_API+"?"+urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"SE-ReviewKit/"+VERSION,"Accept":"application/atom+xml"}),timeout=self.timeout) as response:
                body=response.read()
            time.sleep(self.delay)
            return body
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"arXiv HTTP {exc.code}: stop and check API service, access, or query syntax.") from None
        except (urllib.error.URLError,TimeoutError,OSError):
            raise RuntimeError("arXiv connection failed; check network access.") from None


def crossref_page(data):
    message=data.get("message") if isinstance(data,dict) else None
    if not isinstance(message,dict) or data.get("status")!="ok":
        raise RuntimeError("Unexpected Crossref response: missing successful message.")
    total,rows=message.get("total-results"),message.get("items")
    if not isinstance(total,int) or total<0 or not isinstance(rows,list) or any(not isinstance(r,dict) or not doi(r.get("DOI")) for r in rows):
        raise RuntimeError("Invalid Crossref results or missing DOI.")
    return total,rows


def normalize_crossref(work):
    def first(field):
        values=work.get(field) or []
        return str(values[0]) if isinstance(values,list) and values else ""
    parts=(work.get("published") or {}).get("date-parts") or []
    parts=parts[0] if parts else []
    year,date="",""
    if parts and isinstance(parts[0],int) and 1<=parts[0]<=9999:
        year=f"{parts[0]:04d}"
        if len(parts)==3:
            try:
                date=dt.date(*parts).isoformat()
            except (ValueError,TypeError):
                pass
    clean=lambda value: " ".join(html.unescape(re.sub(r"<[^>]*>"," ",str(value or ""))).split())
    names=[" ".join(str(a.get(k) or "") for k in ("given","family")).strip() or str(a.get("name") or "") for a in (work.get("author") or []) if isinstance(a,dict)]
    d=doi(work.get("DOI"))
    return {"title":clean(first("title")),"year":year,"publication_date":date,"doi":d,"openalex_id":"",
            "authors":"; ".join(n for n in names if n),"venue":clean(first("container-title")),"type":work.get("type") or "",
            "abstract":clean(work.get("abstract")),"url":"https://doi.org/"+d}


class Crossref:
    def __init__(self,mailto="",delay=2.0,timeout=60):
        self.mailto,self.delay,self.timeout=mailto,delay,timeout

    def fetch(self,params):
        url=CROSSREF_API+"?"+urllib.parse.urlencode({**params,**({"mailto":self.mailto} if self.mailto else {})})
        try:
            request=urllib.request.Request(url,headers={"User-Agent":"SE-ReviewKit/"+VERSION,"Accept":"application/json"})
            with urllib.request.urlopen(request,timeout=self.timeout) as response:
                body=response.read()
            time.sleep(self.delay)
            return json.loads(body),body
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Crossref HTTP {exc.code}: stop and check API rate limits or availability; do not repeatedly retry.") from None
        except (urllib.error.URLError,TimeoutError,OSError):
            raise RuntimeError("Crossref connection failed; check network access.") from None
        except (ValueError,UnicodeError):
            raise RuntimeError("Crossref returned a non-JSON response; no data accepted.") from None


class SemanticScholar:
    def __init__(self, key="", delay=1.1, retries=3, timeout=60):
        self.key, self.delay, self.retries, self.timeout = key, delay, retries, timeout

    def fetch(self, params):
        url = S2_API + "?" + urllib.parse.urlencode(params)
        headers = {"Accept":"application/json","User-Agent":"SE-ReviewKit/"+VERSION}
        if self.key:
            headers["x-api-key"] = self.key
        for n in range(self.retries+1):
            try:
                with urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=self.timeout) as response:
                    body=response.read()
                if self.key and self.key.encode() in body:
                    raise RuntimeError("Semantic Scholar response contains credential material; page not saved.")
                time.sleep(self.delay)
                return json.loads(body),body
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    raise RuntimeError("Semantic Scholar HTTP 429: rate limit; wait or request a free API key before a new run.") from None
                if exc.code in (401,403):
                    raise RuntimeError(f"Semantic Scholar HTTP {exc.code}: authentication or entitlement required.") from None
                if exc.code not in (500,502,503,504) or n==self.retries:
                    raise RuntimeError(f"Semantic Scholar HTTP {exc.code}: check query syntax or service availability.") from None
                time.sleep(min(60,2**n))
            except (urllib.error.URLError, TimeoutError, OSError):
                if n==self.retries:
                    raise RuntimeError("Semantic Scholar connection failed; check network access.") from None
                time.sleep(min(60,2**n))
        raise AssertionError("unreachable")


class Scopus:
    """Offset pagination for authorized Scopus Search API access."""
    def __init__(self, key, delay=1.1, retries=3, timeout=40, insttoken=None):
        self.key, self.delay, self.retries, self.timeout, self.insttoken = key, delay, retries, timeout, insttoken

    def fetch(self, params):
        url = SCOPUS_API + "?" + urllib.parse.urlencode(params)
        headers = {"X-ELS-APIKey": self.key, "Accept": "application/json", "User-Agent": "SE-ReviewKit/" + VERSION}
        if self.insttoken:
            headers["X-ELS-Insttoken"] = self.insttoken
        for n in range(self.retries + 1):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=self.timeout) as response:
                    body = response.read()
                # Never persist reflected credentials in raw responses.
                if any(secret and secret.encode() in body for secret in (self.key, self.insttoken)):
                    raise RuntimeError("Scopus response contains credential material; raw page not saved.")
                time.sleep(self.delay)
                return json.loads(body), body
            except urllib.error.HTTPError as exc:
                status = exc.code
                if status in (401, 403):
                    raise RuntimeError(f"Scopus HTTP {status}: verify API entitlement and authorized institutional network; do not retry or purchase access.") from None
                if status == 429:
                    raise RuntimeError("Scopus HTTP 429: quota or throttling limit; stop and check account limits.") from None
                if status not in (500, 502, 503, 504) or n == self.retries:
                    raise RuntimeError(f"Scopus HTTP {status}: check API query syntax or service availability.") from None
                time.sleep(min(60, 2 ** n))
            except (urllib.error.URLError, TimeoutError, OSError):
                if n == self.retries:
                    raise RuntimeError("Scopus connection failed; check network access.") from None
                time.sleep(min(60, 2 ** n))
        raise AssertionError("unreachable")


def csv_write(path, rows, fields):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


class OpenAlex:
    def __init__(self, key, delay=1.1, retries=4, timeout=40):
        self.key, self.delay, self.retries, self.timeout = key, delay, retries, timeout

    def fetch(self, params, path=""):
        # Key stays in HTTP Authorization, never in URL, files, or diagnostics.
        url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
        for n in range(self.retries + 1):
            req = urllib.request.Request(url, headers={"Authorization": "Bearer " + self.key, "User-Agent": "SE-ReviewKit/" + VERSION, "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    body = response.read()
                time.sleep(self.delay)
                return json.loads(body), body
            except urllib.error.HTTPError as exc:
                status = exc.code
                if status not in (429, 500, 502, 503, 504) or n == self.retries:
                    detail = ""
                    try:
                        error = json.loads(exc.read(4096))
                        detail = " ".join(str(error.get(k, "")) for k in ("error", "message")).strip()
                        detail = detail.replace(self.key, "[REDACTED]").replace("\r", " ").replace("\n", " ")[:500]
                    except (ValueError, OSError, AttributeError):
                        pass
                    raise RuntimeError(f"OpenAlex HTTP {status}: {detail or 'examine key, quota, and query syntax.'}") from None
                retry = exc.headers.get("Retry-After", "")
                time.sleep(max(self.delay, min(60, int(retry)) if retry.isdigit() else min(60, 2 ** n)))
            except (urllib.error.URLError, TimeoutError, OSError):
                if n == self.retries:
                    raise RuntimeError("OpenAlex connection failed; check network access.") from None
                time.sleep(min(60, 2 ** n))
        raise AssertionError("unreachable")


def key():
    value = os.environ.get("OPENALEX_API_KEY") or getpass.getpass("OpenAlex API key (hidden): ")
    if not value.strip():
        raise ValueError("An API key is required for cursor paging.")
    return value.strip()


def run_create(out, kind, config):
    out.mkdir(parents=True, exist_ok=False)
    (out / "raw").mkdir()
    write_json(out / "config.json", config)
    manifest = {"tool_version": VERSION, "kind": kind, "started_utc": now(), "status": "running", "config_sha256": digest((out / "config.json").read_bytes()), "queries": [], "pages": []}
    write_json(out / "manifest.json", manifest)
    return manifest


def save_manifest(out, manifest):
    write_json(out / "manifest.json", manifest)


def scopus_page(data):
    result = data.get("search-results")
    if not isinstance(result, dict):
        raise RuntimeError("Unexpected Scopus response: missing search-results.")
    try:
        total = int(result["opensearch:totalResults"])
        start = int(result["opensearch:startIndex"])
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("Unexpected Scopus response: missing pagination metadata.") from None
    rows = result.get("entry") or []
    if total == 0 and isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict) and "error" in rows[0]:
        rows = []
    if total < 0 or start < 0 or not isinstance(rows, list) or any(not isinstance(r, dict) or not r.get("dc:title") for r in rows):
        raise RuntimeError("Unexpected Scopus response: invalid result records.")
    return total, start, rows


def retrieve_scopus(client, out, manifest, query_id, params, max_pages=None):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", query_id):
        raise ValueError("Invalid query identifier.")
    entry = {"query_id":query_id,"params":params,"started_utc":now(),"hits_first_page":None,
             "retrieved_occurrences":0,"pages":0,"status":"running"}
    manifest["queries"].append(entry)
    save_manifest(out, manifest)
    offset, seen_ids = 0, set()
    while True:
        data, body = client.fetch({**params, "start":offset})
        total, reported_start, rows = scopus_page(data)
        if reported_start != offset or len(rows) > params["count"]:
            raise RuntimeError("Scopus pagination mismatch; run not complete.")
        if entry["pages"] == 0:
            entry["hits_first_page"] = total
        if total != entry["hits_first_page"]:
            raise RuntimeError("Scopus reported hit count changed during pagination; run not complete.")
        if offset + len(rows) > total or (not rows and offset < total):
            raise RuntimeError("Scopus returned inconsistent page length; run not complete.")
        page_ids = [scopus_id(r.get("eid")) or scopus_id(r.get("dc:identifier")) for r in rows]
        ids = [s for s in page_ids if s]
        if len(ids) != len(set(ids)) or seen_ids.intersection(ids):
            raise RuntimeError("Scopus returned repeated document IDs within a query; run not complete.")
        seen_ids.update(ids)
        entry["pages"] += 1
        entry["retrieved_occurrences"] += len(rows)
        filename = f"raw/{query_id}-{entry['pages']:06d}.json"
        (out / filename).write_bytes(body)
        manifest["pages"].append({"query_id":query_id,"path":filename,"sha256":digest(body),"retrieved_utc":now(),"rows":len(rows)})
        save_manifest(out, manifest)
        print(f"{query_id}: page {entry['pages']}, {entry['retrieved_occurrences']}/{total} records", flush=True)
        offset += len(rows)
        if offset >= total:
            entry["status"] = "exhausted"
            break
        if max_pages and entry["pages"] >= max_pages:
            entry["status"] = "capped"
            break
        # Offset navigation is limited to 5,000 results; do not silently truncate.
        if offset >= 5000 or total > 5000:
            raise RuntimeError("Scopus offset limit: query exceeds 5,000 results. Stop; use authorized cursor access or prespecified year partitions.")
    entry["finished_utc"] = now()
    save_manifest(out, manifest)


def scopus_search(args):
    config = json.loads(args.config.read_text(encoding="utf-8"))
    start, end = config["from_date"], config["to_date"]
    if dt.date.fromisoformat(start) > dt.date.fromisoformat(end):
        raise ValueError("Invalid date range.")
    selected = [q for q in config["queries"] if not args.only or q["id"] in args.only]
    if not selected or len({q["id"] for q in selected}) != len(selected) or (args.only and set(args.only) != {q["id"] for q in selected}):
        raise ValueError("Need distinct known query IDs.")
    if not 1 <= args.count <= (25 if args.view == "COMPLETE" else 200):
        raise ValueError("Page size outside allowed range for selected Scopus view.")
    config["queries"] = selected
    config["view"] = args.view
    config["count"] = args.count
    config["date_filter_granularity"] = "year"
    secret = os.environ.get("SCOPUS_API_KEY") or getpass.getpass("Scopus API key (hidden): ")
    if not secret.strip():
        raise ValueError("Scopus API key required.")
    client = Scopus(secret.strip(), delay=args.delay, insttoken=os.environ.get("SCOPUS_INST_TOKEN"))
    manifest = run_create(args.out, "scopus_search", config)
    try:
        for q in selected:
            retrieve_scopus(client,args.out,manifest,q["id"],{"query":q["text"],"date":f"{start[:4]}-{end[:4]}","view":args.view,"count":args.count},args.max_pages)
        manifest["status"] = "complete" if all(q["status"] == "exhausted" for q in manifest["queries"]) else "partial_capped"
    except (Exception, KeyboardInterrupt):
        manifest["status"] = "failed_or_interrupted"
        raise
    finally:
        manifest["finished_utc"] = now()
        save_manifest(args.out, manifest)


def s2_page(data):
    rows=data.get("data")
    total=data.get("total")
    token=data.get("token")
    if not isinstance(rows,list) or not isinstance(total,int) or total<0 or (token is not None and not isinstance(token,str)):
        raise RuntimeError("Unexpected Semantic Scholar bulk response.")
    if any(not isinstance(r,dict) or not r.get("title") or not s2_id(r.get("paperId")) for r in rows):
        raise RuntimeError("Invalid Semantic Scholar paper metadata.")
    if len(rows)>1000:
        raise RuntimeError("Semantic Scholar bulk page exceeds expected size.")
    return total,rows,token


def retrieve_s2(client,out,manifest,qid,params,max_pages=None):
    if not re.fullmatch(r"[A-Za-z0-9_-]+",qid):
        raise ValueError("Invalid query identifier.")
    entry={"query_id":qid,"params":params,"started_utc":now(),"hits_first_page":None,"retrieved_occurrences":0,"pages":0,"status":"running"}
    manifest["queries"].append(entry)
    save_manifest(out,manifest)
    token=None
    seen_tokens=set()
    seen_ids=set()
    while True:
        request={**params,**({"token":token} if token else {})}
        data,body=client.fetch(request)
        total,rows,next_token=s2_page(data)
        if entry["pages"]==0:
            entry["hits_first_page"]=total
        ids=[s2_id(r["paperId"]) for r in rows]
        if len(ids)!=len(set(ids)) or seen_ids.intersection(ids):
            raise RuntimeError("Semantic Scholar repeated a paper ID within one query; run not complete.")
        seen_ids.update(ids)
        entry["pages"]+=1
        entry["retrieved_occurrences"]+=len(rows)
        filename=f"raw/{qid}-{entry['pages']:06d}.json"
        (out/filename).write_bytes(body)
        manifest["pages"].append({"query_id":qid,"path":filename,"sha256":digest(body),"retrieved_utc":now(),"rows":len(rows)})
        save_manifest(out,manifest)
        print(f"{qid}: page {entry['pages']}, {entry['retrieved_occurrences']} records (estimated hits {total})",flush=True)
        if not next_token:
            entry["status"]="exhausted"
            break
        if next_token in seen_tokens or not rows:
            raise RuntimeError("Semantic Scholar invalid or repeated continuation token; run not complete.")
        seen_tokens.add(next_token)
        if max_pages and entry["pages"]>=max_pages:
            entry["status"]="capped"
            break
        if entry["retrieved_occurrences"]>=10000000:
            raise RuntimeError("Semantic Scholar bulk retrieval limit reached; run not complete.")
        token=next_token
    entry["finished_utc"]=now()
    save_manifest(out,manifest)


def s2_search(args):
    config=json.loads(args.config.read_text(encoding="utf-8"))
    start,end=config["from_date"],config["to_date"]
    if dt.date.fromisoformat(start)>dt.date.fromisoformat(end):
        raise ValueError("Invalid date range.")
    queries=[q for q in config["queries"] if not args.only or q["id"] in args.only]
    if not queries or len({q["id"] for q in queries})!=len(queries) or (args.only and set(args.only)!={q["id"] for q in queries}):
        raise ValueError("Need distinct known query IDs.")
    config["queries"]=queries
    config["endpoint"]="paper/search/bulk"
    secret="" if args.anonymous else (os.environ.get("SEMANTIC_SCHOLAR_API_KEY") or getpass.getpass("Semantic Scholar API key (hidden; Enter for anonymous): ")).strip()
    client=SemanticScholar(secret,delay=args.delay)
    manifest=run_create(args.out,"s2_search",config)
    try:
        for q in queries:
            retrieve_s2(client,args.out,manifest,q["id"],{"query":q["text"],"publicationDateOrYear":f"{start}:{end}","fields":S2_FIELDS,"sort":"paperId:asc"},args.max_pages)
        manifest["status"]="complete" if all(q["status"]=="exhausted" for q in manifest["queries"]) else "partial_capped"
    except (Exception,KeyboardInterrupt):
        manifest["status"]="failed_or_interrupted"
        raise
    finally:
        manifest["finished_utc"]=now()
        save_manifest(args.out,manifest)


def retrieve_arxiv(client,out,manifest,qid,params,max_pages=None):
    if not re.fullmatch(r"[A-Za-z0-9_-]+",qid):
        raise ValueError("Invalid query identifier.")
    entry={"query_id":qid,"params":params,"started_utc":now(),"hits_first_page":None,"retrieved_occurrences":0,"pages":0,"status":"running"}
    manifest["queries"].append(entry)
    save_manifest(out,manifest)
    offset=0
    seen=set()
    while True:
        body=client.fetch({**params,"start":offset})
        total,start,rows=arxiv_page(body)
        if entry["pages"]==0:
            entry["hits_first_page"]=total
        if start!=offset or total!=entry["hits_first_page"] or len(rows)!=min(params["max_results"],total-offset):
            raise RuntimeError("arXiv pagination changed or returned an incomplete page; run not complete.")
        ids=[r["arxiv_id"] for r in rows]
        if len(set(ids))!=len(ids) or seen.intersection(ids):
            raise RuntimeError("arXiv repeated a paper within a query; run not complete.")
        seen.update(ids)
        entry["pages"]+=1
        offset+=len(rows)
        entry["retrieved_occurrences"]=offset
        filename=f"raw/{qid}-{entry['pages']:06d}.xml"
        (out/filename).write_bytes(body)
        manifest["pages"].append({"query_id":qid,"path":filename,"sha256":digest(body),"retrieved_utc":now(),"rows":len(rows)})
        save_manifest(out,manifest)
        print(f"{qid}: page {entry['pages']}, {offset}/{total} records",flush=True)
        if offset==total:
            entry["status"]="exhausted"
            break
        if max_pages and entry["pages"]>=max_pages:
            entry["status"]="capped"
            break
        if total>30000:
            raise RuntimeError("arXiv query exceeds 30,000 results; refine or partition the date range.")
    entry["finished_utc"]=now()
    save_manifest(out,manifest)


def arxiv_search(args):
    config=json.loads(args.config.read_text(encoding="utf-8-sig"))
    start,end=config["from_date"],config["to_date"]
    if dt.date.fromisoformat(start)>dt.date.fromisoformat(end) or not 1<=args.count<=200 or args.delay<3:
        raise ValueError("Need valid dates, --count 1..200, and --delay >=3 seconds.")
    queries=[q for q in config["queries"] if q.get("mode","boolean")=="boolean" and (not args.only or q["id"] in args.only)]
    if not queries or len({q["id"] for q in queries})!=len(queries) or (args.only and set(args.only)!={q["id"] for q in queries}):
        raise ValueError("Need distinct known Boolean query IDs; semantic queries are not translated.")
    config["skipped_query_ids"]=[q["id"] for q in config["queries"] if q not in queries]
    config["queries"]=queries
    config["date_field"]="submittedDate UTC"
    tasks=[]
    for q in queries:
        translated=f"({arxiv_query(q['text'])}) AND submittedDate:[{start.replace('-','')}0000 TO {end.replace('-','')}2359]"
        tasks.append(("A"+q["id"][1:] if q["id"].startswith("Q") else q["id"],{"search_query":translated,"sortBy":"submittedDate","sortOrder":"ascending","max_results":args.count}))
    manifest=run_create(args.out,"arxiv_search",config)
    try:
        client=Arxiv(delay=args.delay)
        for qid,params in tasks:
            retrieve_arxiv(client,args.out,manifest,qid,params,args.max_pages)
        manifest["status"]="complete" if all(q["status"]=="exhausted" for q in manifest["queries"]) else "partial_capped"
    except (Exception,KeyboardInterrupt):
        manifest["status"]="failed_or_interrupted"
        raise
    finally:
        manifest["finished_utc"]=now()
        save_manifest(args.out,manifest)


def retrieve_crossref(client,out,manifest,qid,params):
    if not re.fullmatch(r"[A-Za-z0-9_-]+",qid):
        raise ValueError("Invalid query identifier.")
    q={"query_id":qid,"params":params,"started_utc":now(),"status":"running","pages":0,"retrieved_occurrences":0,"hits_first_page":None}
    manifest["queries"].append(q)
    save_manifest(out,manifest)
    data,body=client.fetch(params)
    total,rows=crossref_page(data)
    ids=[doi(r["DOI"]) for r in rows]
    if len(rows)!=min(total,params["rows"]) or len(set(ids))!=len(ids):
        raise RuntimeError("Crossref returned an inconsistent or duplicate-ranked result list; run not complete.")
    filename=f"raw/{qid}-000001.json"
    (out/filename).write_bytes(body)
    manifest["pages"].append({"query_id":qid,"path":filename,"sha256":digest(body),"retrieved_utc":now(),"rows":len(rows)})
    q.update(hits_first_page=total,retrieved_occurrences=len(rows),pages=1,status="ranked_limit",finished_utc=now())
    save_manifest(out,manifest)
    print(f"{qid}: ranked discovery, {len(rows)} records (provider hits {total})",flush=True)


def crossref_search(args):
    config=json.loads(args.config.read_text(encoding="utf-8-sig"))
    start,end=config["from_date"],config["to_date"]
    if dt.date.fromisoformat(start)>dt.date.fromisoformat(end) or not 1<=args.top<=1000 or args.delay<2:
        raise ValueError("Need valid dates, --top 1..1000, and --delay >=2 seconds.")
    queries=[q for q in config["queries"] if not args.only or q["id"] in args.only]
    if not queries or len({q["id"] for q in queries})!=len(queries) or (args.only and set(args.only)!={q["id"] for q in queries}):
        raise ValueError("Need distinct known query IDs.")
    if any(q.get("mode")!="ranked" or not isinstance(q.get("text"),str) or not q["text"].strip() or re.search(r"\b(?:AND|OR|NOT)\b",q["text"]) for q in queries):
        raise ValueError("Crossref requires explicit ranked natural-language queries, not Boolean expressions.")
    config.update(queries=queries,ranked_top_per_query=args.top,retrieval_kind="bounded_ranked_discovery")
    manifest=run_create(args.out,"crossref_search",config)
    try:
        client=Crossref(os.environ.get("CROSSREF_MAILTO","").strip(),delay=args.delay)
        for q in queries:
            retrieve_crossref(client,args.out,manifest,q["id"],{"query.bibliographic":q["text"],"filter":f"from-pub-date:{start},until-pub-date:{end}","sort":"score","order":"desc","rows":args.top})
        manifest["status"]="complete"
    except (Exception,KeyboardInterrupt):
        manifest["status"]="failed_or_interrupted"
        raise
    finally:
        manifest["finished_utc"]=now()
        save_manifest(args.out,manifest)


def retrieve(client, out, manifest, query_id, params, max_pages=None):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", query_id):
        raise ValueError("Query identifier must be alphanumeric, underscore or hyphen.")
    entry = {"query_id": query_id, "params": params, "started_utc": now(), "hits_first_page": None, "retrieved_occurrences": 0, "pages": 0, "status": "running"}
    manifest["queries"].append(entry)
    save_manifest(out, manifest)
    if "search.semantic" in params:
        # Semantic retrieval is a bounded ranked list, not cursor-paginated.
        # OpenAlex supports year filters here, but not exact date filters.
        request = {**params, "per_page": 50, "select": FIELDS}
        data, body = client.fetch(request)
        results = data.get("results")
        if not isinstance(results, list) or len(results) > 50:
            raise RuntimeError("Unexpected OpenAlex semantic response.")
        meta = data.get("meta") or {}
        filename = f"raw/{query_id}-000001.json"
        (out / filename).write_bytes(body)
        entry.update(hits_first_page=meta.get("count"), executed_query=(meta.get("x_query") or {}).get("oql"), retrieved_occurrences=len(results), pages=1, status="ranked_limit", finished_utc=now(), request_params=request)
        manifest["pages"].append({"query_id": query_id, "path": filename, "sha256": digest(body), "retrieved_utc": now(), "rows": len(results)})
        save_manifest(out, manifest)
        print(f"{query_id}: bounded semantic retrieval, {len(results)} records", flush=True)
        return
    cursor, seen = "*", set()
    while cursor:
        if cursor in seen:
            raise RuntimeError("Repeated cursor; retrieval not complete.")
        seen.add(cursor)
        data, body = client.fetch({**params, "cursor": cursor, "per_page": 100, "select": FIELDS})
        meta = data.get("meta") or {}
        results = data.get("results")
        if not isinstance(results, list) or "next_cursor" not in meta:
            raise RuntimeError("Unexpected OpenAlex page response.")
        if entry["pages"] == 0:
            entry["hits_first_page"] = meta.get("count")
            entry["executed_query"] = (meta.get("x_query") or {}).get("oql")
        entry["pages"] += 1
        entry["retrieved_occurrences"] += len(results)
        filename = f"raw/{query_id}-{entry['pages']:06d}.json"
        (out / filename).write_bytes(body)
        manifest["pages"].append({"query_id": query_id, "path": filename, "sha256": digest(body), "retrieved_utc": now(), "rows": len(results)})
        cursor = meta.get("next_cursor")
        save_manifest(out, manifest)
        print(f"{query_id}: page {entry['pages']}, {entry['retrieved_occurrences']} records", flush=True)
        if max_pages and entry["pages"] >= max_pages and cursor:
            entry["status"] = "capped"
            break
    else:
        entry["status"] = "exhausted"
    entry["finished_utc"] = now()
    save_manifest(out, manifest)


def execute_run(out, kind, config, tasks, client, cap):
    manifest = run_create(out, kind, config)
    try:
        for qid, params in tasks:
            retrieve(client, out, manifest, qid, params, cap)
        manifest["status"] = "complete" if all(q["status"] in ("exhausted", "ranked_limit") for q in manifest["queries"]) else "partial_capped"
    except (Exception, KeyboardInterrupt):
        manifest["status"] = "failed_or_interrupted"
        raise
    finally:
        manifest["finished_utc"] = now()
        save_manifest(out, manifest)


def search(args):
    config = json.loads(args.config.read_text(encoding="utf-8"))
    start, end = config["from_date"], config["to_date"]
    if dt.date.fromisoformat(start) > dt.date.fromisoformat(end):
        raise ValueError("Invalid date range.")
    tasks = []
    for q in config["queries"]:
        if args.only and q["id"] not in args.only:
            continue
        mode = q.get("mode", "boolean")
        if mode not in ("boolean", "semantic"):
            raise ValueError("Unsupported query mode.")
        if mode == "boolean":
            params = {"search.title_and_abstract": q["text"], "filter": f"from_publication_date:{start},to_publication_date:{end}"}
        else:
            params = {"search.semantic": q["text"], "filter": f"publication_year:{start[:4]}-{end[:4]}"}
        tasks.append((q["id"], params))
    if args.only and set(args.only) != {q[0] for q in tasks}:
        raise ValueError("Unknown --only query identifier.")
    if not tasks or len({q[0] for q in tasks}) != len(tasks):
        raise ValueError("Need distinct query IDs.")
    config["queries"] = [q for q in config["queries"] if q["id"] in {task[0] for task in tasks}]
    execute_run(args.out, "search", config, tasks, OpenAlex(key(), delay=args.delay), args.max_pages)


def chase(args):
    ids = [work_id(line) for line in args.seeds.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not ids or not all(ids):
        raise ValueError("Seed file must contain one valid OpenAlex W identifier per line.")
    if dt.date.fromisoformat(args.from_date) > dt.date.fromisoformat(args.to_date):
        raise ValueError("Invalid date range.")
    client = OpenAlex(key(), delay=args.delay)
    tasks = []
    config = {"seeds": sorted(set(ids)), "from_date": args.from_date, "to_date": args.to_date, "direction": args.direction}
    dates = f"from_publication_date:{args.from_date},to_publication_date:{args.to_date}"
    for wid in config["seeds"]:
        if args.direction in ("forward", "both"):
            tasks.append((f"forward-{wid}", {"filter": f"cites:{wid},{dates}"}))
        if args.direction in ("backward", "both"):
            seed, _ = client.fetch({"select": "id,referenced_works"}, "/" + wid)
            refs = sorted({work_id(v) for v in seed.get("referenced_works", [])} - {""})
            config.setdefault("backward_seed_reference_ids", {})[wid] = refs
            for offset in range(0, len(refs), 50):
                tasks.append((f"backward-{wid}-{offset//50+1}", {"filter": "openalex:" + "|".join(refs[offset:offset+50]) + "," + dates}))
    if not tasks:
        raise ValueError("No indexed citation neighbors for the selected seed(s).")
    execute_run(args.out, "citation_chasing", config, tasks, client, args.max_pages)


def read_run(run, allow_partial):
    m = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    if m["status"] != "complete" and not allow_partial:
        raise ValueError(f"Incomplete run: {run}; pass --allow-partial only for pilot exploration.")
    if digest((run / "config.json").read_bytes()) != m["config_sha256"]:
        raise ValueError(f"Config checksum mismatch: {run}")
    config = json.loads((run / "config.json").read_text(encoding="utf-8"))
    for p in m["pages"]:
        path = run / p["path"]
        if not path.resolve().is_relative_to(run.resolve()):
            raise ValueError("Invalid page path.")
        body = path.read_bytes()
        if digest(body) != p["sha256"]:
            raise ValueError(f"Page checksum mismatch: {path}")
        is_arxiv = m.get("kind") == "arxiv_search"
        data = None if is_arxiv else json.loads(body)
        is_scopus = m.get("kind") == "scopus_search"
        is_s2 = m.get("kind") == "s2_search"
        is_crossref = m.get("kind") == "crossref_search"
        is_dblp = m.get("kind") == "dblp_search"
        results = arxiv_page(body)[2] if is_arxiv else scopus_page(data)[2] if is_scopus else s2_page(data)[1] if is_s2 else crossref_page(data)[1] if is_crossref else data["results"]
        if len(results) != p["rows"]:
            raise ValueError(f"Page count mismatch: {path}")
        for i, work in enumerate(results, 1):
            item = work if is_arxiv or is_dblp else normalize_scopus(work) if is_scopus else normalize_s2(work) if is_s2 else normalize_crossref(work) if is_crossref else normalize_work(work)
            date = item.get("publication_date") or ""
            scope = "unknown"
            if config.get("from_date") and config.get("to_date") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                scope = "in_window" if config["from_date"] <= date <= config["to_date"] else "outside_window"
                if is_scopus:
                    scope = "cover_date_" + scope
            if (is_s2 or is_crossref or is_dblp) and scope == "unknown" and re.fullmatch(r"\d{4}",item["year"]) and config.get("from_date") and config.get("to_date"):
                scope = "year_in_window_date_unknown" if config["from_date"][:4] <= item["year"] <= config["to_date"][:4] else "year_outside_window"
            yield item, {"source": "DBLP" if is_dblp else "arXiv" if is_arxiv else "Scopus" if is_scopus else "Semantic Scholar" if is_s2 else "Crossref" if is_crossref else "OpenAlex", "query_id": p["query_id"], "raw_path": str(path), "row": i, "sha256": p["sha256"], "date_scope": scope}


def read_csv(source, path):
    file_hash = digest(path.read_bytes())
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for i, row in enumerate(csv.DictReader(handle), 2):
            fields = {k.strip().casefold(): str(v or "") for k, v in row.items() if k is not None}
            def get(*names):
                return next((fields[n] for n in names if fields.get(n)), "")
            item = {"title": get("title", "article title", "document title"), "year": get("year", "publication year", "pubyear"), "publication_date": get("publication date", "publication_date"), "doi": doi(get("doi", "digital object identifier")), "openalex_id": work_id(get("openalex_id")), "authors": get("authors", "author", "author full names"), "venue": get("source title", "venue", "publication title", "journal"), "type": get("document type", "type"), "abstract": get("abstract"), "url": get("url", "link", "document url")}
            if source.casefold() == "scopus":
                item["scopus_id"] = scopus_id(get("eid", "scopus_id", "scopus id"))
            if not item["title"]:
                raise ValueError(f"Missing title at {path}:row {i}")
            yield item, {"source": source, "query_id": "institutional_export", "raw_path": str(path), "row": i, "sha256": file_hash, "date_scope": "not_evaluated"}


def consolidate(occurrences):
    # Union exact shared identifiers, never title-only candidates. Conflicting
    # DOI or OpenAlex identity is retained for adjudication rather than hidden.
    if any("arxiv_id" in i for i,_ in occurrences):
        occurrences=[({**i,"arxiv_id":i.get("arxiv_id") or arxiv_id(i["doi"]) or (arxiv_id(i["url"]) if not i["doi"] and re.match(r"https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/",i["url"]) else "")},p) for i,p in occurrences]
    identity_fields=("doi","openalex_id","scopus_id","s2_id","arxiv_id","dblp_id")
    parent = list(range(len(occurrences)))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    seen = {}
    for i, (item, provenance) in enumerate(occurrences):
        for name in identity_fields:
            if item.get(name):
                k = (name, item[name])
                if k in seen:
                    a, b = find(i), find(seen[k])
                    parent[max(a, b)] = min(a, b)
                else:
                    seen[k] = i
        if not any(item.get(name) for name in identity_fields):
            k = ("exact_occurrence", provenance["source"], provenance["sha256"], provenance["row"])
            if k in seen:
                a, b = find(i), find(seen[k])
                parent[max(a, b)] = min(a, b)
            else:
                seen[k] = i
    groups = defaultdict(list)
    for i, pair in enumerate(occurrences):
        groups[find(i)].append(pair)
    records, provenance, conflicts = [], [], []
    for group in groups.values():
        ids = sorted({i["doi"] for i, _ in group if i["doi"]})
        wids = sorted({i["openalex_id"] for i, _ in group if i["openalex_id"]})
        sids = sorted({i["scopus_id"] for i, _ in group if i.get("scopus_id")})
        s2ids = sorted({i["s2_id"] for i, _ in group if i.get("s2_id")})
        aids=sorted({i["arxiv_id"] for i,_ in group if i.get("arxiv_id")})
        dids=sorted({i["dblp_id"] for i,_ in group if i.get("dblp_id")})
        signature = ids + wids + sids + s2ids + ["arxiv:"+a for a in aids] + ["dblp:"+d for d in dids] or sorted({p["source"] + ":" + p["sha256"] + ":" + str(p["row"]) for _, p in group})
        rid = "R-" + digest(";".join(signature).encode())[:20]
        row = {k: max((i[k] for i, _ in group), key=lambda v: (len(v), v)) for k in COLS if k in group[0][0]}
        if any("scopus_id" in i for i, _ in group):
            row["scopus_id"] = ";".join(sids)
        if any("s2_id" in i for i, _ in group):
            row["s2_id"] = ";".join(s2ids)
        if dids:
            row["dblp_id"]=";".join(dids)
            row["dblp_doi_candidates"]=";".join(sorted({d for i,_ in group for d in i.get("dblp_doi_candidates","").split(";") if d}))
        if any("arxiv_id" in i for i,_ in group):
            row["arxiv_id"]=";".join(aids)
            for field in ("arxiv_related_doi","arxiv_version_url"):
                row[field]=";".join(sorted({i[field] for i,_ in group if i.get(field)}))
        row.update(record_id=rid, sources=";".join(sorted({p["source"] for _, p in group})), query_ids=";".join(sorted({p["query_id"] for _, p in group})), occurrences=len(group))
        records.append(row)
        if len(ids) > 1 or len(wids) > 1 or len(sids) > 1 or len(s2ids)>1 or len(aids)>1 or len(dids)>1 or any(len(i.get("dblp_doi_candidates","").split(";"))>1 for i,_ in group):
            conflicts.append({"record_id": rid, "dois": ";".join(ids), "openalex_ids": ";".join(wids), "scopus_ids": ";".join(sids), "s2_ids":";".join(s2ids), "arxiv_ids":";".join(aids), "dblp_ids":";".join(dids), "dblp_doi_candidates":row.get("dblp_doi_candidates",""), "status": "needs_adjudication"})
        for item, p in group:
            provenance.append({"record_id": rid, **p, "observed_doi": item["doi"], "observed_openalex_id": item["openalex_id"], "observed_scopus_id": item.get("scopus_id",""), "observed_s2_id":item.get("s2_id",""), "observed_arxiv_id":item.get("arxiv_id",""), "observed_arxiv_related_doi":item.get("arxiv_related_doi",""), "observed_arxiv_version_url":item.get("arxiv_version_url",""), "observed_dblp_id":item.get("dblp_id",""), "observed_dblp_doi_candidates":item.get("dblp_doi_candidates",""), "observed_title": item["title"]})
    titles = defaultdict(list)
    for r in records:
        title = normalized_title(r["title"])
        if title:
            titles[title].append(r)
    pairs = []
    for title, members in titles.items():
        if len(members) > 1:
            for a in range(len(members)):
                for b in range(a + 1, len(members)):
                    pairs.append({"record_a": members[a]["record_id"], "record_b": members[b]["record_id"], "title": title, "year_a": members[a]["year"], "year_b": members[b]["year"], "status": "needs_adjudication"})
    return sorted(records, key=lambda r: r["record_id"]), provenance, conflicts, pairs


def merge(args):
    occurrences = []
    for path in args.run:
        occurrences.extend(read_run(path, args.allow_partial))
    for spec in args.csv:
        source, path = spec.split("=", 1)
        if not source.strip():
            raise ValueError("Empty CSV source name.")
        occurrences.extend(read_csv(source, Path(path)))
    if not occurrences:
        raise ValueError("No records to consolidate.")
    records, provenance, conflicts, pairs = consolidate(occurrences)
    args.out.mkdir(parents=True, exist_ok=False)
    has_scopus = any("scopus_id" in i for i, _ in occurrences)
    has_s2 = any("s2_id" in i for i, _ in occurrences)
    has_arxiv = any("arxiv_id" in i for i, _ in occurrences)
    arxiv_cols=["arxiv_id","arxiv_related_doi","arxiv_version_url"] if has_arxiv else []
    dblp_cols=["dblp_id","dblp_doi_candidates"] if any("dblp_id" in i for i,_ in occurrences) else []
    cols = COLS + (["scopus_id"] if has_scopus else []) + (["s2_id"] if has_s2 else []) + arxiv_cols + dblp_cols
    csv_write(args.out / "records.csv", records, cols)
    csv_write(args.out / "provenance.csv", provenance, ["record_id", "source", "query_id", "raw_path", "row", "sha256", "date_scope", "observed_doi", "observed_openalex_id", "observed_title"] + (["observed_scopus_id"] if has_scopus else []) + (["observed_s2_id"] if has_s2 else []) + ["observed_"+c for c in arxiv_cols+dblp_cols])
    csv_write(args.out / "identifier_conflicts.csv", conflicts, ["record_id", "dois", "openalex_ids", "status"] + (["scopus_ids"] if has_scopus else []) + (["s2_ids"] if has_s2 else []) + (["arxiv_ids"] if has_arxiv else []) + (["dblp_ids","dblp_doi_candidates"] if dblp_cols else []))
    csv_write(args.out / "version_candidates.csv", pairs, ["record_a", "record_b", "title", "year_a", "year_b", "status"])
    review_fields = ["reviewer_id", "decision", "exclusion_reason", "notes"]
    for stage in ("title_abstract", "full_text"):
        csv_write(args.out / (stage + "_screening.csv"), records, cols + review_fields)
    evidence = ["claim_id", "record_id", "study_family_id", "rq", "artifact", "property", "mechanism", "oracle", "independence", "outcome", "value", "unit", "numerator", "denominator", "comparator", "source_locator", "source_url", "interpretation_limit", "reviewer_id", "validation_status"]
    csv_write(args.out / "evidence_claims.csv", [], evidence)
    source_sets = {r["record_id"]: set(r["sources"].split(";")) for r in records}
    sources = sorted({s for ss in source_sets.values() for s in ss})
    report = {"tool_version": VERSION, "created_utc": now(), "retrieval_occurrences": len(occurrences), "identifier_clusters": len(records), "identifier_conflicts": len(conflicts), "title_only_version_candidates": len(pairs), "missing_abstract": sum(not r["abstract"] for r in records), "records_by_source": {s: sum(s in v for v in source_sets.values()) for s in sources}, "exclusive_records_by_source": {s: sum(v == {s} for v in source_sets.values()) for s in sources}, "source_overlap": {s: {t: sum(s in v and t in v for v in source_sets.values()) for t in sources} for s in sources}, "allow_partial": args.allow_partial, "inclusion_count": None, "note": "Retrieval occurrences are not unique studies. Study-family decisions and reviewer screening remain pending."}
    report["date_scope_occurrences"] = dict(Counter(p.get("date_scope", "unknown") for p in provenance))
    write_json(args.out / "report.json", report)
    print(json.dumps(report, indent=2))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="command", required=True)
    for name, fn in (("search", search), ("chase", chase)):
        sp = sub.add_parser(name)
        sp.add_argument("--out", type=Path, required=True)
        sp.add_argument("--max-pages", type=int, help="Pilot cap per query; omit for full retrieval.")
        sp.add_argument("--delay", type=float, default=1.1, help="Seconds between requests (>=1).")
        if name == "search":
            sp.add_argument("--config", type=Path, required=True)
            sp.add_argument("--only", action="append", help="Run only the named query ID; may be repeated.")
        else:
            sp.add_argument("--seeds", type=Path, required=True)
            sp.add_argument("--from-date", required=True)
            sp.add_argument("--to-date", required=True)
            sp.add_argument("--direction", choices=["forward", "backward", "both"], default="both")
        sp.set_defaults(fn=fn)
    sp = sub.add_parser("merge")
    sp.add_argument("--run", type=Path, action="append", default=[])
    sp.add_argument("--csv", action="append", default=[], help="Source=path, one per export")
    sp.add_argument("--allow-partial", action="store_true")
    sp.add_argument("--out", type=Path, required=True)
    sp.set_defaults(fn=merge)
    sp = sub.add_parser("scopus-search")
    sp.add_argument("--config", type=Path, required=True)
    sp.add_argument("--out", type=Path, required=True)
    sp.add_argument("--only", action="append")
    sp.add_argument("--max-pages", type=int, help="Pilot cap per query.")
    sp.add_argument("--view", choices=["STANDARD","COMPLETE"], default="STANDARD")
    sp.add_argument("--count", type=int, default=25)
    sp.add_argument("--delay", type=float, default=1.1)
    sp.set_defaults(fn=scopus_search)
    sp = sub.add_parser("s2-search")
    sp.add_argument("--config", type=Path, required=True)
    sp.add_argument("--out", type=Path, required=True)
    sp.add_argument("--only", action="append")
    sp.add_argument("--max-pages", type=int, help="Pilot cap per query (up to 1000 records per page).")
    sp.add_argument("--anonymous", action="store_true", help="Use public API without a key; shared rate limits apply.")
    sp.add_argument("--delay", type=float, default=1.1)
    sp.set_defaults(fn=s2_search)
    sp = sub.add_parser("arxiv-search")
    sp.add_argument("--config", type=Path, required=True)
    sp.add_argument("--out", type=Path, required=True)
    sp.add_argument("--only", action="append")
    sp.add_argument("--max-pages", type=int, help="Pilot cap per query.")
    sp.add_argument("--count", type=int, default=100)
    sp.add_argument("--delay", type=float, default=3.1)
    sp.set_defaults(fn=arxiv_search)
    sp = sub.add_parser("crossref-search")
    sp.add_argument("--config", type=Path, required=True)
    sp.add_argument("--out", type=Path, required=True)
    sp.add_argument("--only", action="append")
    sp.add_argument("--top", type=int, default=200, help="Bounded ranked results per query (1..1000).")
    sp.add_argument("--delay", type=float, default=2.0)
    sp.set_defaults(fn=crossref_search)
    sp = sub.add_parser("dblp-search")
    sp.add_argument("--config", type=Path, required=True)
    sp.add_argument("--snapshot", type=Path, required=True)
    sp.add_argument("--dtd", type=Path, required=True)
    sp.add_argument("--snapshot-citation", required=True)
    sp.add_argument("--out", type=Path, required=True)
    sp.add_argument("--only", action="append")
    sp.set_defaults(fn=dblp_search)
    args = p.parse_args(argv)
    if getattr(args, "max_pages", None) is not None and args.max_pages < 1:
        p.error("--max-pages must be positive")
    if not math.isfinite(getattr(args, "delay", 1.1)) or getattr(args, "delay", 1.1) < 1:
        p.error("--delay must be at least 1 second")
    try:
        args.fn(args)
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        p.exit(1, f"SE-ReviewKit: {exc}\n")


if __name__ == "__main__":
    main()
