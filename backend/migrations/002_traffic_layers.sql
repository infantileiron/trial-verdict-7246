-- 流量分层与分流资格：在既有实验档案之上增加流量层、规则版本与独占资格。
-- 应用通过 SQLAlchemy metadata 初始化新库；本文件用于已存在的 MySQL 数据卷升级。
-- 建议先备份；演示环境可直接 docker compose down -v 重建。

CREATE TABLE IF NOT EXISTS layers (
  id INT PRIMARY KEY AUTO_INCREMENT,
  code VARCHAR(60) NOT NULL UNIQUE,
  name VARCHAR(120) NOT NULL,
  description TEXT,
  salt VARCHAR(64) NOT NULL,
  created_by VARCHAR(100),
  created_at VARCHAR(40)
);

CREATE TABLE IF NOT EXISTS traffic_rules (
  id INT PRIMARY KEY AUTO_INCREMENT,
  layer_id INT NOT NULL,
  version INT NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'draft',
  allocations JSON NOT NULL,
  note TEXT,
  created_by VARCHAR(100),
  created_at VARCHAR(40),
  activated_by VARCHAR(100) DEFAULT '',
  activated_at VARCHAR(40) DEFAULT '',
  archived_at VARCHAR(40) DEFAULT '',
  active_slot INT NULL,
  UNIQUE KEY uq_rule_layer_version (layer_id, version),
  UNIQUE KEY uq_rule_layer_active (layer_id, active_slot),
  CONSTRAINT fk_rules_layer FOREIGN KEY (layer_id) REFERENCES layers(id)
);

-- 每个实验每个用户只有一条资格；同一流量层内同时只能有一条生效资格（active_slot=user_key）。
CREATE TABLE IF NOT EXISTS assignments (
  id INT PRIMARY KEY AUTO_INCREMENT,
  layer_id INT NOT NULL,
  experiment_id INT NOT NULL,
  user_key VARCHAR(80) NOT NULL,
  `group` VARCHAR(1) NOT NULL,
  rule_id INT NULL,
  rule_version INT DEFAULT 0,
  status VARCHAR(20) NOT NULL DEFAULT 'active',
  active_slot VARCHAR(80) NULL,
  experiment_bucket DOUBLE NULL,
  group_bucket DOUBLE NULL,
  assigned_at VARCHAR(40),
  released_at VARCHAR(40) DEFAULT '',
  release_reason TEXT,
  UNIQUE KEY uq_assign_exp_user (experiment_id, user_key),
  UNIQUE KEY uq_assign_layer_active (layer_id, active_slot),
  CONSTRAINT fk_assign_layer FOREIGN KEY (layer_id) REFERENCES layers(id),
  CONSTRAINT fk_assign_experiment FOREIGN KEY (experiment_id) REFERENCES experiments(id),
  CONSTRAINT fk_assign_rule FOREIGN KEY (rule_id) REFERENCES traffic_rules(id)
);

-- 既有实验加入默认流量层，保证历史事件可按新模型核对；独立升级前请确认业务归属。
INSERT INTO layers (code, name, description, salt, created_by, created_at)
SELECT 'onboarding', '首次引导流量层', '升级时为既有实验建立的默认流量层', REPLACE(UUID(),'-',''), 'migration', DATE_FORMAT(UTC_TIMESTAMP(),'%Y-%m-%dT%H:%i:%s+00:00')
WHERE NOT EXISTS (SELECT 1 FROM layers WHERE code='onboarding');

ALTER TABLE experiments
  ADD COLUMN layer_id INT NULL,
  ADD COLUMN status VARCHAR(20) DEFAULT 'active',
  ADD COLUMN stopped_at VARCHAR(40) DEFAULT '',
  ADD COLUMN stopped_by VARCHAR(100) DEFAULT '',
  ADD CONSTRAINT fk_experiments_layer FOREIGN KEY (layer_id) REFERENCES layers(id);

UPDATE experiments SET layer_id=(SELECT id FROM layers WHERE code='onboarding') WHERE layer_id IS NULL;

ALTER TABLE events
  ADD COLUMN assignment_id INT NULL,
  ADD COLUMN late INT DEFAULT 0,
  ADD CONSTRAINT fk_events_assignment FOREIGN KEY (assignment_id) REFERENCES assignments(id);

-- 归属冲突阻止发布；排查后由分析员附结论解除。
CREATE TABLE IF NOT EXISTS conflicts (
  id INT PRIMARY KEY AUTO_INCREMENT,
  experiment_id INT NOT NULL,
  layer_id INT NULL,
  user_key VARCHAR(80) NOT NULL,
  kind VARCHAR(40) NOT NULL,
  reason TEXT,
  detail TEXT,
  status VARCHAR(20) NOT NULL DEFAULT 'open',
  resolved_by VARCHAR(100) DEFAULT '',
  resolved_at VARCHAR(40) DEFAULT '',
  resolution TEXT,
  created VARCHAR(40),
  UNIQUE KEY uq_conflict_exp_user_kind (experiment_id, user_key, kind),
  CONSTRAINT fk_conflict_experiment FOREIGN KEY (experiment_id) REFERENCES experiments(id)
);

-- 采集拒收留痕：说明为什么没有接收这条曝光/激活。
CREATE TABLE IF NOT EXISTS rejections (
  id INT PRIMARY KEY AUTO_INCREMENT,
  experiment_id INT NULL,
  layer_id INT NULL,
  user_key VARCHAR(80) NOT NULL,
  kind VARCHAR(20) NOT NULL,
  `group` VARCHAR(1) NULL,
  reason TEXT,
  created VARCHAR(40)
);
