# GoodTeam 功能驱动的体验重设计研究

研究日期：2026 年 10 月 11 日。面向 GoodTeam 产品设计与实施。

## 结论与下一步

GoodTeam 需要围绕“把一个真实任务完成，并能解释它为什么停下、怎样继续、使用了什么依据、交付是否可信”组织体验。新的桌面任务导航、中央对话、可收起的执行区和右侧成果区已经建立了合适的工作台骨架。继续投入的重点应是把已有事实、版本与动作连成可理解的任务路径，而不是再次改一套页面皮肤。

建议保留当前工作台结构，只推进三条具体纵向体验：

1. **记忆查证**：搜索结果能原位打开来源、完整原文、时间、替代关系与引用记录；用户不用解读 JSON，也不会把“检索命中”误认为“本任务实际使用”。这是目前最小、可独立交付的前端切片。
2. **当前尝试的上下文与证据**：从任务进入当前尝试，区分原始约束、锁定方法、实际观察到的读取、产物版本、检查与评审。先展示已有保护的身份和收据；缺少任务绑定的数据明确不可用。
3. **失败到继续与结果核验**：围绕同一任务和尝试展示阻塞、已发生的操作、下一步及其影响；复用真实恢复动作和已有 Review Workbench，不创建前端修复状态机，也不把两个不同执行域强行合并。

同时存在一个比排版更重要的产品边界：自动候选检查与独立 Reviewer 已有控制器，生产外部 correction executor 仍未获准接入。UI 应准确显示这个实际阻塞，不能以增加人工审批、虚构自动修复进度或隐藏权限限制来代替解决它。

## 研究范围和证据等级

本报告检查了实际 React 页面、API、后端投影、执行/记忆合同及历史 UX 记录，并补充官方工具文档和原始研究论文。

研究源码基线：main `df8ead5d864f960adfed8c7375b37aa9683bbf26`。文末源码链接固定到该 SHA。下文矩阵描述此基线，后续实施记录见文末，避免将建议当作已经完成。

标签含义：

- **已有实现**：直接在当前源码或接口发现；不等于本轮运行或视觉验收通过。
- **前端缺口**：后端已有所需字段/能力，当前交互未提供或表达不足。
- **合同缺口**：缺少可靠身份、能力、权限或可验证的数据，不能靠前端推断补齐。
- **验证缺口**：尚无本轮浏览器/真实任务证据。
- **设计假设**：值得测试的安排；未声称已经提升效率。

本轮仅做只读研究与报告写作，没有运行测试、构建、模型或 Git 写入。浏览器视觉验收不可用：本地 Chromium 启动被拒绝，云浏览器访问执行环境 localhost 的 URL 策略被拒绝。没有再次尝试替代地址或绕过限制。因而本文没有宣称当前像素布局、可访问性或端到端交互已验收。

历史 `ux-ui-review-2026-09-21.md` 记录了大量已完成修正，不能继续当作未完成清单。`product-ui-capability-parity.md` 的“迁移完成”也不等于真实模型任务交付验收。旧 `product-vision.md` 中的投资人叙事或人工关卡不是本报告的产品目标依据。[源码 S1、S2]

## 面向使用者的产品模型

### 用户应始终能回答的五个问题

1. 我正在看哪个项目、哪个任务，是当前执行还是历史？
2. 系统正做什么，还是只在等待？如果停下，已知原因和可用下一步是什么？
3. 这次尝试收到的目标、约束、方法和资料是什么？其中哪些只有存储/检索记录，哪些有实际读取证据？
4. 修改或重试会改变什么、复用什么、重新执行什么？哪些外部影响不能撤销？
5. 交付是什么版本，实际做过哪些检查，评审结论是什么，原要求还有什么未被覆盖？

### 信息架构

保留目前的项目入口、Work、Knowledge、Team 和独立 Review 深链。

- **Work 为日常主工作台**：左侧任务与队列、顶部任务身份与下一步、中央项目对话及输入、按需执行详情、右侧交付与证据。
- **上下文从任务或尝试进入**：用同一个详情面板承载“输入与约束 / 方法与资料 / 检查与结果”。不永久增加第四列，不把完整调用图塞进默认对话。
- **Knowledge 为跨任务查证入口**：项目事实、文档、记忆各保留语义。记忆检索结果原位进入来源检查器，能够回到原查询与范围。
- **Team 为长期配置入口**：运行时、角色、Worker、Skill Library 与绑定继续独立。任务中的锁定配置与当前 Team 配置分别标注，不能让保存配置看起来已经改变在途任务。
- **Review Workbench 为已有执行证据深度视图**：从服务端给出的映射进入；先增加上下文连续性，逐步复用投影。不存在的映射显示“此任务没有已绑定的该类证据”。

普通任务应按已授权策略自主推进。只在真实权限边界、无法继续的事实缺口或用户主动干预时突出需要处理的事项。设计不新增强制人工最终验收；也不删掉已有外部权限限制。

## 功能与体验覆盖矩阵

矩阵中的“下一步”是改进建议，不代表当前实现或已测效果。源码编号见文末。

| 用户目标 | 当前可确认的实现 | 缺口或假设 | 薄层改进和边界 | 可测试结果 | 依据 |
| --- | --- | --- | --- | --- | --- |
| 找到要处理的项目 | Projects 有项目载入、查询、attention_required / active 过滤 | 跨项目待处理优先级尚不完整；独立 Inbox 已退役 | 优先复用 Projects 投影；待真实多项目任务显示需要时才扩展聚合，不恢复旧模块总览 | 从项目列表找到指定阻塞并进入正确任务；不把项目总数当阻塞数 | S1；ProjectsPage、projectList |
| 建立或载入项目 | Projects 的 LoadProjectPanel 选择目录并调用 `/api/projects/import`，返回项目后进入 Work；创建/准备事实由既有启动路径处理 | 当前入口是载入目录，不能当作完整的新建目录/仓库向导已完成 | 分清选择已有目录、项目准备与创建首个任务；新增目录/仓库能力先核后端合同，不放无效创建按钮 | 取消后回到原列表和焦点；载入失败可修正原路径；成功进入返回的 project_key，草稿未启动不显示运行 | S1；[LoadProjectPanel](https://github.com/LukeJiaoR/GoodTeam/blob/df8ead5d864f960adfed8c7375b37aa9683bbf26/frontend/src/features/projects/LoadProjectPanel.jsx)、[projectApi](https://github.com/LukeJiaoR/GoodTeam/blob/df8ead5d864f960adfed8c7375b37aa9683bbf26/frontend/src/features/projects/projectApi.js) |
| 说清目标并开始 | 项目持续会话、版本化任务草稿、准备事实、现有启动入口 | 首次任务理解正确率仍需真实任务观察 | 原目标、限制、交付形式与准备状态保持可见；只澄清实际缺项；不制造额外审批步骤 | 查看并开始的任务版本与执行所接收版本一致；讨论不被误当新权限 | S1、S2、TaskDraftControls、ProjectConversation |
| 在任务间切换 | 桌面常驻 TaskNavigation、历史 run 路由、队列草稿 | #512 已完成布局；#513 已修复历史任务 blocker 串绑 | 保持已有实现；未来只有任务数量证明确有需要时增搜索/筛选 | 历史任务不显示当前任务阻塞、不提供当前任务变更操作；项目聊天仍可用 | S3；WorkPage |
| 离开后继续输入 | 草稿、发送确认、输入法保护、项目会话连续性已有 | 无证据支持重新设计整套 composer | 保留 sessionStorage 边界与消息 ID 去重；任务查看身份和项目聊天身份继续分开 | 切页/刷新后未发送文本不丢；迟到响应不覆盖新文本 | S2；ProjectConversation、ConversationComposer |
| 快速知道执行状态 | CurrentWorkContext、真实 execution_actors、当前 attempt、等待依赖说明；执行可收起 | **验证缺口**：当前桌面/窄屏可读性未实测 | 默认给实际阶段、最近可验证事件和下一步；详情按需展开，不用心跳生成进度百分比 | 用户能分辨实际工作、等待依赖、历史失败、未知新鲜度 | S4 |
| 理解失败原因 | TaskAttention 已显示可读错误、历史时间、配置入口、原始错误折叠；active attempt 隐藏重复重试 | 原因、具体 attempt、关键事件的查证路径仍分散；根因常未知 | 增当前 attempt 的证据定位；明确“观察到的失败 / 可能原因 / 未证实” | 不读完整日志也能指出失败对象、证据和合法下一步；不把 429 当额度耗尽证明 | S5；S2 |
| 正确恢复执行 | 既有 retry capability；Reviewer 有锁定输入 retry、cancel；不确定回包提示先刷新 | workflow retry、Reviewer retry、自动 review continue 是不同动作；没有统一“继续所有工作” | 按当前真实对象显示动作名与作用范围；保留旧 attempt；无能力时说明缺项 | 双击/刷新不会新增意外尝试；不确定 transport 不自动重放 | S5、S8、S11 |
| 修改要求并保留上下文 | Intent Commit 保存原文本、解释草稿、版本绑定，Work 内可交接评审修改 | 在途任意 steering / 自动 replan 不是已有通用能力 | 清楚显示“修改讨论 / 已记录变更 / 下次尝试采用”；复用当前受支持变更路径 | 能说出哪些约束已生效；旧证据不能代表新目标或版本 | S1；ConversationChangeContext；runApi |
| 自主检查与修复闭环 | issue-execution 有 exact-candidate 自动 review controller、policy decision、有限 correction history | **合同缺口**：生产外部 correction executor 尚未准入；不是 UI 等待手工批准 | 展示真实 automatic 状态、预算、stop reason、next action；不新增执行 HTTP 接口或前端循环 | 满足策略时可到 reviewed；阻塞时准确指向 executor admission；不虚报自动修复 | S11、S12 |
| 查看交付及其版本 | DeliverySummary、版本/项目/run 绑定报告读取、成果详情、Reviewer 结果与源证据 | 证据可读但分布多处；原始验收项在 issue Workbench 更完整 | 先将“交付、检查、评审、未覆盖项、下一步”组织为一个摘要，深链保留 | 找到主要产物及准确版本，区分检查通过、策略接收和未独立证明的语义正确性 | S6、S8、S12 |
| 核对验收覆盖 | 保护的 issue 输入与 candidate validation 显示每条 criterion，包括未映射项 | 不能将另一执行域的 coverage 直接挂到普通 workflow run | 仅在显式绑定存在时显示逐项证据；缺失项保留 unknown | 重复文字仍保留不同 criterion ID；旧 head、缺失输入不变成绿色完成 | S12、S13 |
| 查看本次确切输入 | task_skill_context / snapshot refs 已在状态保留；issue 输入有保护快照 | **前端/合同混合缺口**：Work 无完整可读任务上下文检查器；部分角色仅公开 refs | 先展示被绑定的原目标/约束/方法版本与输入收据；必要时补受限只读投影，绝不从聊天重建 | 可以回答本次输入来自何时/何版本；legacy、未捕获和不可用可区分 | S8、S9、S13 |
| 知道资料是否真的被读 | 保护 Reviewer resource_reads 有上下文、runtime session、package、digest 绑定 | 此证据只覆盖特定已支持的资源读取，不是所有代码文件、所有 Worker 的阅读历史 | 复用既有证据验证；用“宿主记录的资源读取”描述；不能说证明模型理解 | 无匹配/不完整证据不显示“已读”；资源读与检索/引用分开 | S8 |
| 浏览项目记忆 | role/event 搜索、URL 参数、历史 superseded 条目与详情 API 已有 | **前端缺口**：详情只有 raw JSON；来源/时间/修订关系未被组织 | 结构化原文、来源、角色、时间、替代链；保留 JSON 为诊断 | 1 次打开能看原文，最多再 1 次查看相关版本；无需理解 JSON | S7、S10 |
| 查证跨项目记忆 | 明确 scope 的跨项目检索已有；API 返回 project_key、mem_id、source、timestamp | **前端缺口**：当前只显示项目名和摘要，忽略来源字段、无打开来源入口 | 在原 Knowledge 查询内用 event_project + event 打开来源；验证命中项目和 mem_id | 关闭检查器回到相同角色、查询、范围；同 mem_id 不跨项目串绑 | S7、S10 |
| 区分空记忆与不可用 | role overview 已有 context_status / context_reasons | **前端缺口**：未展示；初始 null 也走 empty。**合同缺口**：cross API 隐去部分来源异常 | 首先展示已有 role 状态；后续给现有跨项目读取结果补 checked/skipped/partial 元数据 | loading、确实无记录、来源失败三个状态不同；空结果不宣称全部来源无匹配 | S7、S10 |
| 纠正过时记忆 | append + supersedes 合同、历史可查、冻结 context 不纳入新事实已有；CLI 可追加 | **合同缺口**：当前 Web 无受控修改 API；来源归属、修订权限与作用范围还需产品合同 | 首片只查证；后片再设计“新增更正”及作用范围，不提供前端直接覆盖原文 | 保留旧事件；显示更正影响未来任务/冻结输入的实际边界；不悄悄更改当前尝试 | S10；memory-file-store |
| 理解谁引用了记忆 | event detail 有 cited_by_jobs；overview 有 latest_retrieval | **语义风险**：前者是 Worker 状态中的引用记录；后者是该 role 最新 job，不是当前任务输入 | 使用准确标签；只有显式映射才能连到任务/尝试；不能按 role 或相近时间拼接 | 用户能区分搜索命中、历史 job 检索、Worker 声明引用和受保护读取证据 | S10 |
| 理解 Team 配置影响 | 原位 AssistantRuntimePanel、Team 命令/版本、角色绑定、Skill 安装预览已有 | “已保存/已安装/已绑定/本次已锁定/真实执行”容易被混同 | 在任务查看锁定版本；在 Team 查看下一次使用配置；启动权限沿用真实 runtime | 保存不伪装运行成功；配置更新不改写在途锁；旧失败不自动证明当前配置失效 | S1、S8、S9；TeamPage、RoleSkillsDialog |
| 用键盘或窄屏完成闭环 | 模态对话、焦点处理、移动页签、reduced motion 与历史回归已有 | **验证缺口**：#512 后未完成当前浏览器视觉验收 | 保留结构，用下一节任务验收重测焦点、返回、长内容与短视口 | 能只用键盘找到失败、原位看来源、返回输入；不靠颜色辨状态，无隐藏操作 | S2、S3、S4 |

## 三条优先纵切

### 第一片 记忆来源与修订检查器

**交付目标**：用户从一条记忆命中出发，知道它写了什么、来自哪里、何时记录、是否被更正，以及系统保存了哪些引用记录。

已有数据足够完成：

- 项目列表已有可信 project_key；`/api/memory/events` 已返回 role、timestamp、source、is_superseded。
- `/api/memory/event` 已返回 text、supersedes、superseded_by、cited_by_jobs。
- `/api/memory/cross` 已返回独立 mem_id，以及包含项目前缀的展示 id。打开详情使用 mem_id，不能拿复合 id 当本项目 event ID。

建议的阅读顺序：正文 → 项目/角色/时间/来源 → 当前版本/替代链 → Worker 引用记录 → 折叠诊断。来源字符串没有被验证为 URL 时原样显示，不能猜文件路径、GitHub 地址或任务深链。

第一片的实现方案是在同一个 Knowledge 页用 `event_project` 与 `event` 表示来源详情，保留原 role、memory_q、cross_q 和 scope；跨项目命中只在 project_key 属于已知项目且属于当前检索范围、mem_id 明确时可打开。该片的实现与验证范围见文末；不计为整套 UX 或真实模型任务验收。

必要测试：

1. 本项目记忆与跨项目命中都展示相同的可读字段；长正文不截成摘要。
2. 点击 supersedes / superseded_by 后显示正确事件；缺失目标为明确不可用，不替换为最近记录。
3. 切换 role、scope、项目或回退时，旧请求迟到不显示在新身份下。
4. 关闭检查器保留查询、范围、角色和主项目；浏览器 Back/Forward 可恢复相应详情。
5. cited_by_jobs 标注为保存的 Worker 引用记录，不说是模型已读证据。
6. 未知/非法项目、mem_id 缺失或响应身份不符时不打开错误事件。

紧接着的独立薄片：显示 role context_status/context_reasons；拆开 loading/empty/error；为来源详情局部恢复读取提供入口。跨项目 partial-completeness 需要 API 小扩展，不在前端靠空数组猜。

### 第二片 当前尝试的上下文与结果证据

**交付目标**：从 Work 顶部或执行条目进入后，不需要去 Team 猜当前配置，也不需要原始日志重建这次尝试。

第一步只提供两层事实：

- 任务层：项目与任务种类、逻辑身份、原目标/约束、锁定版本、是否为 legacy 或未捕获。
- 尝试层：job ID、明确关联的任务引用、方法/包版本、snapshot/context digest、被观察的进程阶段、受保护读取、目标产物版本、检查与评审链接。

为人阅读先显示名称、时间与状态，digest 默认收起可复制。没有证据的栏目说明“没有已绑定记录”，不能说“没有发生”。完整私密输入、凭证、隐藏推理和原始无界日志不应成为这页内容。

最小集成顺序：

1. 复用 `SkillReviewExecutions` 已验证的 task/review 数据和匹配规则，提炼共享只读渲染，不重新实现一套判定。
2. 复用 `state.task_skill_context` / job refs 呈现已有 identity；若某个字段只有保护存储中存在，先确认需要的只读白名单投影，再提供该字段。
3. 记忆仅连接到可证明的相同 job/context receipt。`latest_retrieval` 的“同角色最新”不能用作当前任务来源。
4. 对 issue execution 复用已有保护快照和 Workbench API。workflow_run 与 issue_execution 的合并需要明确服务端映射，不能由前端 run_id、角色、时间或同一 head 猜测。

**完成标准**：给定一个任务和其尝试，测试者可在不打开 raw JSON 的情况下回答“原始要求是什么”“这次使用了哪一个锁定方法版本”“哪些资源读有观测记录”“产物和检查绑定哪个版本”。每个答案可返回相应证据；缺失证据时回答未知。

### 第三片 失败恢复与交付核验

**交付目标**：自主工作遇到阻塞时，用户能判断要不要介入；需要介入时，从当前阻塞到恢复/停止的后果清楚且不重复外部操作。

建议一次只改一个真实恢复域，先做已有 `TaskAttention` 与当前 attempt 的导航，再做已绑定 operator execution 的 Workbench 连续性。

失败卡按以下顺序组织：

1. 哪个任务/尝试停了，当前有没有另一个已在运行的 attempt。
2. 最后一个实际观察到的失败、时间与来源；自动评审判断或推测单独标注。
3. 已发生与尚未确认的操作：例如“本机进程已结束，上游请求是否停止未知”。
4. 服务端允许的下一步：刷新确认、原输入重试、现有运行时配置、停止后续轮次，或明确 admission blocker。
5. 该动作复用哪些输入、是否产生新 attempt、哪些产物/外部效果保留。

禁止一个模糊“重来”覆盖所有恢复。现有归档不是外部进程终止，Reviewer cancel 不是远端请求已撤回，自动 review stop 不是立刻杀掉正在进行的 Reviewer。未知 transport 结果不是“没执行过”。[S5、S8、S11]

交付摘要应按实际合同列出：主产物及版本 → 当前检查收据 → 独立 Reviewer 及结论 → 未覆盖/未确认项 → 下一项真实动作。`accepted_by_review_policy` 是可完成的策略状态；不要把语义判断的局限包装成必须手工批准才能退出的永久等待。

自动 correction runtime 准入属于后端/权限能力缺口。在它未就绪之前，UI 只能显示正确阻塞并保留历史，不能承诺自动修复。实现无需新增 CI，也不改变现有外部权限控制。

## 任务旅程与可观察验收

下面的步数是设计验收目标，尚无实际完成时间或成功率基线。一次“动作”指一次具有语义的导航、打开或提交，不把滚动距离或系统自动刷新当用户动作。

| 旅程 | 起点及操作 | 结束时应能证明什么 | 主要异常场景 |
| --- | --- | --- | --- |
| 重返进行中的任务 | Projects → 指定 Work → 查看顶部身份与状态 | 当前/历史任务、实际是否运行、下一步清楚；不需读完整聊天 | 服务重启、快照 stale、另一任务刚成为 current |
| 定位失败 | 阻塞任务 → 一次定位到失败区 → 一次展开关键证据 | attempt 身份、观察到的错误、发生时间、可用下一步，目标最多两次展开/导航 | 旧 blocker + 新 Worker 正运行；角色相同但 task 不同；来源只有推测 |
| 恢复执行 | 已定位失败 → 检查动作影响 → 已有受支持动作 → 刷新确认 | 旧尝试保留、同一任务、准确输入身份、是否真的新建/继续已获确认 | 连点、网络断开、响应未确认、服务重启、transport 不确定 |
| 查证本次上下文 | 当前任务 → 当前尝试 → 输入与资料 | 原约束、方法版本、实际读取记录与未知部分，目标最多两次进入详情 | Team 后改、源文档后改、缺快照、legacy、失去 pin/权限 |
| 查证记忆 | Knowledge 查询/跨项目命中 → 来源检查器 → 相关修订 → 返回 | 原文、项目/角色/时间/来源、当前替代状态；原查询和范围保留 | 相同 mem_id 出现在不同项目；失效事件；迟到请求；partial 来源 |
| 核验结果 | Work 成果 → 主产物 → 检查/评审/验收项 | 同一 task/head/artifact 版本；通过、未知和失败分别可见 | code 已变化；旧 report 迟到；fixture 被误认为真实模型；部分要求未映射 |
| 给出有范围的修改 | 当前任务中写修改 → 查看既有 change draft → 当前合同要求的确认 → 新尝试/结果 | 原文保存；新解释和旧任务关联清楚；下游究竟是否已更新可判断 | 当前 review 变更、旧 target、未保存编辑、准备仍在进行 |

每条旅程均补充键盘、窄屏、短视口、长内容、Back/Forward 和快速切换；保留 draft、焦点与滚动位置。仅验证主要按钮出现不足以证明旅程可用。

## 怎样测量而不夸大效果

### 工程正确性

使用隔离合成夹具验证身份和异常路径：project/run/attempt/context/artifact 绑定、旧响应拒收、历史只读、重复提交保护、partial 状态和现有权限。按仓库现有验证策略选择检查，不新增 CI 工作流。若控制平面没有变化，勿把只读呈现改动扩成新编排系统。

### 可用性观察

在可用浏览器环境中，给参与者真实但脱敏的记录和明确问题，不先教按钮位置。记录：

- 从看到任务到正确指出失败对象与原因证据的时间、导航次数和正确率。
- 恢复前能否准确说出动作影响；是否误以为会撤销远端副作用。
- 查证确切上下文所需动作数；是否混淆当前 Team 配置、历史 role 检索与本次输入。
- 从记忆命中到来源/修订的时间，返回后过滤条件是否完整。
- 判断交付与原要求是否一致的正确率；是否把退出码、fixture、心跳或 reviewer 判断当成全面验证。
- 需要用户干预的次数与原因；其中哪些为系统缺陷、权限边界或用户主动改需求。

先对当前版本做基线，再用相同或平衡顺序的场景比较后续版本。保存分母、失败案例、任务难度和设备条件。小样本只能作为发现问题的证据；不发表未经测量的“提高多少效率”。

### 真实任务结果

沿用 `m0-product-acceptance.md` 的原合同、独立可检查输出、真实 Provider、干预记录、恢复与复查证据。它不是本轮已经达成的结果。不要把 fake Worker、旧浏览器运行、文档完善或本报告计入真实任务 PASS。

任务结果应至少分开：任务是否按原目标完成、用户是否能正确理解状态、实际验证强度、外部权限是否满足。界面好看或一次流程走通不能替代这四类事实。

## 外部研究如何支持上述决策

研究用于提出和检查设计假设，不用来证明 GoodTeam 已经更快、更可靠。官方产品文档描述其行为，也不是可用性效果实验。

### HAX 人机交互指南

微软 HAX 提供 18 项指南，覆盖初次使用、交互中、系统出错及长期使用。适用于 GoodTeam 的重点是交代能力/可靠性边界、让状态与上下文相关、支持有效纠正、允许全局控制并解释行为。这里将其落实为真实状态、明确未知、可撤回的界面导航和可用恢复入口；不推导固定栏数或全程人工审批。[官方指南](https://www.microsoft.com/en-us/haxtoolkit/ai-guidelines/)

### LangSmith Studio

当前官方文档区分细节丰富的 Graph mode 与更简单的 Chat mode，并列出线程、长期记忆和调试能力。GoodTeam 可以借鉴“日常任务与深度诊断不同层级”的安排，但无需复制完整 graph IDE。线程历史、长期记忆与当前任务输入应有不同语义。[官方 Studio 文档](https://docs.langchain.com/langsmith/studio)

### AGDebugger

CHI 2025 论文使用 5 次形成性访谈和 14 人两部分研究；系统将概要关联到具体消息，支持自主执行、可选干预，并保留分叉历史。适合支持“失败摘要到相关事件”“保留原尝试”“说明恢复点”的设计。研究任务和会话范围有限，浏览器状态 checkpoint 也有近似性，不能推出外部操作都能撤销。[原始论文](https://arxiv.org/html/2503.02068v1)

### AI Chains

CHI 2022 的 20 人研究比较模块化链式界面与单 prompt sandbox。可检查中间结果和局部修改支持了透明度与控制体验；研究没有发现显著的任务完成时间改善，部分参与者反映复杂度增加。对 GoodTeam 的启示是提供有需要时的步骤检查，同时保留任务目标和最终结果为默认焦点，不默认展开一张复杂运行图。[原始论文](https://arxiv.org/html/2110.01691v3)

### XAgen

2025 年 12 月预印本包含 12 次访谈及 8 人初步评价，强调 agent/task 身份、可展开输入输出、失败定位与评估信号。其客观错误定位时间、纠正准确率等仍是未来评估。因此本报告将“摘要连到步骤和证据”作为合理设计假设；绝不引用它声称已经测得调试提速，也不将 evaluator 理由当作已证实根因。[原始论文](https://arxiv.org/html/2512.17896v1)

### Anthropic 上下文工程

官方工程文章区分当前 context、窗口外笔记、按需检索、压缩和独立子代理上下文，并指出过度压缩可能丢掉重要信息。适合校准 GoodTeam 中“已存储、可检索、已提供、被引用、观察到读取”的标签。该文不是 UI 对照实验，不能证明本报告的面板形式最优。[官方文章](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)

### Claude Code checkpointing

当前官方文档将代码恢复、会话恢复、两者恢复和上下文摘要分开，并说明若干不可恢复的变更范围。GoodTeam 可借鉴动作名与真实作用域对应的表达；它只能提供自身后端支持的动作，不能因竞品存在 checkpoint 就新增“撤销一切”的按钮。[官方文档](https://code.claude.com/docs/en/checkpointing)

旧 Letta ADE 文档已转入废弃 v1，未作为当前竞品布局事实使用。BeautifulUI 的视觉组件参考可以服务层级和密度，但不能给上述功能/身份/恢复合同提供依据。

## 数据和交互设计底线

1. **身份先于展示关联**：workflow_run、issue_execution、direct_task、manager_message 分别保持明确身份。任务、attempt、snapshot、context、artifact 和 receipt 之间必须有服务端依据。
2. **检索不等于使用**：memory score 是排序信号，不是置信度；role 最新检索不是当前任务；Worker 引用声明不是宿主输入或资源读取证据。
3. **配置不等于本次执行**：已安装、已绑定、已锁定、已提交输入、已观察读取和已返回结果分别表达。
4. **日志和上下文不等于隐藏推理**：展示用户输入、约束、可核验动作、来源与结果即可。不要索取或呈现模型隐藏思维链。
5. **未知可以是正确答案**：unavailable、partial、stale、legacy 与真正 empty 不合并；未知不伪装失败或成功。
6. **历史必须保留**：更正追加新事实，不改写原尝试；重试复用/改变的范围明示。记忆更正与任务输入修订不是同一个操作。
7. **自主与控制共存**：已有自动路径按授权继续；需要的是真实停止/检查/恢复入口，不是为了“可控”强加最终手工批准。
8. **前端不建立事实权威**：不据聊天摘要判断完成、不按角色猜 job、不推导外部权限、不自动重放不确定操作。

## 源码证据索引

所有源码链接固定到 `df8ead5d864f960adfed8c7375b37aa9683bbf26`；文件内行号以此次只读检查为准。

- **S1 功能覆盖基线**：[product-ui-capability-parity.md](https://github.com/LukeJiaoR/GoodTeam/blob/df8ead5d864f960adfed8c7375b37aa9683bbf26/docs/product-ui-capability-parity.md)，含各现有 API、路由、迁移边界及 #397 等未完成项。
- **S2 已完成 UX 记录**：[ux-ui-review-2026-09-21.md](https://github.com/LukeJiaoR/GoodTeam/blob/df8ead5d864f960adfed8c7375b37aa9683bbf26/docs/ux-ui-review-2026-09-21.md)，尤其运行时原位配置、发送/回复分离、阻塞迁移、current_attempt_id 绑定、审批进入结果区等后续记录。
- **S3 任务导航**：[TaskNavigation.jsx](https://github.com/LukeJiaoR/GoodTeam/blob/df8ead5d864f960adfed8c7375b37aa9683bbf26/frontend/src/features/runs/workspace/TaskNavigation.jsx#L30)，`currentRunId` 绑定 blocker；[WorkPage.jsx](https://github.com/LukeJiaoR/GoodTeam/blob/df8ead5d864f960adfed8c7375b37aa9683bbf26/frontend/src/features/runs/WorkPage.jsx) 中的 current/historical 投影。
- **S4 当前工作台**：[TeamWorkspace.jsx](https://github.com/LukeJiaoR/GoodTeam/blob/df8ead5d864f960adfed8c7375b37aa9683bbf26/frontend/src/features/runs/workspace/TeamWorkspace.jsx#L99-L185) 的执行/结果区；[currentWorkContext.js](https://github.com/LukeJiaoR/GoodTeam/blob/df8ead5d864f960adfed8c7375b37aa9683bbf26/frontend/src/features/runs/workspace/currentWorkContext.js) 的状态与导航，只定位现有控件。
- **S5 阻塞恢复**：[TaskAttention.jsx](https://github.com/LukeJiaoR/GoodTeam/blob/df8ead5d864f960adfed8c7375b37aa9683bbf26/frontend/src/features/runs/workspace/TaskAttention.jsx)，按已有 actions 提供恢复；[runApi.js](https://github.com/LukeJiaoR/GoodTeam/blob/df8ead5d864f960adfed8c7375b37aa9683bbf26/frontend/src/features/runs/runApi.js) 中的 retry、terminate、Intent Commit 合同。
- **S6 交付证据**：[DeliverySummary.jsx](https://github.com/LukeJiaoR/GoodTeam/blob/df8ead5d864f960adfed8c7375b37aa9683bbf26/frontend/src/features/runs/workspace/DeliverySummary.jsx) 的绑定报告读取；[executionEvidence.js](https://github.com/LukeJiaoR/GoodTeam/blob/df8ead5d864f960adfed8c7375b37aa9683bbf26/frontend/src/features/runs/workspace/executionEvidence.js) 明确禁止从 workflow run_id 推导 execution ID。
- **S7 Knowledge 当前呈现**：[KnowledgePage.jsx](https://github.com/LukeJiaoR/GoodTeam/blob/df8ead5d864f960adfed8c7375b37aa9683bbf26/frontend/src/features/knowledge/KnowledgePage.jsx#L50-L158)，50–145 行为 URL 与请求隔离，146–155 行为 empty/error、raw JSON 与跨项目结果；[knowledgeApi.js](https://github.com/LukeJiaoR/GoodTeam/blob/df8ead5d864f960adfed8c7375b37aa9683bbf26/frontend/src/features/knowledge/knowledgeApi.js)。
- **S8 Reviewer 真实证据与控制**：[SkillReviewExecutions.jsx](https://github.com/LukeJiaoR/GoodTeam/blob/df8ead5d864f960adfed8c7375b37aa9683bbf26/frontend/src/features/runs/workspace/SkillReviewExecutions.jsx#L74-L108) 的完整身份匹配；同文件 225–366 行为实际读取/进程状态/cancel/retry；[trusted-native-reviewer.md](https://github.com/LukeJiaoR/GoodTeam/blob/df8ead5d864f960adfed8c7375b37aa9683bbf26/docs/skills/trusted-native-reviewer.md) 列明 runtime 与远端副作用限制。
- **S9 任务锁与生产边界**：[task-skill-dispatch.md](https://github.com/LukeJiaoR/GoodTeam/blob/df8ead5d864f960adfed8c7375b37aa9683bbf26/docs/task-skill-dispatch.md)，与更早的 task-skill-locks.md 分阶段读取；当前特定 Reviewer 能力以 S8 为准，不将旧“所有适配器不通”的阶段记录当当前全局结论。[web.py](https://github.com/LukeJiaoR/GoodTeam/blob/df8ead5d864f960adfed8c7375b37aa9683bbf26/src/goodteam_os/web.py#L3432) 保留 task_skill_context。
- **S10 记忆字段与语义**：[web.py 7361–7520](https://github.com/LukeJiaoR/GoodTeam/blob/df8ead5d864f960adfed8c7375b37aa9683bbf26/src/goodteam_os/web.py#L7361-L7520) 定义引用记录、role 最新 job、context_status 与修订详情；[cross_project_memory.py](https://github.com/LukeJiaoR/GoodTeam/blob/df8ead5d864f960adfed8c7375b37aa9683bbf26/src/goodteam_os/cross_project_memory.py#L34-L55) 保留 mem_id/source/time 但跳过错误来源；[memory_core/projections.py](https://github.com/LukeJiaoR/GoodTeam/blob/df8ead5d864f960adfed8c7375b37aa9683bbf26/src/goodteam_os/memory_core/projections.py) 定义检索/浏览投影；[memory-file-store.md](https://github.com/LukeJiaoR/GoodTeam/blob/df8ead5d864f960adfed8c7375b37aa9683bbf26/docs/memory-file-store.md) 说明更正、冻结 packet、partial/unavailable 与历史边界。
- **S11 自动与受限纠正**：[automatic-candidate-review.md](https://github.com/LukeJiaoR/GoodTeam/blob/df8ead5d864f960adfed8c7375b37aa9683bbf26/docs/automatic-candidate-review.md)；[supervised-correction-loop.md](https://github.com/LukeJiaoR/GoodTeam/blob/df8ead5d864f960adfed8c7375b37aa9683bbf26/docs/supervised-correction-loop.md)。现有控制器不强制人工最终批准；外部 correction 准入仍 blocked。
- **S12 Workbench 与验收证据**：[evidence_manifest.py](https://github.com/LukeJiaoR/GoodTeam/blob/df8ead5d864f960adfed8c7375b37aa9683bbf26/src/goodteam_os/execution/evidence_manifest.py#L24-L101)；[acceptance-coverage-display.md](https://github.com/LukeJiaoR/GoodTeam/blob/df8ead5d864f960adfed8c7375b37aa9683bbf26/docs/acceptance-coverage-display.md)；[web.py 6889–6913](https://github.com/LukeJiaoR/GoodTeam/blob/df8ead5d864f960adfed8c7375b37aa9683bbf26/src/goodteam_os/web.py#L6889-L6913) 的已存在 Workbench API 与页面。
- **S13 原始输入与产品验收**：[task-input-snapshots.md](https://github.com/LukeJiaoR/GoodTeam/blob/df8ead5d864f960adfed8c7375b37aa9683bbf26/docs/task-input-snapshots.md)，不把完整动态 TaskState/compactor 当已完成；[m0-product-acceptance.md](https://github.com/LukeJiaoR/GoodTeam/blob/df8ead5d864f960adfed8c7375b37aa9683bbf26/docs/m0-product-acceptance.md)，真实任务合同和证据标准。

## 实施顺序与停止条件

- 先完成第一片的来源查证与异常状态，不触碰记忆写入或执行合同。交付以字段正确、原位导航、身份隔离和后续实际浏览器验收为条件。
- 然后将第二片限制在已有显式绑定的一个执行类型与一类上下文证据，确认信息足够后再扩展。缺少可靠映射就停止该关联并显示未知，不以 UI 猜测补齐。
- 第三片逐个恢复域实施，先诊断与导航、再受支持动作的影响说明。生产自主修复受 runtime admission 阻塞时保持该事实，不把它转换成设计层人工关卡。
- 真正需要跨执行域统一、用户 Web 记忆更正或自动 correction runtime 时，单独设计其后端合同。它们不属于皮肤重排，也不应偷偷进入一次前端补丁。
- 最后在可用环境对上述七条旅程做浏览器和真实任务验收。没有这些观察，不宣布全面重设计完成或已经改善任务成功率。


## 第一片实施记录

本片实现 `MemoryEventInspector` 与原 Knowledge 记忆页接线：

- 本项目事件与明确跨项目命中均可原位阅读原文、来源、时间、角色、替代链和保存的 Worker 引用。诊断 JSON 默认收起。
- `event_project` 和事件 ID 共同定位来源，详情返回必须匹配事件、角色及可用的项目身份。未选择范围、未知来源或身份不符不展示错误详情。
- 返回查询保留角色、查询草稿、跨项目查询与 scope；支持 Back/Forward 和返回触发控件的键盘焦点。
- 初始载入、空记录、读取失败、role partial/unavailable 分开展示；失败读取可原位重试。跨项目 API 仍不提供逐源完整性，界面不把空结果表述为所有项目均无匹配。
- 记忆界面保持只读。后端未提供受控 Web 更正 API，也没有保留/替代原因字段；展示已有替代关系和原文，不推断事实真伪、理由或对在途任务的影响。

本片采用相关 DOM 交互、真实后端投影夹具合同、lint 和构建验证。浏览器视觉及真实模型使用记忆未验收；具体命令和结果记录于对应 PR。未增加 CI 流程、执行权限或人工审批步骤。后续优先推进本报告第二片，保留跨项目完整性及受控更正为明确合同缺口。

## 第二片实施范围：任务输入与锁定方法

从 Work 中明确绑定的当前/历史 workflow run 按需查看原任务要求、交付标准、捕获时间和锁定的方法版本。投影复用登记任务锁，区别原始捕获、未验证的 workflow 记录和不可用的捕获；不把当前 Team、同角色最新记忆或聊天推断当作本次输入。已有 Reviewer 资源读取依据继续沿用原来的身份/版本检查。

此片只补任务层的输入查证和既有审查证据入口，不代表所有 Worker 的尝试输入、记忆实际加载、动态上下文压缩或 issue/workflow 跨域映射已经完成。详细合同与验收范围见 [任务输入检查器](task-input-context-view.md)，最终验证结果见对应 PR。

## 第三片实施范围：对准当前失败尝试的恢复

实际 HTTP 已复现旧 A 页面重试了当前 B 的错误。恢复请求现在绑定显示的 run 和阶段/attempt 版本，在既有项目锁内比较，拒绝旧任务、已变更尝试、活跃或状态不明的尝试。拒绝发生在日志、恢复处理和执行之前，避免错误中断替换后的任务；每次准入重试也留下独立开始记录，失败后旧请求不能自动重放。

界面按原有恢复模式说明作用范围，并在冲突时保留旧失败依据和草稿，显式刷新后才采用新绑定。详细范围与受控 HTTP/DOM 验证见 [重试目标合同](workflow-retry-target.md)。本片不新增页面、审批或 CI，不代表原生执行、全部恢复域或浏览器视觉验收已完成。
