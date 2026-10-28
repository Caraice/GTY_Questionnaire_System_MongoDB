# 数据迁移约定

当前数据库版本为 1，建库定义在 `database/schema.py`。后续按 `002_描述.py` 编号新增迁移；记录迁移前后的字段、旧数据填充规则、执行结果和回滚方案。不得清空旧集合或重新建立整个系统。

启动初始化仅为新集合设置 validator；既有集合的 validator 变更应通过显式迁移执行 `collMod`。嵌套题目与规则当前由 Pydantic 和业务规则校验。schema_version 表示文档结构版本，version 为管理修订号，published_version 为发布后冻结的内容版本，三者不能混用。

0.4.0 增加可选 published_version。新发布时写入；旧 published 读时回退到现有 version，在首次期限/关闭变更时以原 version 补写。该变更不删旧字段、不重建集合，现有 validator 允许新增字段，因此采用兼容读取和写入时补全，不执行破坏性迁移。

0.5.0 新增 responses 的非唯一 versioned_statistics 复合索引（survey_id、survey_version、submitted_at、_id 均升序），由幂等 initialize 创建，支持统计匹配和稳定排序。保留既有索引、文档与 validator，schema_version 不变。

0.6.0 新增 app_settings 集合及其新集合 validator，保存 response_hmac 持久密钥；首次提交原子 setOnInsert，不替换或轮换已有密钥。responses 增加可选 request_hash，用于原始请求幂等比较；surveys 增加可选 submission_sequence，仅作为准入原子写入的序号，不改变管理 version，也不作统计。旧 validator 允许这些附加字段，无需 collMod；既有集合、答卷和历史基线不删除，schema_version 仍为 1。恢复数据库须包括 app_settings；旧答卷如缺 request_hash，不能假定可以证明某原始请求相同，复用该键返回冲突，不重写原记录。

