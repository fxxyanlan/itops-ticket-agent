"""业务常量：工单状态、故障类型、紧急等级、高危类型等。"""

# ===== 工单状态（状态流转：新建 → 处理中 → 已解决 / 转人工）=====
STATUS_NEW = "新建"
STATUS_IN_PROGRESS = "处理中"
STATUS_RESOLVED = "已解决"
STATUS_HUMAN = "转人工"
ALL_STATUSES = [STATUS_NEW, STATUS_IN_PROGRESS, STATUS_RESOLVED, STATUS_HUMAN]

# ===== 终态标记：自动闭环 / 转人工 =====
MODE_AUTO = "自动闭环"
MODE_HUMAN = "转人工"

# ===== 故障类型 =====
FAULT_TYPES = [
    "打印机",
    "账号权限",
    "网络",
    "硬件",
    "软件",
    "服务器",
    "邮箱",
    "数据库",
    "安全事件",
    "其他",
]
FAULT_TYPE_OTHER = "其他"

# 高危故障类型：命中即转人工（与 P0 一起构成硬规则）
HIGH_RISK_TYPES = {"服务器", "数据库", "网络核心", "安全事件"}

# ===== 紧急等级 =====
URGENCIES = ["P0", "P1", "P2", "P3"]
URGENCY_DEFAULT = "P2"
URGENCY_CRITICAL = "P0"

# ===== Agent 节点名（同时用于日志展示）=====
NODE_PARSE = "工单解析"
NODE_CLASSIFY = "意图&故障分类"
NODE_AGENT = "Agent决策"
NODE_TOOLS = "工具调用"
NODE_EVIDENCE = "证据补齐"
NODE_AUTO = "自动闭环"
NODE_HUMAN = "转人工"
NODE_FINALIZE = "收尾"

# Agent 系统提示词中的工单上下文标记（mock 模式靠它读取上下文）
AGENT_CONTEXT_MARKER = "【工单上下文】"