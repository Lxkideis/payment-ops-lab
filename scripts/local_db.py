"""Manage only this project's isolated local PostgreSQL cluster; no system service changes."""
from pathlib import Path
import argparse,json,os,secrets,socket,subprocess,sys
from urllib.parse import quote
import psycopg
from psycopg import sql
ROOT=Path(__file__).resolve().parents[1]
RUNTIME=ROOT/'_runtime'
PG=Path(os.getenv('PAYOPS_PG_BIN',r'C:\Program Files\PostgreSQL\18\bin'))

def command(name,*args,check=True):
    # On Windows a spawned server can retain a pipe handle; use a file, never PIPE.
    logfile=RUNTIME/'bootstrap.log'
    with logfile.open('ab') as log:
        result=subprocess.run([str(PG/(name+'.exe' if os.name=='nt' else name)),*map(str,args)],
          stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if check and result.returncode:raise RuntimeError(f'{name} failed: {logfile.read_text(errors="replace")[-5000:]}')
    return result

def start():
    RUNTIME.mkdir(exist_ok=True)
    statefile=RUNTIME/'state.json';data=RUNTIME/'pgdata'
    if statefile.exists():
        state=json.loads(statefile.read_text())
    else:
        with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
        owner_pass=secrets.token_urlsafe(24);app_pass=secrets.token_urlsafe(24)
        prefix=f'postgresql://portfolio_owner:{quote(owner_pass)}@127.0.0.1:{port}/'
        appprefix=f'postgresql://portfolio_app:{quote(app_pass)}@127.0.0.1:{port}/'
        state={'port':port,'owner_password':owner_pass,'app_password':app_pass,
          'owner_url':prefix+'payops_demo','app_url':appprefix+'payops_demo',
          'test_owner_url':prefix+'payops_test','test_url':appprefix+'payops_test',
          'admin_url':prefix+'postgres','pg_bin':str(PG)}
        statefile.write_text(json.dumps(state,indent=2),encoding='utf-8')
    if not (data/'PG_VERSION').exists():
        pw=RUNTIME/'owner.pw';pw.write_text(state['owner_password'],encoding='utf-8')
        try:command('initdb','-D',data,'-U','portfolio_owner','--pwfile',pw,'--auth-host=scram-sha-256','--auth-local=scram-sha-256','--encoding=UTF8','--locale=C','--no-instructions')
        finally:pw.unlink(missing_ok=True)
    if command('pg_ctl','-D',data,'status',check=False).returncode:
        command('pg_ctl','-D',data,'-l',RUNTIME/'postgres.log','-o',
          f"-h 127.0.0.1 -p {state['port']} -c max_connections=30 -c shared_buffers=64MB",'-w','start')
    with psycopg.connect(state['admin_url'],autocommit=True) as c:
        if not c.execute("SELECT 1 FROM pg_roles WHERE rolname='portfolio_app'").fetchone():
            c.execute(sql.SQL('CREATE ROLE portfolio_app LOGIN PASSWORD {}').format(sql.Literal(state['app_password'])))
        for name in ['payops_demo','payops_test']:
            if not c.execute('SELECT 1 FROM pg_database WHERE datname=%s',(name,)).fetchone():
                c.execute(sql.SQL('CREATE DATABASE {} OWNER portfolio_owner').format(sql.Identifier(name)))
    schema=(ROOT/'sql/001_schema.sql').read_text()
    for key in ['owner_url','test_owner_url']:
        with psycopg.connect(state[key]) as c:c.execute(schema)
    print(json.dumps({'running':True,'host':'127.0.0.1','port':state['port'],'databases':['payops_demo','payops_test']},indent=2))

def stop():
    data=(RUNTIME/'pgdata').resolve()
    if not data.is_relative_to(RUNTIME.resolve()):raise RuntimeError('Invalid cluster path')
    command('pg_ctl','-D',data,'-m','fast','-w','stop')
    print('Stopped only the portfolio cluster; data retained.')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['start','stop']);args=parser.parse_args()
    start() if args.action=='start' else stop()
