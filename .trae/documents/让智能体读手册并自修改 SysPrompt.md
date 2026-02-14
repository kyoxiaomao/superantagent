## 建议结论：优先“按角色（role_key）配置技能库”
- 你的系统配置（agent_configs.yaml、system_prompts.yaml）天然是按 role_key 管理；技能也按 role_key 管理最一致、最好排障。
- “按单个 agent（实例）配置”只有在同一 role 同时跑多个实例、或要做差异化实验时才值得引入；否则成本高。
- 可选增强：保留一个本地覆盖层（tools_local 或 overrides.yaml），只给少数实例做临时差异化。

## 关于你提出的“所有 agent 重命名 + 等级/经验”
- 我建议把等级/经验先作为**显示名的一部分（最小改动）**：直接改 agent_configs.yaml 的 name。
- 以后若要自动累积经验/升级，再把 level/exp 变成结构化字段（避免频繁改 YAML + 解析字符串）。

## 英文命名规则（推荐统一格式）
- 显示名格式：
  - <Role>_<Codename> Lv<level> EXP<exp>
- 你给的例子翻译：
  - 蚁后_瑟拉，等级lv1，经验:0 → Queen_Sera Lv1 EXP0
  - 蚁王_特鲁，等级lv1，经验:0 → King_Tru Lv1 EXP0
  - 工蚁_莱特，等级lv1，经验:0 → Worker_Light Lv1 EXP0

## 在当前多工蚁角色下的落地建议
- 目前有 3 个工蚁 role：emotion_worker / browser_worker / doc_worker。
- 为了 UI 不混淆，建议每个 role 都有独立前缀：
  - EmotionWorker_<Codename> Lv1 EXP0
  - BrowserWorker_<Codename> Lv1 EXP0
  - DocWorker_<Codename> Lv1 EXP0
- 你已明确“莱特 Light”给工蚁，但未给另外两只工蚁的代号；本次实现可先用占位名（例如 EmotionWorker_Light / BrowserWorker_Light / DocWorker_Light），后续你再在 agent_configs.yaml 或角色编辑器里改成各自的专属代号。

## 技能体系改造（与你当前约定对齐）
### 1) 全局技能库（不变）
- utils/allskill/**/skill.md + skill.py 作为“可选技能目录”。
- 读/写 MD：直接复用已有 doc_io skill（read_text_doc/write_text_doc/append_text_doc）。

### 2) 角色技能库（按 role_key）
- 每个角色只加载：agents/<role_key>/tools/*.py（你要求的“蚂蚁的 Toolkit 只配置当前文件夹技能”）。

### 3) UI 人工分配时的复制落点
- 修改 ui/skill_tool_loader.py：把 utils/allskill 中选中的 skill 复制到 agents/<role_key>/tools/（
  - 复制命名仍为 <skill_key>.py / <skill_key>.md）。

### 4) 运行时 Toolkit 注册时机（关键点）
- 在创建 ReActAgent 之前完成注册：
  - create_react_ant_agent 内部：tk = toolkit or Toolkit() → load_tools(tk, role_key) → ReActAgent(... toolkit=tk ...)

### 5) 新增全局 skill：sys_prompt_editor
- 放在 utils/allskill/sys_prompt_editor/skill.py + skill.md。
- 提供 update_sys_prompt(role_key, new_prompt)：内部调用 services.role_config_store.load_roles/save_roles 更新 configs/prompts/system_prompts.yaml。
- 这个 skill 由人工分配到 queen（复制到 agents/queen/tools/）后，queen 才能改自己的 sysprompt。

## 热重载（你问的“怎么做”落地）
- 工具热重载：给每个角色注册 reload_tools()（重新 load agents/<role_key>/tools）。
- sys_prompt 软热重载：在 create_react_ant_agent 为所有角色（或至少 queen）注册 reload_self_sys_prompt()：重新读 YAML 并更新当前 agent.sys_prompt。
- 如果发现 agentscope 对 sys_prompt 存在内部缓存，再做第二阶段“硬热重载”（重建 colony）。

## 交付清单
- agents/antcolonymanual.md（基础手册，供 doc_io 读取）
- utils/allskill/sys_prompt_editor（新技能）
- ui/skill_tool_loader.py：复制目标迁移到 agents/<role_key>/tools/
- 新/改 loader：从 agents/<role_key>/tools 加载并注册工具
- configs/agent_configs.yaml：更新所有角色 name 为英文名+Lv+EXP 初始值