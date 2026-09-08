-- ForgeSight synthetic annual manufacturing dataset (MySQL 8.0+)
-- Fully self-contained schema for the one-year synthetic dataset.
-- The 10 standard business tables mirror database/mysql/001_schema.sql; this file
-- additionally creates the four synthetic provenance tables so the platform can
-- audit generated records without polluting the official demo schema.
--
-- Safe to run repeatedly: it creates missing objects only (CREATE TABLE IF NOT EXISTS).

CREATE DATABASE IF NOT EXISTS forge_sight_synthetic_annual_v3r2
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_0900_ai_ci;

USE forge_sight_synthetic_annual_v3r2;
SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS dim_product (
  product_id VARCHAR(16) NOT NULL COMMENT '产品 ID',
  product_code VARCHAR(32) NOT NULL COMMENT '产品编码',
  product_name VARCHAR(100) NOT NULL COMMENT '产品名称',
  product_model VARCHAR(50) NOT NULL COMMENT '产品型号',
  product_category VARCHAR(50) NOT NULL COMMENT '产品类别',
  unit VARCHAR(16) NOT NULL COMMENT '计量单位',
  is_active BOOLEAN NOT NULL COMMENT '是否启用',
  PRIMARY KEY (product_id),
  UNIQUE KEY uk_dim_product_code (product_code),
  KEY idx_dim_product_category (product_category)
) ENGINE=InnoDB COMMENT='产品主数据';
CREATE TABLE IF NOT EXISTS dim_process (
  process_id VARCHAR(16) NOT NULL COMMENT '工序 ID',
  process_code VARCHAR(32) NOT NULL COMMENT '工序编码',
  process_name VARCHAR(100) NOT NULL COMMENT '工序名称',
  process_seq SMALLINT UNSIGNED NOT NULL COMMENT '工序顺序',
  standard_yield_rate DECIMAL(7,6) NOT NULL COMMENT '标准良率',
  is_key_process BOOLEAN NOT NULL COMMENT '是否关键工序',
  PRIMARY KEY (process_id),
  UNIQUE KEY uk_dim_process_code (process_code),
  UNIQUE KEY uk_dim_process_seq (process_seq),
  CONSTRAINT chk_process_yield_rate CHECK (standard_yield_rate BETWEEN 0 AND 1)
) ENGINE=InnoDB COMMENT='工序主数据';
CREATE TABLE IF NOT EXISTS dim_production_line (
  line_id VARCHAR(16) NOT NULL COMMENT '产线 ID',
  line_code VARCHAR(32) NOT NULL COMMENT '产线编码',
  line_name VARCHAR(100) NOT NULL COMMENT '产线名称',
  workshop_name VARCHAR(100) NOT NULL COMMENT '车间名称',
  line_manager VARCHAR(50) NOT NULL COMMENT '产线负责人',
  line_status VARCHAR(24) NOT NULL COMMENT '产线状态',
  PRIMARY KEY (line_id),
  UNIQUE KEY uk_dim_line_code (line_code),
  KEY idx_dim_line_status (line_status)
) ENGINE=InnoDB COMMENT='产线主数据';
CREATE TABLE IF NOT EXISTS dim_equipment (
  equipment_id VARCHAR(16) NOT NULL COMMENT '设备 ID',
  equipment_code VARCHAR(32) NOT NULL COMMENT '设备编码',
  equipment_name VARCHAR(120) NOT NULL COMMENT '设备名称',
  equipment_type VARCHAR(50) NOT NULL COMMENT '设备类型',
  line_id VARCHAR(16) NOT NULL COMMENT '所属产线 ID',
  install_date DATE NOT NULL COMMENT '安装日期',
  equipment_status VARCHAR(24) NOT NULL COMMENT '设备状态',
  PRIMARY KEY (equipment_id),
  UNIQUE KEY uk_dim_equipment_code (equipment_code),
  KEY idx_equipment_line (line_id),
  KEY idx_equipment_status (equipment_status),
  CONSTRAINT fk_equipment_line FOREIGN KEY (line_id)
    REFERENCES dim_production_line (line_id)
) ENGINE=InnoDB COMMENT='设备主数据';
CREATE TABLE IF NOT EXISTS mes_work_order (
  work_order_id VARCHAR(20) NOT NULL COMMENT '工单 ID',
  work_order_no VARCHAR(40) NOT NULL COMMENT '工单编号',
  product_id VARCHAR(16) NOT NULL COMMENT '产品 ID',
  line_id VARCHAR(16) NOT NULL COMMENT '产线 ID',
  plan_qty INT UNSIGNED NOT NULL COMMENT '计划数量',
  start_date DATE NOT NULL COMMENT '计划开始日期',
  end_date DATE NOT NULL COMMENT '计划结束日期',
  order_status VARCHAR(24) NOT NULL COMMENT '工单状态',
  PRIMARY KEY (work_order_id),
  UNIQUE KEY uk_work_order_no (work_order_no),
  KEY idx_work_order_product (product_id),
  KEY idx_work_order_line_dates (line_id, start_date, end_date),
  KEY idx_work_order_status (order_status),
  CONSTRAINT fk_work_order_product FOREIGN KEY (product_id)
    REFERENCES dim_product (product_id),
  CONSTRAINT fk_work_order_line FOREIGN KEY (line_id)
    REFERENCES dim_production_line (line_id),
  CONSTRAINT chk_work_order_dates CHECK (end_date >= start_date)
) ENGINE=InnoDB COMMENT='生产工单';
CREATE TABLE IF NOT EXISTS mes_process_output (
  output_id VARCHAR(20) NOT NULL COMMENT '产量记录 ID',
  work_order_id VARCHAR(20) NOT NULL COMMENT '工单 ID',
  product_id VARCHAR(16) NOT NULL COMMENT '产品 ID',
  process_id VARCHAR(16) NOT NULL COMMENT '工序 ID',
  line_id VARCHAR(16) NOT NULL COMMENT '产线 ID',
  stat_date DATE NOT NULL COMMENT '统计日期',
  input_qty INT UNSIGNED NOT NULL COMMENT '投入数量',
  good_qty INT UNSIGNED NOT NULL COMMENT '合格数量',
  defect_qty INT UNSIGNED NOT NULL COMMENT '不良数量',
  rework_qty INT UNSIGNED NOT NULL COMMENT '返工数量',
  shift_code CHAR(1) NOT NULL COMMENT '班次：D 白班，N 夜班',
  PRIMARY KEY (output_id),
  KEY idx_output_date_line (stat_date, line_id),
  KEY idx_output_process_date (process_id, stat_date),
  KEY idx_output_product_date (product_id, stat_date),
  KEY idx_output_work_order (work_order_id),
  CONSTRAINT fk_output_work_order FOREIGN KEY (work_order_id)
    REFERENCES mes_work_order (work_order_id),
  CONSTRAINT fk_output_product FOREIGN KEY (product_id)
    REFERENCES dim_product (product_id),
  CONSTRAINT fk_output_process FOREIGN KEY (process_id)
    REFERENCES dim_process (process_id),
  CONSTRAINT fk_output_line FOREIGN KEY (line_id)
    REFERENCES dim_production_line (line_id),
  CONSTRAINT chk_output_shift CHECK (shift_code IN ('D', 'N'))
) ENGINE=InnoDB COMMENT='工序产量';
CREATE TABLE IF NOT EXISTS qms_inspection (
  inspection_id VARCHAR(20) NOT NULL COMMENT '检验记录 ID',
  inspection_no VARCHAR(40) NOT NULL COMMENT '检验单号',
  work_order_id VARCHAR(20) NOT NULL COMMENT '工单 ID',
  product_id VARCHAR(16) NOT NULL COMMENT '产品 ID',
  process_id VARCHAR(16) NOT NULL COMMENT '工序 ID',
  inspection_date DATE NOT NULL COMMENT '检验日期',
  sample_qty INT UNSIGNED NOT NULL COMMENT '抽检数量',
  defect_qty INT UNSIGNED NOT NULL COMMENT '不良数量',
  inspection_result VARCHAR(16) NOT NULL COMMENT '检验结果',
  PRIMARY KEY (inspection_id),
  UNIQUE KEY uk_inspection_no (inspection_no),
  KEY idx_inspection_date_process (inspection_date, process_id),
  KEY idx_inspection_product_date (product_id, inspection_date),
  KEY idx_inspection_work_order (work_order_id),
  KEY idx_inspection_result (inspection_result),
  CONSTRAINT fk_inspection_work_order FOREIGN KEY (work_order_id)
    REFERENCES mes_work_order (work_order_id),
  CONSTRAINT fk_inspection_product FOREIGN KEY (product_id)
    REFERENCES dim_product (product_id),
  CONSTRAINT fk_inspection_process FOREIGN KEY (process_id)
    REFERENCES dim_process (process_id),
  CONSTRAINT chk_inspection_result CHECK (inspection_result IN ('pass', 'fail')),
  CONSTRAINT chk_inspection_qty CHECK (defect_qty <= sample_qty)
) ENGINE=InnoDB COMMENT='质量检验';
CREATE TABLE IF NOT EXISTS qms_defect_detail (
  defect_id VARCHAR(20) NOT NULL COMMENT '不良明细 ID',
  inspection_id VARCHAR(20) NOT NULL COMMENT '检验记录 ID',
  defect_type VARCHAR(100) NOT NULL COMMENT '不良类型',
  defect_code VARCHAR(20) NOT NULL COMMENT '不良代码',
  defect_qty INT UNSIGNED NOT NULL COMMENT '不良数量',
  severity_level VARCHAR(16) NOT NULL COMMENT '严重等级',
  responsible_process_id VARCHAR(16) NOT NULL COMMENT '责任工序 ID',
  PRIMARY KEY (defect_id),
  KEY idx_defect_inspection (inspection_id),
  KEY idx_defect_type (defect_type),
  KEY idx_defect_process_severity (responsible_process_id, severity_level),
  CONSTRAINT fk_defect_inspection FOREIGN KEY (inspection_id)
    REFERENCES qms_inspection (inspection_id),
  CONSTRAINT fk_defect_process FOREIGN KEY (responsible_process_id)
    REFERENCES dim_process (process_id),
  CONSTRAINT chk_defect_severity CHECK (severity_level IN ('minor', 'major', 'critical'))
) ENGINE=InnoDB COMMENT='不良明细';
CREATE TABLE IF NOT EXISTS eqp_downtime_record (
  downtime_id VARCHAR(20) NOT NULL COMMENT '停机记录 ID',
  equipment_id VARCHAR(16) NOT NULL COMMENT '设备 ID',
  line_id VARCHAR(16) NOT NULL COMMENT '产线 ID',
  start_time DATETIME NOT NULL COMMENT '停机开始时间',
  end_time DATETIME NOT NULL COMMENT '停机结束时间',
  downtime_minutes INT UNSIGNED NOT NULL COMMENT '停机分钟数',
  downtime_reason VARCHAR(100) NOT NULL COMMENT '停机原因',
  is_planned BOOLEAN NOT NULL COMMENT '是否计划停机',
  PRIMARY KEY (downtime_id),
  KEY idx_downtime_equipment_time (equipment_id, start_time),
  KEY idx_downtime_line_time (line_id, start_time),
  KEY idx_downtime_planned (is_planned),
  CONSTRAINT fk_downtime_equipment FOREIGN KEY (equipment_id)
    REFERENCES dim_equipment (equipment_id),
  CONSTRAINT fk_downtime_line FOREIGN KEY (line_id)
    REFERENCES dim_production_line (line_id),
  CONSTRAINT chk_downtime_times CHECK (end_time >= start_time)
) ENGINE=InnoDB COMMENT='设备停机记录';
CREATE TABLE IF NOT EXISTS inv_inventory_snapshot (
  snapshot_id VARCHAR(20) NOT NULL COMMENT '库存快照 ID',
  snapshot_date DATE NOT NULL COMMENT '快照日期',
  product_id VARCHAR(16) NOT NULL COMMENT '产品 ID',
  warehouse_code VARCHAR(20) NOT NULL COMMENT '仓库编码',
  available_qty INT UNSIGNED NOT NULL COMMENT '可用库存',
  frozen_qty INT UNSIGNED NOT NULL COMMENT '冻结库存',
  safety_stock_qty INT UNSIGNED NOT NULL COMMENT '安全库存',
  PRIMARY KEY (snapshot_id),
  KEY idx_inventory_date_product (snapshot_date, product_id),
  KEY idx_inventory_warehouse_date (warehouse_code, snapshot_date),
  CONSTRAINT fk_inventory_product FOREIGN KEY (product_id)
    REFERENCES dim_product (product_id)
) ENGINE=InnoDB COMMENT='库存快照';

CREATE TABLE IF NOT EXISTS synthetic_assumption_registry (
  generation_batch VARCHAR(64) NOT NULL COMMENT '生成批次',
  parameter_key VARCHAR(128) NOT NULL COMMENT '参数键',
  parameter_value TEXT NOT NULL COMMENT '参数值',
  parameter_origin VARCHAR(64) NOT NULL COMMENT '经验估计/场景假设/混合',
  source_window VARCHAR(64) NOT NULL COMMENT '来源时间窗口',
  confidence VARCHAR(32) NOT NULL COMMENT '参数可信等级',
  version VARCHAR(64) NOT NULL COMMENT '参数版本',
  approval_status VARCHAR(64) NOT NULL COMMENT '审核状态',
  notes TEXT COMMENT '说明',
  PRIMARY KEY (generation_batch, parameter_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='合成数据参数假设与审批状态';
CREATE TABLE IF NOT EXISTS synthetic_batch_registry (
  batch_id VARCHAR(64) NOT NULL COMMENT '批次 ID',
  manifest_json JSON NOT NULL COMMENT '生成配置与数据范围',
  validation_json JSON NOT NULL COMMENT '质量验收结果',
  loaded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间',
  PRIMARY KEY (batch_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='合成批次登记';
CREATE TABLE IF NOT EXISTS synthetic_provenance (
  table_name VARCHAR(64) NOT NULL COMMENT '业务表名',
  record_id VARCHAR(64) NOT NULL COMMENT '合成记录主键',
  is_synthetic TINYINT(1) NOT NULL COMMENT '是否为合成记录',
  scenario_type VARCHAR(64) NOT NULL COMMENT '场景类型',
  generation_batch VARCHAR(64) NOT NULL COMMENT '生成批次',
  generation_rule VARCHAR(100) NOT NULL COMMENT '生成规则',
  parent_record_id VARCHAR(64) NULL COMMENT '父记录（顶层工单为空）',
  source_record_id VARCHAR(64) NOT NULL COMMENT '原始样本记录',
  seed BIGINT NOT NULL COMMENT '随机种子',
  source_window VARCHAR(64) NULL COMMENT '参数估计时间窗口',
  parameter_origin VARCHAR(64) NULL COMMENT '参数来源',
  uncertainty_level VARCHAR(32) NULL COMMENT '不确定性等级',
  PRIMARY KEY (table_name, record_id),
  KEY idx_synthetic_batch (generation_batch),
  KEY idx_synthetic_scenario (scenario_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='合成数据来源追踪';
CREATE TABLE IF NOT EXISTS synthetic_simulation_event_log (
  generation_batch VARCHAR(64) NOT NULL COMMENT '生成批次',
  event_id VARCHAR(64) NOT NULL COMMENT '事件 ID',
  event_date DATE NOT NULL COMMENT '事件日期',
  event_type VARCHAR(100) NOT NULL COMMENT '事件类型',
  entity_type VARCHAR(64) NOT NULL COMMENT '实体类型',
  entity_id VARCHAR(128) NOT NULL COMMENT '实体 ID',
  scenario_type VARCHAR(64) NOT NULL COMMENT '场景类型',
  payload_json JSON NOT NULL COMMENT '事件载荷',
  PRIMARY KEY (generation_batch, event_id),
  KEY idx_synthetic_event_date (event_date),
  KEY idx_synthetic_event_entity (entity_type, entity_id),
  KEY idx_synthetic_event_scenario (scenario_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='合成年度状态与流量事件';
