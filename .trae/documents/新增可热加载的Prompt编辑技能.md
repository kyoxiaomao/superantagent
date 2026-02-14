## 可行性说明
- 仅写入 `configs/prompts/system_prompts.yaml` 属于“持久化修改”，但当前仓库的 prompt 默认只在创建 Agent 时读取，不会自动生效。
- 为实现“热加载”，需要补一个极小的运行时能力：把“最新 prompt”写入磁盘后，再把运行中对应 Agent 的 `sys_prompt` 字段更新为新值（仅影响后续消息）。

## 目标
- 在 `d:/SuperAntAgent/utils/allskill/` 新增一个技能目录 `prompt_editor/`，提供工具函数让某个 agent 可以更新自己（或指定 role）的 system prompt。
- 支持两种效果：
  - 仅持久化（写入 YAML，重启后生效）
  - 可选热加载（运行中直接更新 agent.sys_prompt，立即对后续轮次生效）

## 代码改动点
### 1) 新增技能工件：utils/allskill/prompt_editor
- 新增文件：
  - `utils/allskill/prompt_editor/skill.md`
  - `utils/allskill/prompt_editor/skill.py`
- 工具函数（英文标识符，中文文档）：
  - `read_system_prompt(role_key: str) -> str`
  - `update_system_prompt(role_key: str, sys_prompt: str, apply_runtime: bool = True) -> dict`
  - `append_system_prompt(role_key: str, appendix: str, apply_runtime: bool = True) -> dict`
  - （可选）`update_system_prompt_by_agent_name(agent_name: str, sys_prompt: str, apply_runtime: bool = True) -> dict`：按显示名反查 role_key，若重名则直接报错。
- 写回逻辑复用既有配置层：`services.role_config_store.load_roles/save_roles`。

### 2) 新增运行时上下文：services/runtime_context.py
- 目的：让“技能工具函数”在进程内能拿到当前运行中的 `ColonyRuntime` 实例，从而做热加载。
- 提供 API：
  - `set_current_runtime(runtime: object | None) -> None`
  - `get_current_runtime() -> object | None`

### 3) 让 UI/终端运行时注册到 runtime_context
- 修改 `ui/async_bridge.py`：创建 `runtime = ColonyRuntime(...)` 后调用 `set_current_runtime(runtime)`；停止/退出时清空。
- 修改 `main.py`：创建并 `await runtime.start()` 后调用 `set_current_runtime(runtime)`；退出时清空。

### 4) 在 ColonyRuntime 增加“热应用 prompt”的方法
- 修改 `services/runtime.py`：新增方法（同步函数即可）
  - `apply_system_prompt(role_key: str) -> str`
- 行为：
  - 读取最新 `system_prompts.yaml`（复用 `load_roles` 或直接读 YAML），拿到该 role 的 base sys_prompt。
  - 定位运行中该 role 对应的 agent（复用 `iter_role_agents`）。
  - 将 agent 当前 `sys_prompt` 中的“长期记忆规则”附加段保留（以 `\n\n# 长期记忆使用规则\n` 为分隔），把 base prompt + 规则段重新拼接写回 `agent.sys_prompt`。
  - 若找不到 runtime / agent / role_key，直接抛错（严格模式）。

## 使用方式（实现完成后）
1) 在聊天 UI 的“工具库”页签把 `prompt_editor` 装配到某个角色（例如 `queen_sera`）。
2) 让该 agent 调用工具：
   - `update_system_prompt(role_key="queen_sera", sys_prompt="...", apply_runtime=True)`
   - 返回会包含 `persisted/applied` 等字段；热加载成功后，下一轮开始使用新 prompt。

## 验证
- 运行 `python -m ui.chat_app`：
  - 装配 `prompt_editor` 到某角色后调用更新函数。
  - 观察：配置文件内容已更新；且在不重启的情况下，后续回复行为使用新 prompt（通过在 prompt 中加入明显标识语句验证）。