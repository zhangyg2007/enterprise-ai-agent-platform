"""M02 read-only exports and strict frozen inputs for the isolated M11 ledger."""
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path

from .workflow_store import _integer, _json

SNAPSHOT_QUERIES = {
    'customers': 'SELECT id FROM crm.customers ORDER BY id',
    'catalog': 'SELECT id,unit_price FROM scm.products ORDER BY id',
    'leave': 'SELECT employee_id,entitled_days-used_days FROM hcm.leave_balances '
             'WHERE employee_id=auth.current_employee() AND year=%s ORDER BY employee_id',
    'stock': "SELECT i.product_id,sum(i.on_hand),sum(i.reserved),"
             "COALESCE((SELECT sum(pi.quantity) FROM scm.purchase_order_items pi "
             "JOIN scm.purchase_orders po ON (po.tenant_id,po.id)=(pi.tenant_id,pi.order_id) "
             "WHERE pi.tenant_id=auth.current_tenant() AND pi.product_id=i.product_id "
             "AND po.status='ordered'),0),sum(i.reorder_point) FROM scm.inventory i "
             "GROUP BY i.product_id ORDER BY i.product_id",
}


def _nonnegative(value):
    if type(value) is not int or not 0 <= value < 2**31:
        raise ValueError('expected nonnegative integer below 2**31')
    return value


def _decimal(value):
    if not isinstance(value,str) or len(value)>32:
        raise ValueError('expected finite decimal string')
    try: number=Decimal(value)
    except InvalidOperation as exc: raise ValueError('invalid decimal') from exc
    if not number.is_finite() or number<0 or number>=Decimal(2**31):
        raise ValueError('decimal outside snapshot range')
    return number


def normalize_snapshot(obj):
    if not isinstance(obj,dict) or set(obj)!={'tenant','employee','year','customers','catalog','leave','stock'}:
        raise ValueError('invalid snapshot fields')
    tenant=_integer(obj['tenant']); employee=_integer(obj['employee'])
    if type(obj['year']) is not int or not 2000<=obj['year']<=2100: raise ValueError('invalid snapshot year')
    resources=[]; customers=[]
    for name in ('customers','catalog','leave','stock'):
        rows=obj[name]
        if not isinstance(rows,list) or not rows or len(rows)>10000: raise ValueError('empty/large snapshot')
        seen=set()
        for row in rows:
            if name=='customers':
                rid=_integer(row); customers.append((tenant,rid))
            else:
                size={'catalog':2,'leave':2,'stock':5}[name]
                if not isinstance(row,list) or len(row)!=size: raise ValueError('invalid snapshot row')
                rid=_integer(row[0])
                if name=='catalog':
                    price=_decimal(row[1])
                    if price<=0 or price!=price.quantize(Decimal('.01')): raise ValueError('invalid price')
                    value={'unit_price':format(price,'.2f')}
                elif name=='leave':
                    days=_decimal(row[1])
                    if rid!=employee or days!=days.to_integral_value(): raise ValueError('fractional/foreign leave')
                    value={'remaining_days':int(days)}
                else:
                    values=[_nonnegative(v) for v in row[1:]]
                    if values[1]>values[0]: raise ValueError('reserved exceeds stock')
                    value=dict(zip(('on_hand','reserved','on_order','reorder_point'),values))
                resources.append((tenant,name,rid,1,value))
            if rid in seen: raise ValueError('duplicate snapshot resource')
            seen.add(rid)
    return {'tenant':tenant,'customers':customers,'resources':resources,
            'digest':hashlib.sha256(_json(obj).encode()).hexdigest()}


def export_snapshot(conn, year):
    if type(year) is not int or not 2000<=year<=2100: raise ValueError('invalid year')
    with conn.transaction():
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        tenant,employee=conn.execute('SELECT auth.current_tenant(),auth.current_employee()').fetchone()
        obj={'tenant':tenant,'employee':employee,'year':year}
        for name,query in SNAPSHOT_QUERIES.items():
            rows=conn.execute(query,(year,) if name=='leave' else ()).fetchall()
            obj[name]=[row[0] if name=='customers' else
                       [format(v,'f') if isinstance(v,Decimal) else v for v in row] for row in rows]
        normalize_snapshot(obj)
        return obj


def write_snapshot(path, obj):
    normalize_snapshot(obj)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as handle:
        handle.write(json.dumps(obj,sort_keys=True,allow_nan=False)+'\n')
