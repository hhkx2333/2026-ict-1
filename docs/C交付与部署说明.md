# C 模块交付与上云前检查

本轮实现应用工程和部署准备。没有新建、购买或部署华为云资源，没有发起新的真实模型请求，没有生成真实教师审核记录或 CodeArts 使用记录。离线合成回放只验证系统行为，不能证明评分准确性或校准收益。

## 固定输入与职责

- A：`测试数据` 提交 `84357c86ca1cd16d93722e176349d6e2b5316a72`，标签集 `2026-11-16-user-approved-v1`。15 题、1,582 份无分答案、92 份未批准校准候选；规则保持 DRAFT。下载保留 `题目答案相关数据/LICENSE`、来源说明和修改记录。
- B：`codex/b-grading-mvp` 提交 `ca38a5b99ef6c77adb73e4d5235520646b61ef33`。源码包 SHA256 为 `fa992d4fc555b696758ac8ca8d3311c8f6d5d347a5d8e9edded12e023a10008b`。纳入 46 个源码、示例、测试、历史说明文件，`B_SOURCE.json` 可逐文件核对；没有改动 B 算法。
- C：负责身份、课程、持久化、固定版本、任务恢复、真实审核记录、正式导出与运行交付。账号角色只是应用授权；管理员应向实际承担审核职责的人发放教师账号，不把建号当成外部身份核验。

## 已实现的行为

教师可建课程、建题、保存规则草案、审核后生成独立发布版本，导入无分 JSONL，创建批次，查看逐项建议和原文依据，逐项修正，单独批准案例复用，并导出确认结果。发布、复核、案例复用与账号操作都有服务端审计记录。已有规则或答案后题目内容冻结，需以新题号承载变更，避免改写历史依据；未使用题目可编辑。

学生仅能查看所属课程内分配给本人且已发布的最新确认结果。规则变更后旧结果隐藏并提示教师重审。推荐仅来自对应规则版本的教师审核题库，排除已做题。学生提交练习后可查看参考答案；不计算长期掌握率或学习提升。

所有评分请求由后台 Worker 从数据库快照加载。浏览器不能提交 `teacher_confirmed`、`teacher_approved` 或标签分；真实身份与批准来源由服务端记录。B 的 `grade_submission`、`correction_draft`、`case_from_confirmed`、`feedback_and_practice` 是调用边界。接口契约保持 B 的 `schema_version=1`，C API 前缀为 `/api/v1`；开发环境 `/docs` 提供请求字段定义。

任务状态：PENDING 等待、RUNNING 处理中、COMPLETED 调用结束、FAILED 失败、NEEDS_ATTENTION 中断且调用结果未知、RETRIED 已创建重试。COMPLETED 内仍区分 SUGGESTED 与 REVIEW_REQUIRED，不等同教师确认。每份评分最多两次模型请求；数据库唯一索引防止同一答案重复进行评分。

领取任务使用原子条件更新与随机领取标识，完成写入必须仍持有该标识。运行超过 300 秒且未完成的任务转 NEEDS_ATTENTION，保留不确定性；不会自动重新发送答案。人工确认后才能显式重试，真实模型重试需再次确认可能计费。默认部署一个 Worker；待处理任务在进程重启后继续保留。

正式导出仅包含当前规则下最新教师确认结果；零分有效，null 不补零。草案、合成、失败、旧版和旧原型未核验记录不进入正式导出。试验导出单独命名并逐行标记用途。教师确认与学生端发布是不同操作；教师确认成绩可导出，只有主动发布后学生才看得到。

## 本地与容器验证

本机为 Windows、Python 3.14；Linux 容器使用 Python 3.12。依赖直接版本在 `requirements.txt`，本轮实际解析的完整版本在 `requirements.lock.txt`。

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q --basetemp=c-prototype/work/pytest-new-check
.\.venv\Scripts\python.exe -m alembic check
.\.venv\Scripts\python.exe -m c_app.verify_delivery
.\.venv\Scripts\python.exe -m c_app.verify_a
node --check c-prototype/app.js
```

`--basetemp` 目录仅供测试使用，pytest 会清理它，不能指向存放用户材料的目录。本机默认系统临时测试目录曾报 `PermissionError [WinError 5]`，因此测试产物放到工作区内新的专用目录。B 历史源码与合成测试保持原样；C 验证覆盖课程隔离、CSRF、导入回滚、标签拒绝、批次去重、Worker 领取竞争、中断重试、证据校验、试验与正式导出、案例批准、学生反馈和原库只读迁移。

本机没有 Docker、PostgreSQL 和 Redis 服务，不能将配置检查写成这些服务已运行。GitHub Actions 单独运行 Python 3.12/3.14、PostgreSQL 16、Redis 7、迁移检查与镜像构建，再启动容器中的独立 Worker 跑合成任务。每次实际检查结果以对应提交的 Actions 页面为准。Redis 未配置时本地对应测试明确跳过。

有 Docker 的环境中，复制 `.env.example` 为本机 `.env`，为本地 PostgreSQL 填入独立密码，然后：

```sh
docker compose config --quiet
docker compose up --build -d --wait web worker
docker compose exec web python -m c_app.cli bootstrap --username your-admin
docker compose exec web python -m c_app.cli demo --username your-admin
```

Compose 只映射本机 `127.0.0.1:18065`。PostgreSQL 是持久业务库；Redis 用于登录限流，未配置 Redis 时仍有数据库限流。任务与结果不依赖易失的缓存队列。容器以非 root 用户运行。源码镜像不含学生原始数据、数据库、`.env` 或本机登录文件。

`/health/live` 仅检查服务响应；`/health/ready` 检查迁移、数据库和已配置 Redis。容器启用 `C_REQUIRE_WORKER=true`，还要求最近 300 秒有 Worker 心跳；每次模型调用可能耗时较长，心跳阈值与调用上限配套。健康检查不能替代完整业务验收。

## 下一阶段：真实云端检测

本仓库提供 `compose.cloud.yaml` 连接已有 PostgreSQL/RDS 与可选 Redis/DCS，不创建资源。进入这一阶段前，先确认队伍实际账号、区域、资源额度、数据库类型和访问条件；PostgreSQL 兼容适配不能当成已验证其他 RDS 引擎。

1. 在实际 CodeArts 中打开本提交，记录实际完成的开发或部署操作；取得并核对适用的已有部署 Skill，不预填成功记录。
2. 确认已有云服务器、私网 RDS PostgreSQL、数据库账号、CA 证书和可选 Redis/DCS。数据库连接启用证书验证；口令中的 URL 保留字符须编码。缓存如跨主机使用，应按实例要求配置 TLS 与鉴权。
3. 将数据库和模型配置仅放服务端受保护配置；不写入仓库、浏览器、部署截图或公开日志。保持 `C_ALLOW_LIVE=false`，先验证数据库读写、权限与合成流程。
4. 用 Nginx 等已有反向代理提供有效 HTTPS，后端保持本机绑定。转发 `Host` 与 `X-Forwarded-Proto`；`FORWARDED_ALLOW_IPS` 仅填写实际可信代理源地址。生产模式 Cookie 仅通过 HTTPS 传输。
5. 执行迁移并启动 web、worker，检查健康状态。用另一台设备完成登录→导入→任务→复核→导出，确认数据库重启后记录仍在、失败不计完成、越权被拒。
6. 取得真实模型服务的端点、模型和授权后，才开启真实调用。先以获准的单条脱敏答案检测协议、耗时和用量，再决定批次。配置就绪不自动触发请求。
7. 教师实际审核发布规则、逐项确认和批准复用后，再验证真实校准路径。记录实际版本、任务编号、脱敏日志、费用和测试日期；没有实测的项目保持待验证。

云资源与 Skills 的赛事口径、真实身份核验、教师规则与案例审核、独立最终测试和真实工时，需要相应人员实际完成。当前不将历史十份开发样本结果写成 C 新测效果。

## 备份、恢复与回退

- 原始 `grading.sqlite3` 保持不变；`import-legacy` 使用只读连接，将原记录保存在新库独立历史表，标注身份未核验。它不会把旧确认升级为正式成绩。
- SQLite 使用 `python -m c_app.backup --output c-prototype/work/backups/application-new.sqlite3` 生成一致备份；目标必须是新文件。恢复时先停 web/worker，把备份复制到**新的**数据库路径，设置 `DATABASE_URL` 后检查迁移和记录，再启动服务，保留原库供回退。
- PostgreSQL 使用所属数据库版本的 `pg_dump` 或 RDS 备份功能，恢复到新的空库；先验证表、任务与审核记录，再切换连接地址。不要直接将试验恢复覆盖现有业务库。
- 源码按 Git 提交回退；数据库迁移先检查兼容性。初始迁移的 downgrade 会删表，不能用它替代业务回退。

本轮默认仅推送 C 分支和创建合并请求，合并主分支由团队评审决定。交付不包含云端收费操作。
