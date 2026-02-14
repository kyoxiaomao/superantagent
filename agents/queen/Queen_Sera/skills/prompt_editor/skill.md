# Prompt 编辑器（编辑 system prompt）

该技能用于修改各角色的 system prompt（持久化写入 `configs/prompts/system_prompts.yaml`），并支持可选的“热加载”（将运行中的 Agent 立即切换到新 prompt，影响后续消息）。

## 工具列表

- read_system_prompt(role_key: str) -> str
- update_system_prompt(role_key: str, sys_prompt: str, apply_runtime: bool = True) -> dict
- append_system_prompt(role_key: str, appendix: str, apply_runtime: bool = True) -> dict
- update_system_prompt_by_agent_name(agent_name: str, sys_prompt: str, apply_runtime: bool = True) -> dict

## update_system_prompt 返回字段（核验）

当 `apply_runtime=True` 且运行时上下文存在时，返回值会附带“运行时 sys_prompt 核验信息”：

- ok：bool，运行时已热应用且包含本次传入的 sys_prompt 时为 True
- runtime_contains：bool，运行时 sys_prompt 是否包含本次传入 sys_prompt
- runtime_len：int，运行时 sys_prompt 长度（字符数）
- runtime_sha256：str，运行时 sys_prompt 的 SHA256
- runtime_sys_prompt：str，运行时 sys_prompt 的截断预览
- runtime_sys_prompt_truncated：bool，是否发生截断

## 使用说明

### 1) 持久化修改（重启后生效）

```text
update_system_prompt(role_key="queen_sera", sys_prompt="你是……", apply_runtime=False)
```

### 2) 热加载（推荐）

```text
update_system_prompt(role_key="queen_sera", sys_prompt="你是……", apply_runtime=True)
```

### 3) 热加载后核验（推荐）

```text
res = update_system_prompt(role_key="queen_sera", sys_prompt="你是……", apply_runtime=True)
res["ok"] == True
```

说明：

- 热加载仅对“后续轮次”生效；本次正在生成的回复不会被新 prompt 影响。
- 若当前进程里没有运行时上下文（例如未在 UI/终端主程序内运行），会返回 `applied=False`，此时需要重启运行时生效。

