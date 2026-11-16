# 试用激活实验裁决 · Trial Verdict

棱镜协作软件增长小组的实验工作簿：固定协议、核对试用事件、后台检验、负责人裁决，避免只看转化率就提前发布。

## 业务场景

棱镜是一家80人协作软件团队，增长组5人每周评估首次引导。上游产品事件过去由分析员导出到 `trial-events-1115.csv`，在工作簿去重并算A/B转化率，再在群聊申请上线。三个痛点：用户跨组和重复事件不易发现、观察期尚未结束就取结果、分流异常和发布理由未留档。本项目替代事件核对到产品裁决一段，下游由产品负责人使用冻结证据决定上线；采用模拟数据，不连接真实产品采集或发布服务。

## 业务模块

1. **实验档案**：实验编号、假设、带时区观察期、预期A组占比、所属流量层；没有事件时可修订协议API，有事件后禁止事后改写，管理员可停用实验并释放资格。
2. **事件核对**：曝光/激活，按实验+用户+类型去重，禁止同用户跨组、未来事件、超观察期及先激活后曝光；分层实验只接收与分流资格匹配的事件，拒收写入原因。
3. **流量分层**：把互斥实验挂在同一流量层；规则从草稿到生效逐版本留档，切换后既有资格不迁移、新来访使用新规则。
4. **分流资格**：埋点只报用户键，服务端按层盐值稳定哈希选实验并按实验A占比分A/B组；同一层内一个用户只有一个生效资格，并发/重试不重复分配。
5. **实验裁决**：Redis队列由Python worker真算比例z检验、SRM卡方、绝对提升及95%区间，冻结协议、流量层规则、全部资格与冲突快照。
6. **发布记录**：分析员填写业务理由，管理员签核上线B或保留A。未到期、SRM异常、小样本、未解除归属冲突禁止发布；上线B还需正向显著提升。已发布裁决不可覆盖。

| 角色 | 页面 | 权限与动作 |
|---|---|---|
| admin | 全部、审计API | 编辑业务、执行检验、停用实验、签核发布 |
| analyst | 六业务页 | 建立流量层与规则、请求分流、录入事件、建实验、提交检验、解除归属冲突、起草理由；不能发布 |
| viewer | 六业务页 | 只读、下载已有证据；服务端阻止一切写入/计算 |

演示账号 `admin@demo.local` / `analyst@demo.local` / `viewer@demo.local`，密码均为 `Demo123!`。JWT有效8小时，生产应更换JWT_SECRET与数据库密码。

## 真实计算与口径

输入是冻结的实验协议和试用事件。曝光人数 nA/nB 按用户去重，激活人数 xA/xB 为本组曝光用户的激活子集，方向固定为B−A。

- pA=xA/nA，pB=xB/nB，差异 Δ=pB−pA，合并比例 p=(xA+xB)/(nA+nB)。z=Δ/√[p(1−p)(1/nA+1/nB)]，双侧p=2Φ(−|z|)。两组均零或均全激活时z=0、p=1，但正态近似门禁不通过。
- 95%绝对提升Wald区间为 Δ ± 1.959964√[pA(1−pA)/nA+pB(1−pB)/nB]，只裁到概率差理论范围[-1,1]。它是大样本近似，非精确区间。
- SRM：预期人数为总曝光×预定分流比例，χ²=Σ(O−E)²/E，df=1。p<0.01说明分流异常，禁止发布。
- 每组至少20曝光，且两组成功/失败各至少5人，才允许按正态近似发布；每组不足2人直接任务失败。分流合格、观察期结束后才能发布；上线B另需Δ>0且双侧p<0.05。保留A表示负责人暂不切换，并不宣称两版本等效。
- 置信区间以百分点展示。p值后台保留完整精度，前端小值科学计数。相对提升只在pA>0时提供。结果有SHA256校验和与算法版本`two-proportion-srm-2.0`。
- 发布时重新检查冻结协议的结束时间与原快照门禁，后加样本不会改变旧结果。新数据须新建裁决。观察期按UTC存储、界面按上海时区展示，不使用反复偷看校正/多重检验，业务负责人应使用预先定义的单次检验。
- 裁决结果同时冻结分流对账：资格人数按组、曝光人数、生效/已释放资格数、有资格未曝光、有曝光无资格、组别不符、迟到事件、同层多重资格与未解除冲突。规则依据记录裁决时的生效规则版本、各资格实际引用的规则版本及 SHA256 校验和，后改规则不会改变旧裁决。

## 分流口径

- 流量层持有稳定盐值。实验桶 = HMAC-SHA256(salt, `experiment|user_key`) 前 64 位归一化到 [0,1)，按生效规则中实验的累计比例选取实验；未命中的流量保留未分配，不做静默补位。
- 组别桶 = HMAC-SHA256(salt, `group|实验编号|user_key`)，小于实验A占比进A，否则进B。因此同一用户在同一实验内组别永远一致，不受规则版本影响。
- 规则由版本号递增的草稿发布：草稿可修订，只有草稿能生效；生效时归档上一版。生效后既有 `assignments` 不迁移，新请求按新规则计算，规则历史（起草人、生效人、归档时间）在流量分层页可查。
- 资格唯一键：`(experiment_id, user_key)` 保证同实验不重复入组；`(layer_id, active_slot=user_key)` 保证同层同时只有一个生效资格。并发或失败重试命中唯一键时回读已有资格返回，不产生第二条分配。
- 采集接口按用户键查资格：无资格返回 422；同层已归属其他实验返回 409 并记归属冲突；自报组别与资格不符返回 409 并记冲突；停用实验的资格被释放后，观察期内的迟到事件仍写入原实验并标 `late=1`，组别锁定为原资格组，不会被重新分配到别的实验。
- 归属冲突（`group_mismatch`、`layer_reentry`）需分析员在分流资格页填写排查结论后解除；未解除冲突会让该实验的裁决 `release_eligible=false` 并阻断发布。已解除的冲突若再次出现会自动重新打开。

## seed与一天剧本

固定自然键：成熟实验 `LJ-1115-A`，1200名用户各600人/组，A激活120人、B激活180人；1500条曝光/激活事件。`LJ-1115-B`为尚未到期实验。两者同属 `onboarding` 流量层，规则 v1（各50%）已归档、v2（B 100%）生效，历史上1200个资格引用v1，说明切换规则不迁移既有分配。时间相对首次seed当日生成，已存在协议、事件、用户、规则不覆盖。重复seed不会增加重复记录，也不覆盖修改。

1. 08:50 viewer核对首次引导协议，确认A分流50%，并在流量分层页确认生效规则v2与历史v1。
2. 09:00 analyst查看1200名用户的1500条事件，核对`LJ-U0001`。
3. 09:05 analyst在分流资格页为新用户请求分流，重复点击返回同一资格；再录入曝光，激活时间不得早于曝光。
4. 09:10 用错误自报组别再录一条事件会被409拒收，拒收原因与归属冲突出现在分流资格页。
5. 09:20 选择 `LJ-1115-A` 提交后台检验，冻结样本、规则版本、资格与冲突。
6. 09:30 刷新结果：seed原始数据A=20%、B=30%、绝对提升10个百分点，SRM p=1；对账显示资格1200、曝光1200、组别不符0。
7. 09:40 未解除冲突时发布会被409阻断；先在冲突卡片填写排查结论解除，再对照区间和样本量填写理由。
8. 10:00 admin签核上线B；未到期、分流异常或仍有冲突的实验会被409阻断。
9. 10:10 viewer下载 `trial-verdict-<id>.json`，产品负责人留存。发布记录只在本项目落库，不调用外部发布服务。

## 快速启动

```bash
cp .env.example .env
docker compose config --quiet
docker compose up -d --build
# 可选：手工复跑幂等演示seed
docker compose exec -T backend python -m scripts.seed_demo
```

前端 [http://localhost:18203](http://localhost:18203)，后端文档 [http://localhost:19203/docs](http://localhost:19203/docs)，MySQL宿主端口17703。停止保留数据 `docker compose down`；重置模拟数据 `docker compose down -v --remove-orphans`。

## 技术与目录

| 层 | 实现 |
|---|---|
| 前端 | 原生HTML/CSS/ES modules、Nginx，Google Fonts Noto Sans SC + ZCOOL XiaoWei |
| 后端 | Python 3.11 / FastAPI / SQLAlchemy / PyJWT / SciPy |
| 任务 | Redis持久队列 + 独立Python worker，启动回收queued/running任务 |
| 存储 | MySQL8.0，实验/事件/裁决/用户/审计；数据库、Redis、产物三命名卷 |

`frontend/src` 按api/stores/types/components/hooks/pages/router/utils/constants拆分；`backend/src` 按routes/controllers/services/models/schemas/middlewares/utils/constants/config拆分；`backend/scripts/seed_demo.py`；`backend/migrations`记录初始元数据约束。schema由SQLAlchemy metadata初始化。所有计算/校验/持久化均在Python。

共享枚举：后端 `backend/src/constants/enums.py`，前端 `frontend/src/constants/enums.js`，含角色admin/analyst/viewer、组别A/B、事件exposure/activation、任务queued/running/done/failed、裁决publish_b/keep_a、实验active/stopped、规则draft/active/archived、资格active/released、冲突open/resolved；schema的Literal校验和页面选择器使用同组值。

本地开发：后端安装 `backend/requirements.lock`（完整固定版本），设置DATABASE_URL、REDIS_URL、JWT_SECRET后在backend运行`uvicorn src.main:app --reload --port 8000`；另开`python -m src.worker`。前端可用本项目Nginx容器代理该后端，所有请求统一`/api/`剥前缀。常用API为 `/auth/login`、`/layers`、`/layers/{id}/rules`、`/rules/{id}/activate`、`/assign`、`/assignments`、`/conflicts`、`/conflicts/{id}/resolve`、`/rejections`、`/experiments`、`/experiments/{id}/stop`、`/events`、`/decisions`、`/decisions/{id}/rationale`、`/decisions/{id}/publish`、`/decisions/{id}/export`。

自动化测试在 `backend/tests/test_traffic_flow.py`，使用临时SQLite与假Redis，覆盖稳定分流、并发幂等、规则草稿/生效与历史保留、独占资格、拒收原因、停用释放与迟到事件、裁决冻结与对账、冲突阻断发布、权限和规则校验：

```bash
pip install -r backend/requirements-dev.txt
cd backend && python -m unittest discover -s tests -v
```

环境变量见`.env.example`：COMPOSE_PROJECT_NAME、三宿主端口、DB_PASSWORD、JWT_SECRET、SEED_DEMO、NETWORK_SUBNET；内部DATABASE_URL与REDIS_URL由Compose设置。默认显式子网10.253.104.0/24用于避开本机地址池耗尽，迁移时可修改。5服务都有健康检查，MySQL通过TCP SELECT1验证应用用户，worker用20秒心跳；后端健康检查同时查数据库和Redis。审计记录操作者/动作/UTC时间；请求ID、安全响应头与全局错误恢复构成横切处理。

## 已知差异

小型范围提供逐条事件录入与API，分流入口与埋点采集为同一项目内的模拟接口，不含CSV批量导入、外部产品埋点、通知和真实上线控制；事件列表最多显示最近80条但计算使用全部记录。实验协议修订只提供API且仅限无事件时；已有分流资格后不可再改A组占比或流量层，流量层编号在已有规则后不可改。原始事件采用追加留痕，不提供覆盖删除。前端字体网络不可用时退回字体类别。这里的显著结果仅是固定样本的大样本近似，不替代业务成本审查。

MIT License，见LICENSE。真实验收、测试脚本、截图位于源码目录外 `../../test-artifacts/gb-3104/`。
