import csv,hashlib,io,re,uuid
from datetime import date
from pydantic import BaseModel,ConfigDict,Field,model_validator
from psycopg import errors
from .db import connection,pipeline_lock

class ImportRejected(ValueError):pass

class LedgerRow(BaseModel):
    model_config=ConfigDict(extra='forbid')
    event_id:str=Field(pattern=r'^[A-Za-z0-9_:.-]{1,100}$')
    payment_id:str=Field(pattern=r'^[A-Za-z0-9_:.-]{1,100}$')
    kind:str=Field(pattern=r'^(charge|refund)$')
    currency:str=Field(pattern=r'^(SGD|USD|JPY)$')
    gross_minor:int=Field(ge=-9000000000000,le=9000000000000)
    fee_minor:int=Field(ge=0,le=9000000000000)
    occurred_at:date
    expected_on:date
    @model_validator(mode='after')
    def business_rules(self):
        if self.expected_on<self.occurred_at:raise ValueError('Expected settlement precedes transaction')
        if self.kind=='charge' and not (self.gross_minor>0 and self.fee_minor<self.gross_minor):raise ValueError('Invalid charge/fee')
        if self.kind=='refund' and not (self.gross_minor<0 and self.fee_minor==0):raise ValueError('Refund must be negative and fee-free in this lab')
        return self

class PartnerRow(BaseModel):
    model_config=ConfigDict(extra='forbid')
    row_id:str=Field(pattern=r'^[A-Za-z0-9_:.-]{1,100}$')
    event_id:str=Field(pattern=r'^[A-Za-z0-9_:.-]{1,100}$')
    installment:int=Field(ge=1,le=1000)
    currency:str=Field(pattern=r'^(SGD|USD|JPY)$')
    gross_minor:int=Field(ge=-9000000000000,le=9000000000000)
    fee_minor:int=Field(ge=0,le=9000000000000)
    payout_id:str=Field(pattern=r'^[A-Za-z0-9_:.-]{1,100}$')
    settled_on:date

def parse_csv(source,body):
    if source not in ['ledger','partner']:raise ImportRejected('Unknown source')
    if len(body)>20_000_000:raise ImportRejected('CSV limit is 20 MB')
    model=LedgerRow if source=='ledger' else PartnerRow
    try:
        reader=csv.DictReader(io.StringIO(body.decode('utf-8-sig')))
        fields=list(model.model_fields)
        if reader.fieldnames!=fields:raise ImportRejected('Expected ordered columns: '+','.join(fields))
        rows=[]
        for line,row in enumerate(reader,start=2):
            if len(rows)>=100000:raise ImportRejected('Limit is 100,000 rows per import')
            # Financial minor units cannot be rounded from decimal strings.
            for col in ['gross_minor','fee_minor']:
                if not re.fullmatch(r'-?\d+',str(row.get(col,''))):raise ImportRejected(f'Line {line}: {col} must be an integer')
            try:rows.append(model.model_validate(row).model_dump())
            except ValueError as e:raise ImportRejected(f'Line {line}: invalid fields or business rule') from e
        if not rows:raise ImportRejected('CSV has no data rows')
        return rows,fields
    except UnicodeError as e:raise ImportRejected('CSV must be UTF-8') from e

def ingest(source,body):
    rows,fields=parse_csv(source,body);digest=hashlib.sha256(body).hexdigest();batch_id=uuid.uuid4()
    table='ledger_entry' if source=='ledger' else 'settlement_entry'
    try:
        with connection() as conn:
            pipeline_lock(conn)
            old=conn.execute('SELECT id,row_count FROM ingest_batch WHERE source=%s AND sha256=%s',(source,digest)).fetchone()
            if old:return {'batch_id':str(old['id']),'rows':old['row_count'],'replayed':True,'sha256':digest}
            conn.execute('INSERT INTO ingest_batch(id,source,sha256,row_count) VALUES(%s,%s,%s,%s)',(batch_id,source,digest,len(rows)))
            with conn.cursor().copy(f"COPY {table} ({','.join(fields)},batch_id) FROM STDIN") as copy:
                for row in rows:copy.write_row([row[f] for f in fields]+[batch_id])
            return {'batch_id':str(batch_id),'rows':len(rows),'replayed':False,'sha256':digest}
    except (errors.UniqueViolation,errors.CheckViolation,errors.ForeignKeyViolation) as e:
        raise ImportRejected('Import rolled back: duplicate source key or violated integrity rule') from e
