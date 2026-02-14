## 你的要求（纳入本次方案）
- 旧的 `agents/skills` 共享机制彻底删除。
- 只保留“每个 agent 自己的个人目录 skills/tools”。
- 新增的 agent_home 定位逻辑与任何“工具相关的辅助文件”都放在 `utils/` 下（不放 services/）。

## 统一落盘规范（简洁、无兼容包袱）
- Skill：`<agent_home>/skills/<skill_key>/skill.py + skill.md`
- Tool：`<agent_home>/tools/<tool_key>/tool.json + tool.md`
- 兵蚁生成脚本也写到同一结构：`file_name=xxx.py` → `<agent_home>/skills/xxx/skill.py`。

## 实施步骤
### 1) 新增 utils 下的 agent_home 定位器（新增 1 个文件）
- 新增 `utils/agent_home_locator.py`
  - 扫描 `agents/**/agent.py`，用正则匹配其中 `role_key="..."`，找到与传入 role_key 相同的那个文件，其目录即 `<agent_home>`。
  - 提供：
    - `find_agent_home(repo_root, role_key) -> str`
    - `get_agent_skill_dir(repo_root, role_key) -> str`
    - `get_agent_tool_dir(repo_root, role_key) -> str`

### 2) 重写技能加载/写入逻辑（修改 1 个文件）
- 修改 `services/skill_loader.py`
  - 用 `utils/agent_home_locator.py` 定位 `<agent_home>/skills`，删除所有 `agents/skills/<role_key>` 相关逻辑。
  - `load_skills()`：只加载 `<agent_home>/skills/*/skill.py`（调用其中的 `register(toolkit)`）。
  - `safe_write_skill_file/doc()`：把 `xxx.py|xxx.md` 规范化落到 `<agent_home>/skills/xxx/skill.py|skill.md`。
  - `list_skill_artifacts()`：只列出目录式技能（`<skill_key>/skill.py|skill.md`）。

### 3) 改造技能/工具配置页（修改 1 个文件）
- 修改 `ui/skill_tool_loader.py`
  - 技能 tab：
    - 已分配技能集合改为读取 `<agent_home>/skills` 子目录名。
    - “添加技能”copytree：`utils/allskill/<skill_key>/` → `<agent_home>/skills/<skill_key>/`。
    - “移除技能”删除 `<agent_home>/skills/<skill_key>/`。
  - 工具 tab：补齐与技能 tab 对称的“分配/移除”：
    - 已分配工具读取 `<agent_home>/tools` 子目录名。
    - “添加工具”copytree：`utils/alltool/<tool_key>/` → `<agent_home>/tools/<tool_key>/`。
    - “移除工具”删除 `<agent_home>/tools/<tool_key>/`。

### 4) 迁移现有共享技能并删除 agents/skills（迁移 + 删除）
- 把现有 `agents/skills/worker_nova/doc_io.py|md` 迁移为：
  - `agents/worker/Worker_Nova/skills/doc_io/skill.py|skill.md`
- 删除整个 `agents/skills/` 目录（包含 `__init__.py` 与 `__pycache__`）。

### 5) 清理启动清理逻辑（修改 1 个文件）
- 修改 `services/startup_cleanup.py`
  - 删除“清理 agents/skills 下产物”的逻辑。
  - 改为：扫描所有 `agents/**/skills/*` 与 `agents/**/tools/*`，删除其中的工件目录（保留 `.gitkeep`）。

## 验证
- 导入验证：在虚拟环境下 import `services.skill_loader`、`agents.worker`、`services.workflow`。
- 行为验证：在 UI 分配一个 skill/tool 后，确认落盘到对应 agent 的个人目录；重启后 해당 agent 仅从自己的 skills 加载。
