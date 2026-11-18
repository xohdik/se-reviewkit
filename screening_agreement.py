"""Compute independent screening agreement with explicit incomplete pairs."""
import argparse,csv,json,hashlib
from pathlib import Path

LABELS=('include','exclude','uncertain')


def read_decisions(path,id_column):
    with Path(path).open(encoding='utf-8-sig',newline='') as f:
        return list(csv.DictReader(f))


def evaluate(a,b,id_column='component'):
    def index(rows):
        out={}
        for r in rows:
            i=r[id_column]; d=r.get('decision','').strip().lower()
            if not i or i in out or d not in ('',*LABELS):raise ValueError('invalid or repeated record ID/decision')
            if d and (not r.get('reviewer_id','').strip() or not r.get('reason_code','').strip()):raise ValueError('completed decisions need reviewer and reason')
            out[i]=r|{'decision':d}
        return out
    x,y=index(a),index(b)
    if set(x)!=set(y):raise ValueError('review packets do not share the same record universe')
    reviewers_a={r['reviewer_id'].strip() for r in x.values() if r['decision']}; reviewers_b={r['reviewer_id'].strip() for r in y.values() if r['decision']}
    if len(reviewers_a)>1 or len(reviewers_b)>1 or reviewers_a&reviewers_b:raise ValueError('packets must have distinct independent reviewer IDs')
    matrix={i:{j:0 for j in LABELS} for i in LABELS};pending=[];disagreements=[];advance=[]
    for k in sorted(x):
        l,r=x[k]['decision'],y[k]['decision']
        if not l or not r:pending.append(k);continue
        matrix[l][r]+=1
        if l!=r:disagreements.append(k)
        if l!='exclude' or r!='exclude':advance.append(k)
    n=sum(sum(v.values()) for v in matrix.values())
    agree=sum(matrix[k][k] for k in LABELS)
    row={k:sum(matrix[k].values()) for k in LABELS};col={k:sum(matrix[i][k] for i in LABELS) for k in LABELS}
    observed=agree/n if n else None; expected=sum(row[k]*col[k] for k in LABELS)/(n*n) if n else None
    kappa=(observed-expected)/(1-expected) if n and expected!=1 else None
    return dict(records=len(x),complete_pairs=n,pending_pairs=len(pending),matrix=matrix,observed_agreement=observed,expected_agreement=expected,cohens_kappa=kappa,disagreement_ids=disagreements,pending_ids=pending,nonunanimous_exclusion_ids=advance,final_inclusions=None,note='Paired decisions only; kappa undefined when expected agreement equals one or there are no pairs. No automatic exclusion or adjudication.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('reviewer_a',type=Path);p.add_argument('reviewer_b',type=Path);p.add_argument('--id-column',default='component');p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    result=evaluate(read_decisions(a.reviewer_a,a.id_column),read_decisions(a.reviewer_b,a.id_column),a.id_column)
    result['input_sha256']={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (a.reviewer_a,a.reviewer_b)}
    with a.out.open('x',encoding='utf-8') as f:json.dump(result,f,indent=2);f.write('\n')
