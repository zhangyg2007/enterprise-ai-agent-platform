"""Fixed read-only business queries. Authorization comes from DB login + RLS."""

CUSTOMER = """
WITH totals AS (
    SELECT tenant_id, order_id, SUM(quantity * unit_price) AS amount
    FROM crm.sales_order_items GROUP BY tenant_id, order_id
)
SELECT c.id, c.name, COUNT(o.id) AS order_count,
       COALESCE(SUM(t.amount), 0)::numeric(16,2) AS order_amount
FROM crm.customers c
LEFT JOIN crm.sales_orders o ON (o.tenant_id, o.customer_id) = (c.tenant_id, c.id)
LEFT JOIN totals t ON (t.tenant_id, t.order_id) = (o.tenant_id, o.id)
WHERE c.id = %s GROUP BY c.id, c.name
"""

LEAVE = """
SELECT employee_id, year, entitled_days, used_days,
       entitled_days - used_days AS remaining_days
FROM hcm.leave_balances
WHERE employee_id = auth.current_employee() AND year = %s
"""

INVENTORY = """
SELECT i.product_id, p.sku, i.warehouse_id, p.unit,
       i.on_hand, i.reserved, i.on_hand - i.reserved AS available,
       i.reorder_point,
       GREATEST(i.reorder_point - (i.on_hand - i.reserved), 0) AS snapshot_gap
FROM scm.inventory i
JOIN scm.products p ON (p.tenant_id, p.id) = (i.tenant_id, i.product_id)
WHERE i.product_id = %s ORDER BY i.warehouse_id
"""
