# #419 QA Worker 的 Ego 浏览器验收

2026 年 11 月 26 日（Asia/Singapore），GoodTeam 派出的 QA Worker
在 Full Access 下通过了 StockFlow 仓库切换用例。Worker 先运行
`ego-browser skill`，随后用 Ego 登录本机测试站点、操作页面并保存了三张截图。

## 候选与作业

| 项目 | 记录 |
| --- | --- |
| GoodTeam 基线 | `68d1158767c52acd453ac7d1476f3815a6cc8d49` |
| StockFlow 候选 | `bdb32f9a68eb5391d340e3124b72717cedb85941` |
| 候选 Git tree | `2b247d90b013c8cb5ed399f4c86bec325d9db98a` |
| QA 任务 | `QA419-1` |
| QA 作业 | `job-16a27249-f8da-49de-91ce-da3b75af8cb9`，第 1 次执行 |
| 确认批次 | `task-batch-7a0c2a56054b26f5b71f35e0b9dbbce798c0b810b2390bcf5aa3e41202155df5` |
| 隔离派工 | `dispatch-2f373c71f4806ea4ed6c2ac09981ddd2e6f7ff33bdbdb391dc5e333ef0459990` |
| 实际运行器 | Codex CLI `0.171.0`，`gpt-6.1-sol` |
| 原生会话记录 | `danger-full-access`，审批 `never`，工作区 `dispatch_isolated` |
| Ego 会话 | `2.2.1`，TaskSpace `79`，页面 `p1` |

## QA 实际操作

| 步骤 | 观察结果 | Worker 截图 |
| --- | --- | --- |
| 打开库存页，选中 A 仓 | `DEMO-LAMP` 实物 18、已占用 0、可用 18 | [A 仓](qa-ego-20261126-a-inventory.png)，事件 `item_10` |
| 在库存页切换 B 仓 | 同一 SKU 实物 0、已占用 0、可用 0，卡片显示低库存预警 | [B 仓](qa-ego-20261126-b-inventory.png)，事件 `item_11` |
| 打开概览，选中 B 仓 | 「低库存商品」列出 `DEMO-LAMP`，可用 0 | [B 仓低库存](qa-ego-20261126-b-low-stock.png)，事件 `item_13` |

Worker 最初遇到 SKU 定位歧义，随后又等错了预警区标题。它重新读取页面后
完成用例，并关闭本次 Ego TaskSpace。GoodTeam 收到报告后将批次置为
`review`。派工回执为 `changed_files=[]`，写入范围校验通过。派工收集的
Git tree 与原候选一致。

## 验证范围

- 以 StockFlow 候选副本启动本机 Web 和 API，并在临时 PostgreSQL 中迁移和
  写入演示数据。QA 校对了候选 Git HEAD。服务的源码来源由启动记录绑定。
- 临时启动配置只给本次 Codex QA 开启 Full Access。测试 Team 通过 QA 角色提示
  提供主线的 Ego 方法。确认、隔离派工和报告回流使用 GoodTeam 原有服务。
- 使用本机 Apple Command Line Tools Git 完成隔离派工。验证结束后已关闭
  临时服务并移除临时登录凭据。
- 本次覆盖三项真实浏览器检查。正式权限选择、运行时审批、Role Skill 自动绑定
  和 Stop 仍待产品路径验证。继续跟踪 [#751](https://github.com/LukeJiaoR/GoodTeam/issues/751)
  与 [#419](https://github.com/LukeJiaoR/GoodTeam/issues/419)。

## 截图 SHA-256

| 截图 | SHA-256 |
| --- | --- |
| A 仓 | `ea3af29d964049fbd32b49fb119d883e39a61cb61fd838473a36a5515a8f5f2f` |
| B 仓 | `58d51261ebe0312023a2ac167b976c46875066a6323710ff588daa2e0cdbf80f` |
| B 仓低库存 | `dd74ae7121a2f5ec4d4c3536809c4908d6acac18a0a0905197330f0c89acb152` |
