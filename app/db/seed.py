"""种子数据：历史已解决工单、设备资产、运维工程师。

说明：历史解决方案直接复用 `tickets` 表中 `status='已解决'` 的记录，
工具1（search_similar_tickets）就是从这些记录里检索。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import MODE_AUTO, STATUS_RESOLVED
from app.db.models import DeviceAsset, Engineer, Ticket

# ===== 历史已解决工单（工具1 的知识来源）=====
# 字段：title, description, fault_type, resolution
HISTORICAL_TICKETS: list[dict] = [
    {
        "title": "打印机提示脱机无法打印",
        "description": "打印机一直提示脱机，重启打印服务后仍无法打印，打印任务一直挂起",
        "fault_type": "打印机",
        "resolution": (
            "1. 打开「服务」面板，重启 Print Spooler（打印后台处理程序）；\n"
            "2. 清空打印队列：删除 C:\\Windows\\System32\\spool\\PRINTERS 下残留文件；\n"
            "3. 在「设备和打印机」中右键打印机 → 取消勾选「脱机使用打印机」；\n"
            "4. 确认打印机电量/电源与网线连接正常，重新添加端口后测试打印。"
        ),
    },
    {
        "title": "打印机卡纸导致无法打印",
        "description": "打印机卡纸，面板提示纸张卡住，取出卡纸后恢复",
        "fault_type": "打印机",
        "resolution": "1. 断电后打开后盖取出卡纸；2. 检查纸张是否受潮变形；3. 重启打印机并做一次测试页。",
    },
    {
        "title": "VPN密码忘记需要重置",
        "description": "员工忘记VPN密码无法登录，需要重置密码后重新配置VPN客户端",
        "fault_type": "账号权限",
        "resolution": (
            "1. 在「统一身份认证」后台为该账号执行密码重置，生成临时密码；\n"
            "2. 通过企业微信将临时密码发送给本人，要求首次登录强制修改；\n"
            "3. 指导员工在 VPN 客户端重新输入新密码并连接测试。"
        ),
    },
    {
        "title": "OA账号被锁定",
        "description": "OA账号连续输错密码被锁定，管理员解锁后恢复",
        "fault_type": "账号权限",
        "resolution": "1. 管理后台解除账号锁定；2. 重置密码并通知本人；3. 提醒开启二次验证。",
    },
    {
        "title": "Outlook无法收发邮件",
        "description": "Outlook提示连接服务器失败，无法收发邮件，重建配置文件后恢复",
        "fault_type": "邮箱",
        "resolution": "1. 检查网络与代理设置；2. 重建 Outlook 配置文件；3. 重置邮箱密码后重新登录验证。",
    },
    {
        "title": "办公区无法连接WiFi",
        "description": "办公区笔记本无法连接公司WiFi，重新获取IP并更新无线网卡驱动后恢复",
        "fault_type": "网络",
        "resolution": "1. 忘记网络后重新连接；2. 释放并重新获取 IP（ipconfig /release && /renew）；3. 更新无线网卡驱动。",
    },
    {
        "title": "台式机显示器黑屏无信号",
        "description": "台式机开机显示器黑屏提示无信号，更换视频线后恢复",
        "fault_type": "硬件",
        "resolution": "1. 检查显示器电源与信号源输入；2. 重新插拔/更换视频线；3. 更换显卡输出接口测试。",
    },
    {
        "title": "财务软件客户端闪退",
        "description": "财务软件客户端打开后闪退，重装客户端并清理缓存后恢复",
        "fault_type": "软件",
        "resolution": "1. 清理客户端缓存目录；2. 卸载后重新安装最新版本；3. 以管理员身份运行并验证登录。",
    },
    {
        "title": "办公电脑无法安装新软件",
        "description": "办公电脑安装新软件提示权限不足，申请管理员权限后安装成功",
        "fault_type": "软件",
        "resolution": "1. 提交软件安装授权申请；2. 由运维远程以管理员权限安装；3. 安装后更新至最新补丁版本。",
    },
    {
        "title": "测试服务器磁盘空间告警",
        "description": "测试服务器磁盘使用率95%告警，清理日志文件后恢复",
        "fault_type": "服务器",
        "resolution": "1. 定位大文件与历史日志；2. 归档并清理超过30天日志；3. 配置日志轮转策略防止再次占满。",
    },
]

# ===== 设备资产（工具2 的数据源）=====
DEVICE_ASSETS: list[dict] = [
    {
        "device_id": "PC-1024",
        "hostname": "PRT-3F-1024",
        "device_type": "网络打印机",
        "os": "HP LaserJet M403dn 固件 2.9",
        "ip": "10.20.3.24",
        "location": "3F 打印区",
        "owner": "行政部",
        "purchase_date": "2022-06-15",
        "warranty_until": "2025-06-14",
        "health_status": "离线",
    },
    {
        "device_id": "PJ-03",
        "hostname": "PJ-2F-A03",
        "device_type": "投影仪",
        "os": "Epson CB-X06 固件 1.4",
        "ip": "10.20.2.33",
        "location": "2F 会议室A",
        "owner": "行政部",
        "purchase_date": "2021-09-01",
        "warranty_until": "2024-08-31",
        "health_status": "待检修",
    },
    {
        "device_id": "SRV-DB-01",
        "hostname": "srv-db-01",
        "device_type": "数据库服务器",
        "os": "CentOS 7.9 / MySQL 8.0",
        "ip": "10.10.0.11",
        "location": "机房A-01",
        "owner": "运维部",
        "purchase_date": "2020-03-20",
        "warranty_until": "2025-03-19",
        "health_status": "告警",
    },
    {
        "device_id": "RTR-01",
        "hostname": "core-router-01",
        "device_type": "核心路由器",
        "os": "Cisco IOS-XE 17.6",
        "ip": "10.10.0.1",
        "location": "机房A-02",
        "owner": "网络组",
        "purchase_date": "2019-11-05",
        "warranty_until": "2024-11-04",
        "health_status": "正常",
    },
    {
        "device_id": "NB-305",
        "hostname": "nb-305",
        "device_type": "笔记本电脑",
        "os": "Windows 11 专业版",
        "ip": "10.20.5.105",
        "location": "研发部",
        "owner": "研发部",
        "purchase_date": "2023-04-11",
        "warranty_until": "2026-04-10",
        "health_status": "正常",
    },
    {
        "device_id": "PC-2001",
        "hostname": "pc-2001",
        "device_type": "办公台式机",
        "os": "Windows 10 企业版",
        "ip": "10.20.4.51",
        "location": "4F 办公区",
        "owner": "市场部",
        "purchase_date": "2022-01-08",
        "warranty_until": "2025-01-07",
        "health_status": "正常",
    },
    {
        "device_id": "PC-1077",
        "hostname": "pc-1077",
        "device_type": "办公台式机",
        "os": "Windows 10 企业版",
        "ip": "10.20.4.77",
        "location": "2F 财务区",
        "owner": "财务部",
        "purchase_date": "2021-07-19",
        "warranty_until": "2024-07-18",
        "health_status": "正常",
    },
    {
        "device_id": "PJ-01",
        "hostname": "PJ-1F-B01",
        "device_type": "投影仪",
        "os": "BenQ MX560 固件 1.1",
        "ip": "10.20.1.21",
        "location": "1F 会议室B",
        "owner": "行政部",
        "purchase_date": "2022-10-02",
        "warranty_until": "2025-10-01",
        "health_status": "正常",
    },
    {
        "device_id": "PRT-05",
        "hostname": "PRT-4F-05",
        "device_type": "网络打印机",
        "os": "Canon iR2625 固件 3.2",
        "ip": "10.20.4.15",
        "location": "4F 打印区",
        "owner": "行政部",
        "purchase_date": "2020-12-25",
        "warranty_until": "2023-12-24",
        "health_status": "正常",
    },
    {
        "device_id": "MAC-09",
        "hostname": "mac-09",
        "device_type": "显示器",
        "os": "Dell U2723QE",
        "ip": "-",
        "location": "3F 设计部",
        "owner": "设计部",
        "purchase_date": "2023-08-16",
        "warranty_until": "2026-08-15",
        "health_status": "正常",
    },
]

# ===== 运维工程师（转人工时按专长 + 负载分派）=====
ENGINEERS: list[dict] = [
    {"name": "张伟", "specialty": "服务器", "level": "L3", "contact": "zhangwei@corp.com", "active_tickets": 2, "avg_hours": 1.5},
    {"name": "李娜", "specialty": "数据库", "level": "L3", "contact": "lina@corp.com", "active_tickets": 1, "avg_hours": 2.0},
    {"name": "王强", "specialty": "网络", "level": "L2", "contact": "wangqiang@corp.com", "active_tickets": 3, "avg_hours": 2.5},
    {"name": "陈静", "specialty": "账号权限", "level": "L2", "contact": "chenjing@corp.com", "active_tickets": 1, "avg_hours": 1.0},
    {"name": "刘洋", "specialty": "打印机", "level": "L1", "contact": "liuyang@corp.com", "active_tickets": 0, "avg_hours": 1.5},
    {"name": "孙磊", "specialty": "硬件", "level": "L2", "contact": "sunlei@corp.com", "active_tickets": 2, "avg_hours": 3.0},
]


def seed_all(session: Session, force: bool = False) -> str:
    """写入种子数据；幂等（已存在则跳过）。返回执行结果描述。"""
    exists = session.scalar(select(Engineer).limit(1))
    if exists and not force:
        return "种子数据已存在，跳过"

    # 历史工单：只补充缺失的标题，避免重复插入
    existing_titles = set(session.scalars(select(Ticket.title)).all())
    added_tickets = 0
    for item in HISTORICAL_TICKETS:
        if item["title"] in existing_titles:
            continue
        session.add(
            Ticket(
                title=item["title"],
                description=item["description"],
                fault_type=item["fault_type"],
                urgency="P3",  # 历史工单均已解决，等级仅作展示
                status=STATUS_RESOLVED,
                resolution_mode=MODE_AUTO,
                resolution=item["resolution"],
            )
        )
        added_tickets += 1

    added_devices = 0
    for item in DEVICE_ASSETS:
        if session.get(DeviceAsset, item["device_id"]):
            continue
        session.add(DeviceAsset(**item))
        added_devices += 1

    added_engineers = 0
    existing_names = set(session.scalars(select(Engineer.name)).all())
    for item in ENGINEERS:
        if item["name"] in existing_names:
            continue
        session.add(Engineer(**item))
        added_engineers += 1

    session.commit()
    return (
        f"种子数据写入完成：历史工单 +{added_tickets}，设备 +{added_devices}，"
        f"工程师 +{added_engineers}"
    )