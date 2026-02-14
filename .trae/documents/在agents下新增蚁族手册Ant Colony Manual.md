## 目标
- 在 `d:/SuperAntAgent/agents/` 下新增 Markdown 文档：`Ant Colony Manual.md`，作为“蚁族手册”，用于说明本项目蚁族角色、目录结构、配置与运行方式。

## 文档内容大纲（将写入）
- 概览：系统是什么、核心技术栈、基本运行形态（UI/终端/向量库服务）。
- 蚁族名册（Roster）：基于 [ant_roster.jsonl](file:///d:/SuperAntAgent/agents/ant_roster.jsonl) 输出表格（id / role_key / 中文名 / 英文名 / tags / skills/tools）。
- 角色职责与交互流：引用 [runtime.py](file:///d:/SuperAntAgent/services/runtime.py) 与 [README.md](file:///d:/SuperAntAgent/README.md) 的既有约定（用户输入直达蚁后，蚁王周期 update 群聊调度等）。
- 目录结构说明：`agents/base`、各角色目录（queen/king/soldier/worker）及 `skills/`、`tools/` 的用途。
- Prompt/配置管理：
  - 配置文件位置：`configs/agent_configs.yaml`、`configs/prompts/system_prompts.yaml`。
  - 可视化编辑入口：`python -m ui.role_editor`（并说明保存后需重启运行时生效）。
  - system prompt 注入点：`agents/base/ant_agent_base.py` 的 `load_agent_config/create_react_ant_agent`。
- 运行与排障速查：从 README 提炼常用启动命令与常见问题（不引入新依赖/新流程）。
- 开发规范摘要：遵循仓库规则（严格模式、不做兜底、代码标识符尽量英文、嵌入模型只用 qwen3-vl-embedding）。

## 实施步骤（将执行）
- 在 `agents/` 创建 `Ant Colony Manual.md`。
- 将上述内容按章节写入，并把关键源码位置用链接引用到仓库文件（便于跳转）。
- 做一次快速自检：确认链接路径正确、Markdown 渲染正常、信息与当前配置一致（尤其是 role_key=queen_sera 等）。