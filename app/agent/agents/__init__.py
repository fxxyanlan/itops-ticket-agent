"""多 Agent 协作层。

架构：Supervisor（调度中枢）+ 专职 Agent
    - intake     解析 Agent：自然语言 → 结构化工单字段
    - triage     分类 Agent：判定「可自动解决 / 必须人工处理」
    - research   检索 Agent：原生 Function Calling 收集证据（历史方案 + 设备资产）
    - resolver   处置 Agent：生成自动闭环方案 或 转人工派单通知
    - critic     审核 Agent：对自动闭环方案做交叉审核，未通过则退回重做
    - finalize   收尾 Agent：汇总处理结果并归档

调度规则集中在 `app.agent.routing.plan_next`，为确定性代码而非模型自由路由，
保证多 Agent 协作过程可解释、可复现、可测试。
"""