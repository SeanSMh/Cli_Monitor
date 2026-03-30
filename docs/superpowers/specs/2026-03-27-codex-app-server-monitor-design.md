# Codex 监控优化技术方案

**日期:** 2026-03-27  
**状态:** 可实施版草案 v2  
**目标:** 用 Codex app-server 结构化事件替换 TUI 日志文本启发式解析，实现精准、低抖动、可扩展的状态检测

---

## 一、背景与结论

`cli-monitor` 当前通过 `script -a -F -q` 捕获 Codex TUI 终端输出，再用正则表达式解析 ANSI 文本推断状态。这条路径天然脆弱，因为它解析的是给人看的界面，而不是给程序消费的协议。

Codex 已提供 app-server 协议，且在 WebSocket 模式下会输出权威状态：

```text
ThreadStatus = NotLoaded | Idle | SystemError | Active { activeFlags }
ThreadActiveFlag = WaitingOnApproval | WaitingOnUserInput
```

因此，Codex 的监控主路径应切换为：

- **主路径:** app-server 结构化事件
- **降级路径:** 旧日志文本解析

但要注意两个关键事实：

1. app-server 的 WebSocket 客户端不是“连上就能收事件”，每个连接都必须先执行 JSON-RPC `initialize` / `initialized` 握手。
2. thread/turn/item 事件是**连接感知**的，不能假设“再开一个 observer 连接就一定能旁听到 TUI 那个连接的全部事件”。

基于这两个约束，本方案不采用“旁路 observer 直接被动旁听”作为主设计，而采用：

- **推荐实现:** `Codex app-server proxy + monitord`

即：

- TUI 不再直连 app-server，而是连接本地 proxy
- proxy 作为 app-server 的正式客户端连接真实 app-server
- proxy 将结构化事件同时转发给 TUI 和 monitor daemon
- monitor daemon 维护跨进程共享状态，供终端面板和 macOS 面板读取

这条路径不依赖未被协议保证的“多连接旁听”能力，可实施性更高。

---

## 二、现有问题

### 2.1 根本问题

当前 Codex 监控路径：

```text
codex TUI
  └── script 捕获 TTY
        └── monitor.py 读日志尾部
              └── 正则 + 启发式推断状态
```

固有缺陷如下：

| 问题 | 表现 |
|------|------|
| ANSI/VT100 污染 | ratatui 全屏重绘、光标移动、颜色码混入文本 |
| 帧重复 | 同一状态行可在短时间内写入多次 |
| 延迟 | 状态变化取决于日志刷新时机 |
| 不完备 | 新 UI 文案、新弹窗、新交互一出现就要补正则 |
| 语义漂移 | monitor.py 和 panel_app.py 各自做 hold / fallback，容易不一致 |

### 2.2 已知 Bug

**Bug A：`item.completed` 被错误映射为 IDLE**

当前兼容解析中，`"completed"` 被泛化为 idle token，导致 `item.completed` 可能误触发 IDLE。  
但 `item.completed` 只表示某个 item 完成，turn 可能仍在继续。真正的 turn 结束应以 `turn.completed` 或 `thread/status/changed -> idle` 为准。

**Bug B：MCP elicitation 审批弹窗未被 WAITING 检测**

当前文本规则未覆盖 `"<server_name> needs your approval."` 这一类 MCP 审批标题。

### 2.3 架构问题

当前设计文档 v1 还有两个架构级问题需要修正：

1. **Store 被描述成“线程安全”**  
   实际上 `monitor.py` 与 `panel_app.py` 是不同进程，单纯线程锁无法共享状态；这里需要的是**进程间状态服务**，不是进程内锁。

2. **假设 observer 连接可直接旁听 TUI 会话**  
   该假设没有被 app-server 协议明确保证。协议只明确说明：
   - 每个连接都要初始化握手
   - `thread/start` / `thread/fork` 会自动给该连接订阅 turn/item 事件
   - `thread/unsubscribe` 是按“当前连接”取消订阅

因此，多连接事件可见性不能想当然。

---

## 三、设计目标

### 3.1 必达目标

1. Codex 的状态以 app-server 结构化事件为主，不再以 TUI 文本为主。
2. `monitor.py` 和 `panel_app.py` 读取同一份状态源，避免状态漂移。
3. 可精确区分：
   - `RUNNING`
   - `WAITING_APPROVAL`
   - `WAITING_INPUT`
   - `IDLE`
   - `ERROR`
4. 其他工具（Claude、Gradle、Maven、Gemini）保持现状，不受本次重构影响。

### 3.2 非目标

1. 不修改 Codex 源码。
2. 不要求用户改变 `codex` 的使用习惯。
3. 不在第一版中重构所有工具的监控数据源。

---

## 四、协议事实与约束

### 4.1 WebSocket 是实验接口

Codex app-server 的 WebSocket 监听在当前版本中标记为 experimental / unsupported。  
这不影响本地实验性接入，但必须在设计里明确写成风险项，而不是默认稳定接口。

### 4.2 每个连接必须初始化握手

WebSocket 客户端连上后，必须：

1. 发送 `initialize`
2. 收到 initialize response
3. 发送 `initialized`

否则连接不能正常收发业务 RPC。

### 4.3 事件是连接感知的

协议明确了：

- `thread/start` 会自动让**当前连接**订阅 turn/item 事件
- `thread/unsubscribe` 取消的是**当前连接**的订阅

因此不能把“第二个连接天然收到第一个连接的 thread/turn/item 事件”写成设计前提。

### 4.4 可依赖的状态信号

Codex app-server 中，最可靠的状态相关通知是：

| 通知 | 说明 |
|------|------|
| `thread/started` | 初始 thread 引入事件，payload 含当前 `thread.status` |
| `thread/status/changed` | loaded thread 的状态变更，权威状态源 |
| `turn/started` | turn 开始，可用于更快落 `RUNNING` |
| `turn/completed` | turn 结束，可作为 `IDLE` 备用信号 |

其中 `thread/status/changed` 应为最高优先级。

---

## 五、推荐架构

### 5.1 总体架构

```text
┌──────────────────────────────────────────────┐
│ 用户执行 `codex`                              │
└──────────────────────┬───────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────┐
│ shell/codex_launcher.sh                      │
│ - 启动真实 codex app-server                  │
│ - 启动 codex app-server proxy                │
│ - 启动 monitord（若未运行）                  │
│ - 启动前台 codex --remote ws://proxy         │
└──────────────────────┬───────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────┐
│ proxy/codex_app_server_proxy.py              │
│ - 作为 TUI 的远端端点                         │
│ - 作为真实 app-server 的正式客户端            │
│ - 执行 initialize / initialized 握手          │
│ - 转发 JSON-RPC 请求/响应/通知                │
│ - 将结构化通知镜像给 monitord                 │
└──────────────────────┬───────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────┐
│ daemon/monitord.py                           │
│ - Codex / Claude / Generic sources 统一入口   │
│ - 维护内存快照                               │
│ - 提供本地 IPC 读取接口                       │
└──────────────┬──────────────────────┬────────┘
               │                      │
               ▼                      ▼
         monitor.py              panel_app.py
```

### 5.2 为什么选 proxy，而不是旁路 observer

proxy 方案的优点：

1. **不依赖多连接事件可见性假设**
2. **能拿到 TUI 实际收到的完整通知流**
3. **天然拿到 threadId / requestId / session 关联关系**
4. **monitor 只消费 mirror 事件，不需要自己驱动 thread 生命周期**

这是当前最稳的实现路径。

---

## 六、核心组件

### 6.1 `daemon/monitord.py`

`monitord` 是整个系统的唯一状态写入者，也是跨进程单一事实源。

职责：

- 接收 Codex proxy 镜像来的结构化事件
- 接收 Claude hook 信号
- 接收通用日志分析结果
- 执行统一状态机归约
- 通过 IPC 向 `monitor.py` / `panel_app.py` 暴露快照

不再让 `monitor.py` 与 `panel_app.py` 各自维护语义 hold 或独立判定逻辑。

### 6.2 `engine/models.py`

定义统一数据结构：

```python
TaskState
- session_id
- tool_name
- status                # RUNNING / WAITING_APPROVAL / WAITING_INPUT / IDLE / ERROR / DONE
- message
- thread_id
- source                # codex_proxy / claude_hook / log_text / ...
- updated_at_ms

MonitorEvent
- source
- session_id
- tool_name
- event_type
- payload
- ts_ms
```

### 6.3 `engine/reducer.py`

唯一状态机。  
所有来源的事件都先转成 `MonitorEvent`，再归约为 `TaskState`。

优先级：

```text
codex structured
  > claude hook
  > semantic text
  > plain text
```

这样可以删除现有 monitor 层和 panel 层的双重 hold 逻辑。

### 6.4 `engine/store.py`

这里只是 `monitord` 进程内存中的 store。  
不再把它描述成跨进程共享对象。

跨进程访问方式由 IPC 提供：

- Unix domain socket
- 或 localhost HTTP `/state`

推荐先做 localhost HTTP，只读接口，便于 terminal panel 和 menubar app 复用。

### 6.5 `registry/session_registry.py`

记录 session 元数据到 `~/.cli-monitor/sessions/<session_id>.json`。

建议字段：

```json
{
  "session_id": "codex_...",
  "tool": "codex",
  "log_file": "/tmp/ai_monitor_logs/....log",
  "real_app_server_url": "ws://127.0.0.1:43127",
  "proxy_url": "ws://127.0.0.1:43128",
  "app_server_pid": 12345,
  "proxy_pid": 12346,
  "tui_pid": 12347,
  "thread_id": "thr_123",
  "started_at": "2026-03-27T12:34:56Z",
  "state_source": "codex_proxy"
}
```

其中 `thread_id` 可在 proxy 看到 `thread/started` 或相关 response 后回填。

### 6.6 `proxy/codex_app_server_proxy.py`

这是本次重构的关键组件。

职责：

1. 监听本地 `ws://127.0.0.1:PROXY_PORT`
2. 接受 TUI 的连接
3. 与真实 app-server 建立上游连接
4. 转发：
   - request
   - response
   - notification
5. 将上游 notification 镜像给 `monitord`

proxy 不需要理解所有业务 RPC，只需：

- 正确转发 JSON-RPC
- 识别少量关键通知并做镜像
- 维护 `session_id <-> thread_id`

### 6.7 `shell/codex_launcher.sh`

`alias codex=` 不再直接走通用 `ai_wrapper codex`，而是走专用 launcher。

职责：

1. 生成 `session_id`
2. 创建日志文件并写入 `MONITOR_START`
3. 选择两个空闲端口：
   - `REAL_PORT`
   - `PROXY_PORT`
4. 启动真实 app-server：

```bash
codex app-server --listen ws://127.0.0.1:${REAL_PORT}
```

5. 启动 `monitord`（若未运行）
6. 启动 proxy，连接到真实 app-server
7. 启动前台 TUI：

```bash
codex --remote ws://127.0.0.1:${PROXY_PORT}
```

8. 退出时清理：
   - app-server
   - proxy
   - session registry
   - 日志尾部 `MONITOR_END`

---

## 七、Codex 状态映射

### 7.1 权威映射

| 事件 | 状态 |
|------|------|
| `thread/started.thread.status.type == "active"` 且 `activeFlags == []` | `RUNNING` |
| `thread/started.thread.status.type == "active"` 且含 `waitingOnApproval` | `WAITING_APPROVAL` |
| `thread/started.thread.status.type == "active"` 且含 `waitingOnUserInput` | `WAITING_INPUT` |
| `thread/status/changed -> idle` | `IDLE` |
| `thread/status/changed -> systemError` | `ERROR` |
| `thread/status/changed -> active + waitingOnApproval` | `WAITING_APPROVAL` |
| `thread/status/changed -> active + waitingOnUserInput` | `WAITING_INPUT` |
| `thread/status/changed -> active + []` | `RUNNING` |
| `turn/started` | `RUNNING` |
| `turn/completed` | `IDLE` 备用 |

### 7.2 说明

1. `thread/status/changed` 优先级高于 `turn/*`
2. `turn/completed` 只是备用，因为最终权威 thread 状态可能稍后到达
3. `item/completed` 绝不能直接映射为 `IDLE`
4. `item/started` / `item/completed` 只用于补充 message，不用于结束 turn

---

## 八、IPC 设计

### 8.1 推荐接口

`monitord` 提供只读 localhost HTTP：

- `GET /state`
- `GET /state?tool=codex`
- `GET /session/<session_id>`
- `GET /healthz`

返回示例：

```json
{
  "tasks": [
    {
      "session_id": "codex_1711531000_12345_999",
      "tool": "codex",
      "status": "WAITING_APPROVAL",
      "message": "Would you like to run the following command?",
      "thread_id": "thr_123",
      "source": "codex_proxy",
      "updated_at_ms": 1774600000123,
      "log_file": "/tmp/ai_monitor_logs/codex_....log"
    }
  ]
}
```

### 8.2 为什么不用共享内存文件直接读写

可以做，但第一版不推荐。原因：

- 需要处理并发写
- UI 端需要轮询文件
- 崩溃恢复与局部更新复杂

HTTP/UDS 服务更简单，也更利于未来扩展。

---

## 九、回退策略

### 9.1 Codex 回退

`monitor.py` 在读取某个 codex 任务时：

1. 先按 `session_id` 查询 `monitord`
2. 如果存在结构化状态，直接使用
3. 如果不存在：
   - 说明是旧版会话
   - 或 proxy/daemon 启动失败
   - 或用户绕过 launcher 直接运行 `codex`
4. 此时自动降级到旧日志解析

### 9.2 其他工具

Claude / Gradle / Maven / Gemini 继续走原有路径，不受影响。

---

## 十、兼容性与风险

| 风险 | 说明 | 缓解 |
|------|------|------|
| WebSocket 接口实验性 | app-server README 标记 experimental / unsupported | 保留日志降级路径；实现时封装在独立 source/proxy 层 |
| proxy 增加一层复杂度 | JSON-RPC 转发、错误恢复、进程清理都要做 | 先做最小代理，只支持单 TUI 客户端 |
| 握手细节遗漏 | initialize / initialized 任一步漏掉都无法稳定工作 | 在 proxy 中把握手做成显式状态机 |
| 进程生命周期复杂 | app-server / proxy / TUI / monitord 的退出顺序要管好 | launcher 用 `trap EXIT` 统一清理 |
| 用户绕过 launcher | 直接运行 `codex` 时不会有 structured source | 自动降级为日志解析 |

---

## 十一、实施计划

### Phase 0：修补当前文档与兼容层

1. 修复 Bug A：`item.completed` 不再触发 IDLE
2. 修复 Bug B：补充 MCP approval 文本规则
3. 在文档里明确：
   - WebSocket 握手要求
   - observer 假设无效
   - 需要跨进程状态服务

### Phase 1：建立统一状态服务

1. 新增 `monitord`
2. 抽出 `engine/models.py`
3. 抽出 `engine/reducer.py`
4. `monitor.py` / `panel_app.py` 改为读 `monitord`

此阶段不改变 Codex 的数据源，只先统一状态出口。

### Phase 2：接入 Codex proxy

1. 新增 `codex_launcher.sh`
2. 新增 `codex_app_server_proxy.py`
3. `alias codex=` 指向 launcher
4. Codex structured 状态写入 `monitord`

### Phase 3：收缩文本解析职责

1. Codex 文本解析降为 fallback
2. 删除 panel 层重复 semantic hold 的判定权
3. 只保留 message 提取与 UI 展示用途

---

## 十二、实现边界

### 12.1 第一版必须做到

1. Codex 通过 structured source 正确显示：
   - Running
   - Waiting on approval
   - Waiting on input
   - Idle
2. `monitor.py` 和 `panel_app.py` 同步一致
3. proxy / app-server / TUI 的启动和清理可靠

### 12.2 第一版可以暂缓

1. 非 Codex 工具统一迁移到 daemon source
2. 多客户端同时连同一个 proxy
3. 复杂鉴权模式下的远程 websocket 访问

---

## 十三、最小可行实现建议

若希望尽快验证技术路线，建议按以下最小切片落地：

1. 先实现 `monitord` + HTTP `/state`
2. 再实现单会话 `codex_app_server_proxy.py`
3. 只镜像并消费这些通知：
   - `thread/started`
   - `thread/status/changed`
   - `turn/started`
   - `turn/completed`
4. 只支持一个 TUI 客户端
5. 其余消息透明转发

这版足以验证核心价值，同时避免一次性把所有协议面铺太大。

---

## 十四、附录：与旧版方案的差异

| 旧版方案 | 新版方案 |
|----------|----------|
| Sidecar observer 直连 app-server | Proxy 作为正式中介连接 |
| 线程安全 Store | `monitord` + IPC 跨进程状态服务 |
| 默认认为 observer 能旁听 TUI 事件 | 不做该假设 |
| 未写 initialize / initialized | 明确纳入协议流程 |
| `monitor.py` / `panel_app.py` 各自 hold | 统一 reducer，UI 只读 |

---

## 十五、结论

这次重构的关键，不是“再多写几个 Codex 正则”，而是把 Codex 监控从“解析终端表象”升级为“消费官方协议事件”。

在协议约束下，最稳妥的实现方式不是旁路 observer，而是：

- `launcher`
- `app-server proxy`
- `monitord`
- `统一状态机`

只要按这个方向推进，Codex 的 WAITING / RUNNING / IDLE 检测质量会显著高于当前文本解析方案，同时为后续扩展更多结构化信号打下基础。
