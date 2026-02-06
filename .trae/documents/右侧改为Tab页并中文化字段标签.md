## 需求理解

* 右侧区域改为 Tab 页（ttk.Notebook）：

  * Tab1：系统提示词

  * Tab2：基础信息（把现有基础信息 + heartbeat 放到这里）

* 文案以中文为主，英文（原字段名）放在中文后面的括号里。

## 将要改动的文件 

* 仅修改 UI 文件：[role\_editor.py](file:///d:/antagent/ui/role_editor.py)

## 具体实现方案

1. **引入 Notebook 并拆分页面**

   * 在 right 容器内创建 `ttk.Notebook`，grid 到 `row=0, column=0, sticky="nsew"`。

   * 新建两个 Frame：

     * `tab_prompt`：放 `System Prompt` 文本区（Text + Scrollbar）

     * `tab_basic`：放“基础信息 + 心跳”两块内容

   * 将当前的 `basic`、`hb`、`prompt` 三个 LabelFrame：

     * `prompt` 移到 `tab_prompt`

     * `basic` 和 `hb` 移到 `tab_basic`

   * 调整 grid 权重：

     * `right.rowconfigure(0, weight=1)` + `right.columnconfigure(0, weight=1)`

     * `tab_prompt.rowconfigure(0, weight=1)`（确保文本区随窗口伸缩）

     * `tab_basic` 一般不需要 weight=1（除非你希望基础信息页也拉伸某块区域）

2. **文案中文化（中文 + 英文括号）**

   * 基础信息区：

     * `role_key` → `角色标识（role_key）`

     * `显示名` → `显示名（name）`

     * `max_iters` → `最大迭代（max_iters）`

   * 心跳区：

     * `Heartbeat` → `心跳（heartbeat）`

     * `启用` → `启用（enabled）`

     * `间隔(s)` → `间隔（interval_s，秒）`

     * `抖动(s)` → `抖动（jitter_s，秒）`

     * `空闲不递增(s)` → `空闲不递增（idle_no_increment_s，秒）`

     * `话题冷却(s)` → `话题冷却（topic_cooldown_s，秒）`

     * `话题活跃(s)` → `话题活跃（topic_active_s，秒）`

     * `话题决策最小间隔(s)` → `决策最小间隔（topic_decision_min_gap_s，秒）`

     * `话题轮转间隔(s)` → `轮转间隔（topic_turn_interval_s，秒）`

     * `历史窗口(n)` → `历史窗口（history_window_n）`

   * Tab 名称：

     * Tab1：`系统提示词（system prompt）`

     * Tab2：`基础信息（config）`

3. **交互与现有逻辑保持不变**

   * 不改读取/保存/校验逻辑；仍旧编辑同一份内存 `roles`。

   * 继续保留“未保存”提示与 `<<Modified>>` 事件；Notebook 不会影响 `_apply_form_to_role` / `_load_role_to_form`。

## 验证方式

* 编译检查：`python -m compileall -q ui\role_editor.py`

* 手动启动：`python -m ui.role_editor`

  * 默认能看到两个 Tab

  * 切换角色时，两个 Tab 内容都能正确刷新

  * 窗口拉伸时，系统提示词页的文本框能随高度扩展

确认后我就按上面方案直接改代码并启动验证。
