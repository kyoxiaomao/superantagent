## 总体结论（先回答你的问题）
- 是的：为了保持一致的架构边界，UI 需要展示/操作的“工具库数据、角色全量属性、装配状态”应由 DataCenter 统一提供。
- 做法是：把“角色全属性包装 + 技能/工具目录扫描 + 装配/卸载文件操作 + 搜索过滤”集中到 DataCenter；ChatDialog 只负责渲染与触发动作。

## 目标功能（对齐截图/描述）
- 在聊天 UI 新增“蚁族工具库”页签，并在启动时默认选中该页。
- 顶部控件：模式切换（工具库/角色）、角色下拉（选择目标角色）、类型下拉（全部/技能/工具）、搜索框。
- 卡片网格：
  - 工具库模式：展示全部技能+工具卡片；卡片按钮为“装配”，装配到当前选择角色。
  - 角色模式：展示当前角色已装配的技能+工具卡片；卡片按钮为“卸载”。
- 搜索：按技能/工具的 key、title、markdown 内容关键字过滤。

## 核心架构调整：增强 DataCenter（角色全属性 + 工具库能力）
### 1) DataCenter 统一输出“UI 需要的角色全属性”
- 在 [data_center.py](file:///d:/SuperAntAgent/ui/data_center.py) 增加数据结构（英文标识符）：
  - `RoleProfile`：`role_key/name/max_iters/heartbeat/sys_prompt/tags/skills/tools` 等。
  - `CatalogCard`：`kind(skill|tool)/key/title/summary/interfaces_or_steps_count/is_installed` 等。
  - `ToolLibrarySnapshot`：包含 `roles(list)`、`selected_role_key`、`cards(list)`、`mode/type_filter/query`。
- DataCenter.start() 时加载：
  - 角色配置与 system prompt：复用 [role_config_store.load_roles](file:///d:/SuperAntAgent/services/role_config_store.py#L25-L63)（满足“角色全属性包装”）。
  - roster tags：解析 [ant_roster.jsonl](file:///d:/SuperAntAgent/agents/ant_roster.jsonl)。
  - 全量工件目录：复用 [skill_tool_catalog.load_catalog](file:///d:/SuperAntAgent/utils/skill_tool_catalog.py#L62-L67)（skills/tools 的 key/title/doc_markdown 等）。
  - 角色已装配技能/工具：复用 [agent_home_locator.get_agent_skill_dir/get_agent_tool_dir](file:///d:/SuperAntAgent/utils/agent_home_locator.py#L40-L47) + `os.listdir`。

### 2) DataCenter 提供“查询与动作”接口（UI 不直读文件系统）
- 新增方法（示例命名）：
  - `get_role_profiles() -> list[RoleProfile]`
  - `get_tool_library_snapshot(mode, role_key, type_filter, query) -> ToolLibrarySnapshot`
  - `install_skill(role_key, skill_key)` / `uninstall_skill(role_key, skill_key)`
  - `install_tool(role_key, tool_key)` / `uninstall_tool(role_key, tool_key)`
- 装配/卸载实现复用现有逻辑（不重复造轮子）：
  - 装配：`shutil.copytree(artifact_dir, dest_dir)`（参考 [skill_tool_loader.py](file:///d:/SuperAntAgent/ui/skill_tool_loader.py#L326-L452)）
  - 卸载：`shutil.rmtree(dest_dir)`
- 严格模式：不吞异常；失败直接抛出（必要时 UI 仅把异常文本显示到系统栏后再抛出，不会“吃掉”）。

## UI 实现：在 ChatDialog 内增加工具库页签（默认选中）
### 1) 新增面板组件（建议新增文件）
- 新建：`ui/tool_library_panel.py`
- 面板只做 UI：
  - 维护控件状态（mode/role/type/query），每次变更就调用 `data_center.get_tool_library_snapshot(...)` 刷新。
  - 使用 `Canvas + Frame + Scrollbar` 实现可滚动卡片网格；窗口 resize 时重新计算列数。
  - 卡片按钮点击：调用 DataCenter 的 install/uninstall，再刷新快照。

### 2) ChatDialog 集成
- 修改 [chat_dialog.py](file:///d:/SuperAntAgent/ui/chat_dialog.py)：
  - `ChatDialog.__init__` 增加参数 `data_center: DataCenter`（让“UI 数据都从 DataCenter 拿”落地）。
  - Notebook 增加第三个 tab：“工具库”，并把 `ToolLibraryPanel` 放进去。
  - 默认选中工具库 tab（启动即展示）。

### 3) chat_app.py 对接
- 修改 [chat_app.py](file:///d:/SuperAntAgent/ui/chat_app.py)：
  - 创建 `ChatDialog(..., queen_name=data_center.queen_name, data_center=data_center)`。
  - 其余聊天事件泵逻辑保持不变。

## 交互细节（按截图）
- 默认模式：工具库；默认角色：queen_sera。
- 类型下拉：全部/技能/工具。
- 角色模式切换后：卡片集合切为“该角色已装配”；按钮文案切为“卸载”。
- 搜索框：支持模糊包含匹配（key/title/markdown）。

## 验证
- 运行 `python -m ui.chat_app`：
  - 启动默认落在“工具库”页签。
  - 切换角色/模式/类型/搜索，卡片刷新正确。
  - 点击装配/卸载后，对应 `agents/**/skills` 与 `agents/**/tools` 目录变化符合预期，UI 立即反映装配状态。