USE forge_sight;

SELECT 'dim_product' AS table_name, COUNT(*) AS row_count FROM dim_product
UNION ALL SELECT 'dim_process', COUNT(*) FROM dim_process
UNION ALL SELECT 'dim_production_line', COUNT(*) FROM dim_production_line
UNION ALL SELECT 'dim_equipment', COUNT(*) FROM dim_equipment
UNION ALL SELECT 'mes_work_order', COUNT(*) FROM mes_work_order
UNION ALL SELECT 'mes_process_output', COUNT(*) FROM mes_process_output
UNION ALL SELECT 'qms_inspection', COUNT(*) FROM qms_inspection
UNION ALL SELECT 'qms_defect_detail', COUNT(*) FROM qms_defect_detail
UNION ALL SELECT 'eqp_downtime_record', COUNT(*) FROM eqp_downtime_record
UNION ALL SELECT 'inv_inventory_snapshot', COUNT(*) FROM inv_inventory_snapshot;

SELECT 'orphan_equipment_line' AS check_name, COUNT(*) AS issue_count
FROM dim_equipment e LEFT JOIN dim_production_line l ON l.line_id = e.line_id
WHERE l.line_id IS NULL
UNION ALL
SELECT 'orphan_output_work_order', COUNT(*)
FROM mes_process_output o LEFT JOIN mes_work_order w ON w.work_order_id = o.work_order_id
WHERE w.work_order_id IS NULL
UNION ALL
SELECT 'orphan_inspection_work_order', COUNT(*)
FROM qms_inspection i LEFT JOIN mes_work_order w ON w.work_order_id = i.work_order_id
WHERE w.work_order_id IS NULL
UNION ALL
SELECT 'orphan_defect_inspection', COUNT(*)
FROM qms_defect_detail d LEFT JOIN qms_inspection i ON i.inspection_id = d.inspection_id
WHERE i.inspection_id IS NULL
UNION ALL
SELECT 'orphan_downtime_equipment', COUNT(*)
FROM eqp_downtime_record d LEFT JOIN dim_equipment e ON e.equipment_id = d.equipment_id
WHERE e.equipment_id IS NULL
UNION ALL
SELECT 'orphan_inventory_product', COUNT(*)
FROM inv_inventory_snapshot s LEFT JOIN dim_product p ON p.product_id = s.product_id
WHERE p.product_id IS NULL;

SELECT
  MIN(stat_date) AS min_stat_date,
  MAX(stat_date) AS max_stat_date,
  ROUND(SUM(good_qty) / NULLIF(SUM(input_qty), 0) * 100, 2) AS overall_yield_pct
FROM mes_process_output;
