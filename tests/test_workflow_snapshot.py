from decimal import Decimal
import unittest
from enterprise_ai.workflow_snapshot import normalize_snapshot, SNAPSHOT_QUERIES


class SnapshotTests(unittest.TestCase):
    def sample(self):
        return {'tenant':1,'employee':1,'year':2026,'customers':[1],
                'catalog':[[1,'100.00']], 'leave':[[1,'15.00']],
                'stock':[[1,20,5,4,30]]}

    def test_snapshot_values_are_strict_and_hand_computable(self):
        result=normalize_snapshot(self.sample())
        self.assertEqual(result['resources'][0],(1,'catalog',1,1,{'unit_price':'100.00'}))
        self.assertIn((1,'leave',1,1,{'remaining_days':15}),result['resources'])
        self.assertIn((1,'stock',1,1,{'on_hand':20,'reserved':5,'on_order':4,'reorder_point':30}),result['resources'])

    def test_fractional_days_nonfinite_money_bool_and_duplicates_rejected(self):
        for field,value in [('leave',[[1,'1.50']]),('catalog',[[1,'NaN']]),
                            ('stock',[[1,True,0,0,30]]),('customers',[1,1])]:
            obj=self.sample(); obj[field]=value
            with self.subTest(field=field), self.assertRaises(ValueError): normalize_snapshot(obj)

    def test_missing_or_extra_fields_and_foreign_employee_denied(self):
        for change in ({'extra':1},{'leave':[[2,'15']]},{'tenant':True}):
            obj=self.sample(); obj.update(change)
            with self.assertRaises(ValueError): normalize_snapshot(obj)

    def test_source_queries_are_read_only_and_include_in_transit_orders(self):
        self.assertTrue(all(q.lstrip().upper().startswith('SELECT') for q in SNAPSHOT_QUERIES.values()))
        self.assertIn("status='ordered'",SNAPSHOT_QUERIES['stock'])
        self.assertIn('auth.current_employee()',SNAPSHOT_QUERIES['leave'])


if __name__=='__main__': unittest.main()
