"""Audit reference metadata identities and provenance without merging records."""
import argparse
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path


def title_key(title):
    return ' '.join(re.findall(r'[^\W_]+', unicodedata.normalize('NFKC',title).casefold()))


def doi_key(doi):
    return re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)','',doi.strip(),flags=re.I).lower()


def audit(rows, archive_root=None):
    """Report collisions and missing fields; exact titles are never automatic merges."""
    indices={key:defaultdict(list) for key in ('citekey','doi','title','component')}
    errors=[]
    checked_pages={}
    for i,r in enumerate(rows,1):
        if not isinstance(r, dict):
            raise ValueError(f'Row {i}: expected JSON object')
        ref=str(r.get('citekey') or f'row:{i}')
        for key in ('citekey','component','title','doi','year','authors','archive','page','row','page_sha256'):
            if not r.get(key) and not (key=='doi' and r.get('doi_status')=='not_assigned'):
                errors.append({'reference':ref,'field':key,'issue':'missing'})
        doi=doi_key(str(r.get('doi') or ''))
        if doi and not re.fullmatch(r'10\.\d{4,9}/\S+',doi):errors.append({'reference':ref,'field':'doi','issue':'invalid_format'})
        digest=str(r.get('page_sha256') or '')
        if digest and not re.fullmatch(r'[a-fA-F0-9]{64}',digest):errors.append({'reference':ref,'field':'page_sha256','issue':'invalid_format'})
        if not isinstance(r.get('row'),int) or isinstance(r.get('row'),bool) or r['row']<1:errors.append({'reference':ref,'field':'row','issue':'invalid_locator'})
        if archive_root is not None:
            root=Path(archive_root).resolve()
            try:
                archive,page=r.get('archive'),r.get('page')
                if not isinstance(archive,str) or not isinstance(page,str) or not archive or not page:
                    raise ValueError('missing archive path')
                relative=Path(archive)/page
                if Path(archive).is_absolute() or Path(page).is_absolute() or '..' in relative.parts:
                    raise ValueError('invalid archive path')
                path=(root/relative).resolve()
                if not path.is_relative_to(root):
                    raise ValueError('invalid archive path')
                if path not in checked_pages:
                    with path.open('rb') as f:
                        h=hashlib.sha256()
                        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
                    checked_pages[path]=h.hexdigest()
                if checked_pages[path]!=digest.lower():
                    errors.append({'reference':ref,'field':'page_sha256','issue':'digest_mismatch'})
            except (OSError,ValueError):
                errors.append({'reference':ref,'field':'page','issue':'archive_page_unavailable_or_unsafe'})
        for key,value in [('citekey',str(r.get('citekey') or '')),('component',str(r.get('component') or '')),('doi',doi),('title',title_key(str(r.get('title') or '')))]:
            if value:indices[key][value].append(ref)
    collisions={k:{v:refs for v,refs in idx.items() if len(refs)>1} for k,idx in indices.items()}
    return {'records':len(rows),'errors':errors,'duplicate_citekeys':collisions['citekey'],'duplicate_dois':collisions['doi'],'duplicate_titles':collisions['title'],'duplicate_components':collisions['component'],'automatic_merges':0,'final_inclusions':None,'archive_pages_hashed':len(checked_pages),'note':'Structural validation and optional local archive digest checks only; does not establish DOI resolution, source identity, study family, eligibility or author identity.'}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('ledger',type=Path);p.add_argument('--out',type=Path,required=True);p.add_argument('--archive-root',type=Path,help='Optional private extracted archive root');a=p.parse_args()
    try:
        data=a.ledger.read_bytes();result=audit([json.loads(line) for line in data.decode('utf-8').splitlines() if line.strip()],a.archive_root);result['ledger_sha256']=hashlib.sha256(data).hexdigest()
        with a.out.open('x',encoding='utf-8') as f:json.dump(result,f,indent=2);f.write('\n')
    except (OSError,ValueError,TypeError) as exc:
        p.exit(1,f'SE-ReviewKit: {exc}\n')
