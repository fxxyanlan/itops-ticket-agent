"""验收测试样例（spec §11）与期望结果。"""

# expect_status / expect_mode 用于自动化比对
SAMPLES: list[dict] = [
    {
        "desc": "我的打印机 PC-1024 打印一直提示脱机，重启打印服务也不行",
        "contact": "张小明 / 13800000001",
        "expect_status": "已解决",
        "expect_mode": "自动闭环",
        "note": "常见打印机故障，应命中历史方案自动闭环",
    },
    {
        "desc": "核心数据库服务器宕机了，业务全部中断，十万火急！",
        "contact": "李经理 / 13900000002",
        "expect_status": "转人工",
        "expect_mode": "转人工",
        "note": "P0 + 高危类型（数据库），必须转人工",
    },
    {
        "desc": "公司新采购的示波器驱动装不上，型号 XYZ-9",
        "contact": "王工 / 13700000003",
        "expect_status": "转人工",
        "expect_mode": "转人工",
        "note": "无历史匹配方案，转人工",
    },
    {
        "desc": "我忘记 VPN 密码了，需要重置",
        "contact": "赵敏 / 13600000004",
        "expect_status": "已解决",
        "expect_mode": "自动闭环",
        "note": "账号权限类且有历史方案，自动闭环",
    },
    {
        "desc": "会议室投影仪没信号，设备编号 PJ-03",
        "contact": "陈工 / 13500000005",
        "expect_status": "转人工",
        "expect_mode": "转人工",
        "note": "硬件类无匹配方案（匹配度不足），验证条件边",
    },
]