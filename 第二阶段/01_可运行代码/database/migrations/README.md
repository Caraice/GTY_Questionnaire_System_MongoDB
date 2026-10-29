# 数据迁移约定

当前0.9.0已接通题库、版本、共享与引用页面。002本轮识别已管理的question_sources，不把导入或主动升级的实例重复建为新题；当前衔接说明见 [第二阶段专题](../../../02_文档/关键逻辑说明.md)。下面002第一轮及更早条目按当时状态保留；启动不自动执行数据迁移。

## 第二阶段002迁移

当前新增 [002_question_library.py](002_question_library.py)，由 `python -m scripts.migrate_question_library --database 目标库` 执行只读预检，增加 `--apply` 才写入。新集合schema和索引见 [../library_schema.py](../library_schema.py)。正常网页启动不自动迁移；旧5个集合、validator、索引、题号、选项ID、rules、answers和path均保持原样。

本轮保留问卷嵌入快照，独立建立question_templates、question_versions、question_usages及schema_migrations。迁移编号2与每种文档自身schema_version=1是不同概念。设计、执行、冲突、中断续跑和回退方案见 [第二阶段基线与题目迁移](../../../02_文档/数据库设计.md)。题库、共享和跨问卷统计页面尚未在此轮实现。

以下保留第一阶段已有迁移约定与结构演进记录。

当前数据库版本为 1，建库定义在 `database/schema.py`。后续按 `002_描述.py` 编号新增迁移；记录迁移前后的字段、旧数据填充规则、执行结果和回滚方案。不得清空旧集合或重新建立整个系统。

启动初始化仅为新集合设置 validator；既有集合的 validator 变更应通过显式迁移执行 `collMod`。嵌套题目与规则当前由 Pydantic 和业务规则校验。schema_version 表示文档结构版本，version 为管理修订号，published_version 为发布后冻结的内容版本，三者不能混用。

0.4.0 增加可选 published_version。新发布时写入；旧 published 读时回退到现有 version，在首次期限/关闭变更时以原 version 补写。该变更不删旧字段、不重建集合，现有 validator 允许新增字段，因此采用兼容读取和写入时补全，不执行破坏性迁移。

0.5.0 新增 responses 的非唯一 versioned_statistics 复合索引（survey_id、survey_version、submitted_at、_id 均升序），由幂等 initialize 创建，支持统计匹配和稳定排序。保留既有索引、文档与 validator，schema_version 不变。

0.6.0 新增 app_settings 集合及其新集合 validator，保存 response_hmac 持久密钥；首次提交原子 setOnInsert，不替换或轮换已有密钥。responses 增加可选 request_hash，用于原始请求幂等比较；surveys 增加可选 submission_sequence，仅作为准入原子写入的序号，不改变管理 version，也不作统计。旧 validator 允许这些附加字段，无需 collMod；既有集合、答卷和历史基线不删除，schema_version 仍为 1。恢复数据库须包括 app_settings；旧答卷如缺 request_hash，不能假定可以证明某原始请求相同，复用该键返回冲突，不重写原记录。

# 0.9.0兼容补充

002继续默认只读预检，必须显式--apply，不随网页启动执行。本轮增加对surveys.question_sources的校验与识别：已在线导入或经过明确主动升级的实例不再被迁移成另一道题，历史版本和原question_usages不覆盖。共享撤销不影响旧的合法快照。没有在线来源的旧题继续按原流程检测冲突。详见docs/第二阶段/03-题库版本共享与引用.md和04验收记录；以下保留既有说明。

