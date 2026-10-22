"""Trusted read-only adapters: synthetic business fixtures, PostgreSQL and M09."""
from .agent import decision
from .db import connect, normalize
from .queries import CUSTOMER, LEAVE, INVENTORY
from .rag_search import search, evidence_answer


class FixtureTools:
    def __init__(self,index=None,state=None,encoder=None):
        self.index=index; self.state=state; self.encoder=encoder

    def knowledge(self,principal,args,kind):
        if self.index is None: raise ValueError('knowledge index not configured')
        if self.index['tenant_id']!=principal.tenant_id: raise PermissionError('index tenant mismatch')
        result=search(self.index,self.state,args['query'],roles=list(principal.roles),as_of=principal.as_of,
                      k=3,encoder=self.encoder)
        return {'kind':kind,'tool':'knowledge','index_digest':self.index['digest'],'answer':evidence_answer(result)}

    def call(self,principal,plan):
        plan=decision(plan,principal); tool=plan['tool']; args=plan['arguments']
        if tool=='knowledge': return self.knowledge(principal,args,'synthetic-fixture')
        if tool=='customer': rows=[{'customer_id':args['customer_id'],'order_amount':'1200.00'}] if args['customer_id']<=2 else []
        elif tool=='inventory': rows=[{'product_id':args['product_id'],'available':18}] if args['product_id']<=2 else []
        elif tool=='leave': rows=[{'employee_id':principal.employee_id,'year':int(principal.as_of[:4]),'remaining_days':'15.00'}]
        else: raise ValueError('not an executable tool')
        return {'kind':'synthetic-fixture','tool':tool,'tenant_id':principal.tenant_id,'rows':rows}


class LiveTools(FixtureTools):
    def __init__(self,settings,index=None,state=None,encoder=None):
        super().__init__(index,state,encoder); self.settings=settings

    def call(self,principal,plan):
        plan=decision(plan,principal); tool=plan['tool']; args=plan['arguments']
        if tool=='knowledge':
            if self.encoder is None or self.encoder.identity.get('kind')!='local-transformers':
                raise ValueError('live mode requires real embedding')
            return self.knowledge(principal,args,'live-python-rag')
        queries={'customer':(CUSTOMER,(args.get('customer_id'),)),
                 'leave':(LEAVE,(int(principal.as_of[:4]),)),
                 'inventory':(INVENTORY,(args.get('product_id'),))}
        if tool not in queries: raise ValueError('not an executable tool')
        try:
            with connect(self.settings,principal.reader) as conn,conn.transaction():
                conn.execute('SET TRANSACTION READ ONLY')
                conn.execute('SET LOCAL statement_timeout=5000')
                flags=conn.execute('SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()
                identity=conn.execute('SELECT auth.current_tenant(),auth.current_employee()').fetchone()
                if flags!=(False,False) or identity!=(principal.tenant_id,principal.employee_id):
                    raise PermissionError('reader identity mismatch')
                cursor=conn.execute(*queries[tool]); rows=cursor.fetchmany(51)
                if len(rows)>50: raise ValueError('tool row limit exceeded')
                names=[c.name for c in cursor.description]
                result=[dict(zip(names,[normalize(v) for v in row])) for row in rows]
        except Exception as exc:
            code=getattr(exc,'sqlstate',None)
            if code=='57014' or (isinstance(code,str) and code.startswith('08')):
                raise TimeoutError('database unavailable') from None
            if code is None and type(exc).__name__=='OperationalError' and type(exc).__module__.startswith('psycopg'):
                raise ConnectionError('database unavailable') from None
            raise
        return {'kind':'live-postgres-reader','tool':tool,'tenant_id':principal.tenant_id,'rows':result}
