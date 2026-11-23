"""Generate deterministic, entirely fictional charge/refund and partner files with an oracle."""
from pathlib import Path
from datetime import date,timedelta
from collections import Counter
import argparse,csv,hashlib,json,random
ROOT=Path(__file__).resolve().parents[1]

def generate(folder=None,count=10000,seed=27,as_of=date(2026,10,21)):
    folder=Path(folder or ROOT/'data');folder.mkdir(exist_ok=True,parents=True)
    rng=random.Random(seed);ledger=[];partner=[];truth=Counter()
    def settled(row,suffix='a',installment=1,gross=None,fee=None,currency=None):
        return {'row_id':row['event_id']+'_'+suffix,'event_id':row['event_id'],'installment':installment,
          'currency':currency or row['currency'],'gross_minor':row['gross_minor'] if gross is None else gross,
          'fee_minor':row['fee_minor'] if fee is None else fee,
          'payout_id':'po_'+row['currency']+'_'+row['expected_on'].replace('-',''),
          'settled_on':row['expected_on']}
    for i in range(count):
        cur=['SGD','USD','JPY'][i%3];gross=rng.randint(1500,400000);fee=gross*29//1000+(30 if cur!='JPY' else 0)
        expected=as_of-timedelta(days=rng.randint(1,44));occurred=expected-timedelta(days=2)
        rem=i%200
        if rem==5:expected=as_of+timedelta(days=3);occurred=as_of
        row={'event_id':f'evt_{i:06d}','payment_id':f'pay_{i:06d}','kind':'charge','currency':cur,
          'gross_minor':gross,'fee_minor':fee,'occurred_at':str(occurred),'expected_on':str(expected)}
        ledger.append(row)
        if rem==0:truth['missing']+=1
        elif rem==5:truth['pending']+=1
        elif rem in [1,6]:
            partner.append(settled(row,fee=fee+7));truth['amount']+=1
        elif rem==2:
            partner.append(settled(row,currency='USD' if cur!='USD' else 'SGD'));truth['currency']+=1
        elif rem==3:
            partner.extend([settled(row),settled(row,'duplicate')]);truth['duplicate']+=1
        elif rem==4:
            g=gross//2;f=fee//2
            partner.extend([settled(row,'part1',1,g,f),settled(row,'part2',2,gross-g,fee-f)]);truth['matched']+=1
        else:partner.append(settled(row));truth['matched']+=1
        if i%50==17:
            refund={**row,'event_id':f'ref_{i:06d}','kind':'refund','gross_minor':-gross//3,'fee_minor':0}
            ledger.append(refund);partner.append(settled(refund));truth['matched']+=1
    for i in range(12):
        partner.append({'row_id':f'unknown_{i}','event_id':f'orphan_{i}','installment':1,'currency':'SGD',
          'gross_minor':10000+i,'fee_minor':100,'payout_id':'po_unknown','settled_on':str(as_of-timedelta(days=4))})
        truth['orphan']+=1
    for filename,rows in [('ledger.csv',ledger),('partner.csv',partner)]:
        with (folder/filename).open('w',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    manifest={'dataset':'entirely synthetic, not actual Stripe or bank records','seed':seed,'as_of':str(as_of),
      'charge_count':count,'ledger_rows':len(ledger),'partner_rows':len(partner),'expected_reconciliation_counts':dict(truth),
      'sha256':{name:hashlib.sha256((folder/name).read_bytes()).hexdigest() for name in ['ledger.csv','partner.csv']}}
    (folder/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return manifest

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--count',type=int,default=10000);p.add_argument('--output');a=p.parse_args()
    print(json.dumps(generate(a.output,a.count),indent=2))
