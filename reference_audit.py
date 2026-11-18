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


def audit(rows):
    """Report collisions and missing fields; exact titles are never automatic merges."""
    indices={key:defaultdict(list) for key in ('citekey','doi','title','component')}
    errors=[]
    for i,r in enumerate(rows,1):
        ref=str(r.get('citekey') or f'row:{i}')
        for key in ('citekey','component','title','doi','year','authors','archive','page','row','page_sha256'):
            if not r.get(key):errors.append({'reference':ref,'field':key,'issue':'missing'})
        doi=doi_key(str(r.get('doi') or ''))
        if doi and not re.fullmatch(r'10\.\d{4,9}/\S+',doi):errors.append({'reference':ref,'field':'doi','issue':'invalid_format'})
        digest=str(r.get('page_sha256') or '')
        if digest and not re.fullmatch(r'[a-fA-F0-9]{64}',digest):errors.append({'reference':ref,'field':'page_sha256','issue':'invalid_format'})
        if not isinstance(r.get('row'),int) or isinstance(r.get('row'),bool) or r['row']<1:errors.append({'reference':ref,'field':'row','issue':'invalid_locator'})
        for key,value in [('citekey',str(r.get('citekey') or '')),('component',str(r.get('component') or '')),('doi',doi),('title',title_key(str(r.get('title') or '')))]:
            if value:indices[key][value].append(ref)
    collisions={k:{v:refs for v,refs in idx.items() if len(refs)>1} for k,idx in indices.items()}
    return {'records':len(rows),'errors':errors,'duplicate_citekeys':collisions['citekey'],'duplicate_dois':collisions['doi'],'duplicate_titles':collisions['title'],'duplicate_components':collisions['component'],'automatic_merges':0,'final_inclusions':None,'note':'Structural validation only; does not verify DOI resolution, source-page bytes, study family, eligibility or author identity.'}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('ledger',type=Path);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    data=a.ledger.read_bytes();result=audit([json.loads(line) for line in data.decode('utf-8').splitlines() if line.strip()]);result['ledger_sha256']=hashlib.sha256(data).hexdigest()
    with a.out.open('x',encoding='utf-8') as f:json.dump(result,f,indent=2);f.write('\n')
