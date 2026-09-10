# 新 UI 新拟物风格与布局推广

## 目标与范围

- 以已验看的 Work 质感为基准，统一 Projects、Team、Review、Knowledge 和共享导航。
- 同一冷灰材质、左上光源、清晰文字、分层圆角与轻微按压反馈。
- Projects 增加本地搜索及状态筛选；Team 改善角色/Worker/能力布局及查找。
- Review 扩大阅读区域，保留评审操作与证据的可见性。
- Knowledge 增加文档查找，消除切换期间旧正文与新标题混用。
- 不变更后端、审批规则、Worker 调度、依赖与持久化。

## 执行与验证

1. 共享材质、导航、控件及动效。
2. 各页面布局与本地交互。
3. 前端回归、lint、build；隔离只读预览验证桌面/手机、明暗主题、键盘和减少动态效果。

验证级别：Tier 4（浏览器可见路径），覆盖现有接口的前端消费与展现，不涉及控制面契约变化。
使用既有 M0 隔离 smoke 数据源的 GET-only 代理 `127.0.0.1:21920`，不提交修改数据的操作。

## 结果

已完成页面推广与定向自查，沿用现有 Work 视觉基础。

| Before | After | Why |
| --- | --- | --- |
| 页面材质、控件边角与间距不同 | 新 UI 使用同一材质、光源、圆角层级和 140ms 反馈 | 导航与页面之间保持连续感 |
| Team 三块内容使用两列布局 | 桌面角色 / Worker / 能力三列，窄屏分层 | 对齐信息，保持操作触点可用 |
| 项目与团队只能逐行查找 | 本地搜索、状态筛选、空结果恢复、结果数量 | 快速定位；清空搜索后焦点回输入框 |
| Review 进入可审批材料即显示源码 | 默认阅读预览，保留编辑和差异入口 | 先阅读再决定是否修改；审批契约未动 |
| Markdown 固定为深色配色 | 正文、标题、表格与代码跟随明暗主题 | 消除浅色背景上近白正文的问题 |
| Knowledge 切换标题时保留旧正文 | 文档按项目与键挂载，加载占位，忽略过期请求 | 保证标题与内容对应 |

## 验证证据

- `npm run test --prefix frontend`：6 项 Node + 270 项 Vitest 全部通过。
- 新增覆盖：项目筛选与空状态、Worker/模型搜索与禁用筛选、文档加载期间不串内容、文档搜索/焦点恢复、评审默认阅读并可切换编辑。
- `npm run lint --prefix frontend`、`npm run build --prefix frontend`、`git diff --check`：通过。
- 构建仍提示既有的单包体积超过 500 kB，未在本次视觉任务中调整依赖或拆包。
- 浅色 1440/390px：Projects、Team、Review、Knowledge 概览/文档、Work 均无横向溢出。
- 深色 1440/390/320px：同上六个视图均无横向溢出。
- Team 桌面三列宽度约 298/639/341px；Review 队列/正文/决策台约 245/753/339px。
- Knowledge 记忆页手机实测 13 个角色入口，内容宽度与视口一致。
- 英文 Team 手机页与添加 Worker 弹窗无横向溢出；Escape 关闭后焦点回添加 Worker。
- 文档搜索只过滤列表，保持当前阅读文档；清除后焦点回搜索框。项目和团队搜索命中数均按预期变化。
- 实际 Tab 键聚焦显示 2px 轮廓；减少动态效果时运行中动画为 0，添加项目弹窗仍可打开。
- 正文颜色实测：浅色 `#102044` / 背景 `#edf2f8`；深色 `#f2f6ff` / 背景 `#182231`。

浏览器记录与截图保存在本机（未随仓库提交），位于：
`/Users/luke/.codex/visualizations/2026/09/10/01a08926-b18b-7693-8f00-52d06c49d9a1/`

- `rollout-browser-evidence.json`：浅色首轮布局记录。
- `rollout-final-checks.json`：深色与小屏、搜索、键盘和 reduced-motion 记录；其中程序化 focus 未触发 focus-visible，后续实际 Tab 已验证可见轮廓。
- `rollout-team-final.png`、`rollout-artifacts-light.png`、`rollout-artifacts-dark.png`、`rollout-review-dark-1440.png`、`rollout-team-dialog-mobile.png`。

验证使用隔离 smoke 工作区的 GET-only 代理，未提交团队配置、审批、工作流或知识写入。未运行后端全套测试：本次未修改后端或控制面契约。

## 路径展示改进（2026-09-10）

目标：主界面显示名称与用途，完整文件路径按需查看；保持原始路径、搜索能力和文档正文完整。

| Before | After | Why |
| --- | --- | --- |
| 项目列表直接显示绝对路径 | 本地文件夹名称 + 可展开的文件位置 | 减少技术信息占用，仍可区分与定位目录 |
| 知识库直接显示 artifact key 与长路径 | 本地化文档名称与文件名 | 快速理解文档用途；搜索同时匹配名称、键和路径 |
| Work 产物预览、证据与概览中的绝对路径常驻 | 统一 FileLocation 折叠详情，保留完整原文与复制按钮 | 明确区分阅读信息与定位文件的操作 |
| 原弹窗在 window capture 阶段响应 Escape | 开启路径详情时交由内层处理，再次 Escape 关闭弹窗 | 避免误关整个任务 |

- 新增 FileLocation、文件名/文档名称展示 helper；复制成功反馈、失败后的手动复制提示、Windows/UNC 路径支持。
- Work 展开的路径独占一行，预览/源码/复制正文操作保持原有功能。
- 输入路径的表单、命令、文档原文、证据版本与外部链接不转换，未改写持久化数据。
- 最终 `npm run test --prefix frontend`：6 项 Node + 276 项 Vitest 通过。
- `npm run lint --prefix frontend`、`npm run build --prefix frontend`、`git diff --check` 通过；构建仍有既有单包体积提示。
- 隔离只读预览实测：中文“需求”搜索命中需求说明；展开并复制 Linux 完整路径成功；Projects 展开位置不进入 Work；浅色、深色手机页面无横向溢出。
- Work 弹窗第一次 Escape 后弹窗保留且路径关闭，第二次 Escape 关闭任务弹窗；对应集成回归测试通过。
- 截图：`path-knowledge-desktop.png`、`path-knowledge-mobile-expanded.png`、`path-knowledge-mobile-dark.png`、`path-projects-mobile.png`、`path-work-preview-dark.png`，均位于上文截图目录。
- 本次仅调整前端呈现、局部交互和回归测试，无后端或审批契约变更；未运行后端全套测试。
