## 目标
- 修复 Terminal 报错 `KeyError: 'queen'`（UI 启动即崩溃）。
- 按“严格模式”改造：不做吞异常兜底；把涉及的中文标识符（变量/函数/方法/属性名）统一改为英文。
- 让“蚁后显示名”不再在 UI 中硬编码，改为从静态配置映射读取，避免后续改名再炸。

## 事实依据（已核对）
- [ant_roster.jsonl](file:///d:/SuperAntAgent/agents/ant_roster.jsonl) 蚁后条目 `role_key` 为 `queen_sera`。
- [agent_configs.yaml](file:///d:/SuperAntAgent/configs/agent_configs.yaml) 的角色键同样为 `queen_sera`，不存在 `queen`。
- 当前崩溃点在 [data_center.py](file:///d:/SuperAntAgent/ui/data_center.py#L82-L93) 的 `self.role_to_name["queen"]`。
- [chat_dialog.py](file:///d:/SuperAntAgent/ui/chat_dialog.py) 多处硬编码 `"蚁后_瑟拉"`，与“可改名”的目标冲突。

## 将要修改的文件与改动
### 1) 修复 KeyError（不兜底）
- 在 [data_center.py](file:///d:/SuperAntAgent/ui/data_center.py) 把 `self.role_to_name["queen"]` 改为 `self.role_to_name["queen_sera"]`，与配置一致；不存在则直接抛错，不做兜底逻辑。
- 同时把 `self.蚁后名` 改为 `self.queen_name`，并同步替换所有引用点。

### 2) UI 解除蚁后名字硬编码（严格依赖 DataCenter 静态映射）
- 在 [chat_app.py](file:///d:/SuperAntAgent/ui/chat_app.py) 启动后从 `data_center.role_to_name["queen_sera"]` 取 `queen_name`，并将其传入对话框组件。
- 在 [chat_dialog.py](file:///d:/SuperAntAgent/ui/chat_dialog.py) 把所有 `"蚁后_瑟拉"` 的硬编码改为基于 `self.queen_name` 的动态键（例如 `agent:{self.queen_name}`），保证改名后 UI 不需要改代码。

### 3) 英文化中文标识符（仅改“代码名”，不改中文文案）
- [chat_app.py](file:///d:/SuperAntAgent/ui/chat_app.py)：把 `_环境变量真值/_停止打点启用/_格式化时间戳/_打印停止打点/_清理蚁后聊天记录`、`资源根目录/项目根目录/关闭打点起点` 等全部改为英文 snake_case，并同步所有调用处。
- [data_center.py](file:///d:/SuperAntAgent/ui/data_center.py)：把 `蚁后名` 改为 `queen_name`。
- [chat_dialog.py](file:///d:/SuperAntAgent/ui/chat_dialog.py)：把 `系统信息变量/标签变量/标签下拉/标签选项/_回复流式激活/_回复流式全文/更新系统消息` 等属性与方法名改为英文（例如 `system_info_var/update_system_message/tag_var/...`），并同步 [chat_app.py](file:///d:/SuperAntAgent/ui/chat_app.py) 的调用。

### 4) 移除“吞异常兜底”代码（严格模式）
- [chat_app.py](file:///d:/SuperAntAgent/ui/chat_app.py)：
  - 删除 `_清理...` 中 `try/except: pass`，文件删除失败直接抛错。
  - 删除 `pump_events()` 的大而全 `try/except`（当前会吞渲染异常并继续跑），让问题直接暴露。
- [data_center.py](file:///d:/SuperAntAgent/ui/data_center.py)：
  - 删除 `_emit()` 对 subscriber 的 `try/except: pass`，subscriber 抛错直接暴露。
  - 删除 `_load_history_events()` 的 `try/except: pass`（历史加载出错直接抛错）。

## 验证（我将在变更后执行）
- 运行 `python -m ui.chat_app`：确认不再出现 `KeyError: 'queen'`，并且 UI 能启动。
- 做一次最小交互：发送一条消息，确认群聊/蚁后视图能正常追加消息（依赖新的 `queen_name` 动态键）。

## 影响说明
- 仅重构 UI 层与数据中心的命名/异常处理策略，不改配置文件内容。
- “严格模式”会让原先被吞掉的异常直接暴露；如果因此暴露出新的真实错误，我会继续跟进修复到可运行状态。