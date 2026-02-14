## 你这次要改的是什么
- `configs/agent_configs.yaml` 里的顶层键（例如 `browser_worker:`）不是“显示名”，它是系统级 **role_key**：会被 UI、运行时心跳、workflow 调度、chromaserver 初始化、skills 目录等多处直接引用。
- `configs/prompts/system_prompts.yaml` 里的顶层键同样是 **role_key**（chromaserver 会强校验 key 必须存在）。
- 因此把 `browser_worker` 改成 `Worker_Nova`（或 `worker_nova`）属于“全仓级重命名”，必须同步改所有引用点；否则启动必炸。

## 推荐的 role_key 命名规范（避免踩坑）
- 推荐使用 **全小写 snake_case** 作为 role_key（例如 `worker_nova`、`king_tru`），而把 `Worker_Nova` 留给英文显示名。
- 但如果你坚持 role_key 也用 `Worker_Nova`（含大写），也能做，只是会让路径/排序/未来代码约定更别扭。

## 目标映射（我将按“推荐方案”执行）
把现有 6 个 role_key 全部改成新英文 role_key：
- `queen` → `queen_sera`
- `king` → `king_tru`
- `soldier` → `soldier_ares`
- `emotion_worker` → `worker_light`
- `browser_worker` → `worker_nova`
- `doc_worker` → `worker_reed`

同时保留显示名（中文/英文）在 roster 与 agent_configs.name 中：
- queen_sera.name = 蚁后_瑟拉
- king_tru.name = 蚁王_特鲁
- soldier_ares.name = 兵蚁_阿瑞
- worker_light.name = 工蚁_莱特
- worker_nova.name = 工蚁_诺瓦
- worker_reed.name = 工蚁_里德

## 需要同步修改的地方（必改）
### 1) 配置文件 key
- `configs/agent_configs.yaml`：把 6 个顶层 key 全部重命名为新 role_key
- `configs/prompts/system_prompts.yaml`：把 6 个顶层 key 同步重命名为新 role_key（内容里已经是新中文名，只需要改 key）

### 2) 运行与调度
- `services/runtime.py`：`iter_role_agents()` 的 role_key 列表改为新 role_key
- `services/workflow.py`：
  - `create_colony()` 创建/路由表中的 role_key 改为新 role_key
  - `dispatch` 中对 `browser_worker/doc_worker/...` 的分支，全部改为新 role_key
  - 清理/统一别名键（例如 `soldier_ant`），避免残留旧 key

### 3) agent 工厂创建时传入的 role_key
- `agents/queen/agent.py`：`role_key="queen"` → `"queen_sera"`
- `agents/king/agent.py`：`"king"` → `"king_tru"`
- `agents/soldier/agent.py`：`"soldier"` → `"soldier_ares"`
- `agents/worker/emotion_worker.py`：`"emotion_worker"` → `"worker_light"`
- `agents/worker/browser_worker.py`：`"browser_worker"` → `"worker_nova"`
- `agents/worker/doc_worker.py`：`"doc_worker"` → `"worker_reed"`

### 4) skills 目录与加载
- `services/skill_loader.py`：动态技能目录 `agents/skills/<role_key>/` 会跟着变；需要把原有目录迁移/重建到新 key 下（如有旧技能）。
- 现有 `load_skills(... role_key=...)` 的调用点全部改为新 key。

### 5) UI 角色顺序与角色编辑器
- `ui/data_center.py`：`role_order` 列表替换为新 role_key
- `ui/role_editor.py`：默认 role 顺序替换为新 role_key
- `ui/skill_tool_loader.py`：角色列表来自 `load_roles()`，只要上游配置 key 已改，这里主要确认不再写死旧 key

### 6) 记忆类型按 role_key 的特殊分支
- `chromaserver/service.py` 与 `memory/memory_manager.py`：把对 `queen/king` 的特殊分支改为 `queen_sera/king_tru`（否则 queen/king 规则失效或报错）。

### 7) roster 同步
- `agents/ant_roster.jsonl`：每条记录的 `role_key` 字段改为新 role_key

## 数据库/历史是否要清理
- **建议清理并重建**：因为 workspace_id/collection 元信息与旧 role_key/旧 agent_name 会产生割裂；你说“数据库可以重新清理后生成”——可以。
- 执行迁移后，如果要从零开始：清空 `chromaserver/data/chroma_vector_store` 与 `chromaserver/data/jsonl_storage`，再重新初始化。

## 验证
- 启动前：全仓 grep 确认旧 role_key 字符串完全为 0（queen/king/soldier/emotion_worker/browser_worker/doc_worker）。
- 运行时：启动 UI + chromaserver，确认角色列表能加载、心跳能跑、不会因缺 role_key 抛错。
- 数据：解析 `agents/ant_roster.jsonl` 确认 6 条 JSON 都合法且 role_key 全为新 key。

## 说明
- 你举的 `agent_configs.yaml#L57-59`（heartbeat 配置）不需要动；要动的是该段所在的**顶层 key 名**（比如 `browser_worker:` 这一行）。
- 我按“推荐的小写 role_key”执行；若你坚持把 role_key 也写成 `Worker_Nova/King_Tru` 这种驼峰+大写，我会把上面映射里的新 key 统一替换为你指定的大小写形式，再做同样的全仓迁移。