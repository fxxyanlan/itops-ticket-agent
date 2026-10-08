r"""合成基准数据集生成器：为 API 性能测试构造真实量级的 SQLite 数据。

背景
    仓库内没有可用的生产数据源（原种子数据仅 10 条历史工单），任何性能测试都
    无法暴露真实瓶颈。本脚本按 10 类故障模板化生成大规模历史已解决工单，
    写入【独立的基准库】，不污染 demo 库（否则会改变检索结果、破坏既有用例）。

可复现
    固定 `--seed`，同参数多次执行产生完全一致的数据集。

用法
    .\.venv\Scripts\python.exe scripts\gen_benchmark_data.py            # 默认规模
    .\.venv\Scripts\python.exe scripts\gen_benchmark_data.py --tickets 40000 --devices 4000
    .\.venv\Scripts\python.exe scripts\gen_benchmark_data.py --db data/benchmark.db

产物
    data/benchmark.db（tickets / device_assets / engineers）
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB = BASE_DIR / "data" / "benchmark.db"

# 10 类故障的模板：标题 / 描述 / 解决步骤 各若干变体，组合后形成有区分度的语料，
# 使 difflib 相似度分布接近真实（既有高分命中，也有低分不匹配）。
FAULT_TEMPLATES: dict[str, dict[str, list[str]]] = {
    "打印机": {
        "titles": [
            "打印机提示脱机无法打印",
            "打印机卡纸导致无法打印",
            "打印任务一直挂起不执行",
            "打印机驱动异常无法识别设备",
            "网络打印机连接失败",
            "打印页出现条纹且缺色",
        ],
        "descs": [
            "打印机一直提示脱机，重启打印服务后仍无法打印，打印任务一直挂在队列里",
            "打印机卡纸，面板提示纸张卡住，取出卡纸后仍报错无法继续打印",
            "发送打印后任务一直挂起不执行，打印队列显示状态为正在打印但无输出",
            "打印机驱动异常，设备管理器显示黄色感叹号，无法识别打印机型号",
            "网络打印机连接失败，ping 得通但无法建立打印连接，提示端口不可用",
            "打印页出现规律性条纹且彩色缺失，更换墨盒后问题依旧存在",
        ],
        "resolutions": [
            "1. 重启 Print Spooler 打印后台处理程序；\n2. 清空 C:\\Windows\\System32\\spool\\PRINTERS 残留文件；\n3. 取消勾选「脱机使用打印机」；\n4. 重新添加打印端口后测试。",
            "1. 断电后打开检修盖取出卡纸残片；\n2. 检查搓纸轮磨损情况；\n3. 复位打印机并打印测试页确认。",
            "1. 重新安装官方驱动并选择正确型号；\n2. 删除旧打印队列后重新添加打印机；\n3. 固定打印机 IP 避免 DHCP 变更。",
        ],
    },
    "账号权限": {
        "titles": [
            "无法访问共享文件夹权限不足",
            "域账号被锁定无法登录",
            "VPN 账号频繁掉线",
            "新员工账号未开通系统权限",
            "密码过期后无法修改",
            "共享盘只读权限需调整",
        ],
        "descs": [
            "无法访问共享文件夹，提示拒绝访问，需要刷新域账号权限",
            "域账号多次输错被锁定，登录提示账号已锁定，需要解锁并重置",
            "VPN 偶尔断线，切换节点后短暂恢复，一天内多次掉线影响办公",
            "新入职员工账号未开通 OA 与邮箱权限，需要按岗位批量授权",
            "密码已过期，修改时提示不符合复杂度要求，无法自行修改",
            "共享盘目录只有只读权限，需要调整为本部门可读写",
        ],
        "resolutions": [
            "1. 在 AD 中解除账号锁定；\n2. 重置密码并要求下次登录修改；\n3. 核对所属安全组权限是否正确。",
            "1. 在文件服务器 ACL 中为对应用户组追加读写权限；\n2. 注销并重新登录使权限生效；\n3. 验证访问路径。",
            "1. 检查 VPN 客户端版本并升级；\n2. 调整 MTU 与保活参数；\n3. 更换就近接入节点。",
        ],
    },
    "网络": {
        "titles": [
            "无法访问内网提示网络受限",
            "办公区 WiFi 频繁断开",
            "网速异常缓慢影响视频会议",
            "交换机端口指示灯不亮",
            "无法解析内网域名",
            "跨楼层网络时通时断",
        ],
        "descs": [
            "网线更换后无法访问内网，网络图标显示受限，无法获取 IP 地址",
            "办公区 WiFi 频繁断开，重连后不久再次掉线，影响正常办公",
            "网速异常缓慢，视频会议卡顿严重，测速远低于带宽标准",
            "交换机端口指示灯不亮，该端口下设备全部断网",
            "无法解析内网域名，ping IP 通但域名不通，怀疑 DNS 异常",
            "跨楼层访问时通时断，丢包严重，影响业务系统使用",
        ],
        "resolutions": [
            "1. 检查网线与水晶头压接质量；\n2. 释放并重新获取 DHCP 地址；\n3. 确认交换机端口 VLAN 配置正确。",
            "1. 排查 AP 信道干扰并调整；\n2. 升级 AP 固件；\n3. 适当增加 AP 密度消除盲区。",
            "1. 检查 DNS 服务器可达性；\n2. 刷新 DNS 缓存（ipconfig /flushdns）；\n3. 修正内网 DNS 解析记录。",
        ],
    },
    "硬件": {
        "titles": [
            "鼠标光标卡顿无法正常操作",
            "笔记本进水后开机黑屏",
            "电脑风扇异响严重",
            "显示器出现坏点与闪屏",
            "键盘部分按键失灵",
            "主机无法开机电源无反应",
        ],
        "descs": [
            "鼠标光标卡顿，USB 重新插拔后短暂恢复，更换接口仍然复现",
            "笔记本进水，开机黑屏无任何显示，风扇转动但无画面输出",
            "电脑风扇异响，运行时噪音很大，出风口温度偏高",
            "显示器出现固定坏点并伴随间歇性闪屏，影响正常使用",
            "键盘部分按键失灵，外接键盘正常，判断为内置键盘故障",
            "主机按电源键无任何反应，电源指示灯不亮",
        ],
        "resolutions": [
            "1. 更换 USB 接口并测试有线鼠标；\n2. 更新主板芯片组与 USB 驱动；\n3. 排除鼠标本体故障后安排更换。",
            "1. 立即断电并拆卸电池；\n2. 送修做主板烘干与腐蚀检查；\n3. 评估维修与更换成本后处置。",
            "1. 拆机清灰并更换散热硅脂；\n2. 检查风扇轴承磨损，必要时更换风扇；\n3. 复核 CPU 温度是否恢复正常。",
        ],
    },
    "软件": {
        "titles": [
            "Office 字体缺失无法正常显示",
            "软件证书过期无法启动",
            "屏幕亮度无法调节",
            "浏览器缓存过多网页打开缓慢",
            "专业软件闪退无法保存",
            "系统更新后软件不兼容",
        ],
        "descs": [
            "Office 软件字体缺失，文档无法正常显示，排版错乱",
            "软件证书过期，启动时提示证书无效，无法进入主界面",
            "屏幕亮度无法调节，快捷键无效，电源选项里没有亮度滑杆",
            "浏览器缓存过多，网页打开很慢，清理后短时恢复",
            "专业软件运行时闪退，编辑内容无法保存，影响日常工作",
            "系统更新后原有软件不兼容，启动报错缺少运行库",
        ],
        "resolutions": [
            "1. 重新安装缺失字库并清除字体缓存；\n2. 修复 Office 安装；\n3. 复核文档显示是否正常。",
            "1. 导入有效证书到受信任根存储；\n2. 校验证书链完整性；\n3. 重启软件验证。",
            "1. 重装显示驱动；\n2. 检查电源计划中的显示设置；\n3. 更新显卡驱动后重启。",
        ],
    },
    "服务器": {
        "titles": [
            "服务器宕机业务全部中断",
            "服务器 CPU 负载持续过高",
            "服务器磁盘空间告警",
            "服务器内存使用率接近上限",
            "服务器系统时间不同步",
            "应用服务器响应超时",
        ],
        "descs": [
            "服务器宕机，业务全部中断，紧急故障需要立即恢复",
            "服务器 CPU 负载持续过高，业务响应明显变慢",
            "服务器磁盘空间告警，剩余空间不足 5%",
            "服务器内存使用率接近上限，频繁触发 OOM",
            "服务器系统时间错误，需要同步服务器时间",
            "应用服务器响应超时，大量请求返回 502",
        ],
        "resolutions": [
            "1. 确认硬件健康并重启服务；\n2. 切换备用节点承接流量；\n3. 事后复盘并补充监控告警。",
            "1. 定位高负载进程并分析调用栈；\n2. 优化慢查询与线程池配置；\n3. 评估扩容方案。",
            "1. 清理过期日志与临时文件；\n2. 调整日志轮转策略；\n3. 评估扩容或归档策略。",
        ],
    },
    "邮箱": {
        "titles": [
            "Outlook 收件箱爆满无法收信",
            "邮箱无法发送带附件邮件",
            "邮箱被判定为垃圾邮件来源",
            "邮箱客户端配置丢失",
            "邮件附件无法下载",
            "邮箱密码同步失败",
        ],
        "descs": [
            "Outlook 收件箱爆满，无法接收新邮件，需要归档旧邮件",
            "邮箱无法发送带大附件的邮件，提示超出大小限制",
            "邮箱被判定为垃圾邮件来源，对外发信被拒收",
            "邮箱客户端配置丢失，重装后无法同步历史邮件",
            "邮件附件无法下载，点击后无响应或提示服务不可用",
            "邮箱密码同步失败，移动端持续提示密码错误",
        ],
        "resolutions": [
            "1. 创建本地归档并清空服务器大附件；\n2. 设置自动归档策略；\n3. 验证收信恢复正常。",
            "1. 使用超大附件链接方式发送；\n2. 检查发信服务大小限制；\n3. 指导压缩后重发。",
            "1. 检查 SPF/DKIM/DMARC 配置；\n2. 提交解封申请；\n3. 验证对外投递成功率。",
        ],
    },
    "数据库": {
        "titles": [
            "数据库连接间歇性报错",
            "数据库慢查询导致业务卡顿",
            "数据库主从延迟过大",
            "数据库表空间增长过快",
            "数据库备份任务失败",
            "数据库死锁频发",
        ],
        "descs": [
            "数据库连接间歇性报错，连接池偶发耗尽，业务出现短暂不可用",
            "数据库慢查询导致业务卡顿，多个接口响应时间明显上升",
            "数据库主从延迟过大，读库数据滞后影响报表准确性",
            "数据库表空间增长过快，需要评估归档与清理方案",
            "数据库备份任务失败，日志提示空间不足或权限异常",
            "数据库死锁频发，事务相互等待导致请求超时",
        ],
        "resolutions": [
            "1. 检查连接池配置与连接泄漏；\n2. 分析慢日志定位长事务；\n3. 调整最大连接数并加监控。",
            "1. 分析执行计划并补充缺失索引；\n2. 优化 SQL 避免全表扫描；\n3. 复核慢查询数量下降情况。",
            "1. 排查主从网络与 IO 瓶颈；\n2. 优化大事务拆分；\n3. 复核延迟指标恢复正常。",
        ],
    },
    "安全事件": {
        "titles": [
            "账号异常登录疑似被盗用",
            "终端检出木马病毒",
            "钓鱼邮件导致信息泄露风险",
            "服务器出现异常外联行为",
            "弱口令被扫描探测",
            "数据外发行为触发告警",
        ],
        "descs": [
            "账号异常登录疑似被盗用，存在异地登录记录，需要紧急处置",
            "终端检出木马病毒，杀毒软件反复告警，需要隔离处理",
            "收到钓鱼邮件并点击了链接，存在信息泄露风险",
            "服务器出现异常外联行为，存在大流量外发连接",
            "系统弱口令被扫描探测，日志出现大量失败登录记录",
            "数据外发行为触发告警，存在批量导出敏感数据迹象",
        ],
        "resolutions": [
            "1. 立即冻结账号并强制改密；\n2. 排查登录来源 IP 与操作审计；\n3. 开启多因素认证。",
            "1. 断开网络并全盘查杀；\n2. 提取样本做溯源分析；\n3. 补丁加固后重新接入网络。",
            "1. 封禁恶意域名与 IP；\n2. 重置相关凭据；\n3. 开展全员钓鱼演练。",
        ],
    },
    "其他": {
        "titles": [
            "企业网盘上传提示超出大小限制",
            "投影仪画面偏色",
            "会议室视频设备无法投屏",
            "门禁卡无法正常刷卡",
            "办公软件授权到期",
            "打印区排队等待时间过长",
        ],
        "descs": [
            "企业网盘上传提示超出大小限制，无法上传大文件",
            "投影仪画面偏色，线材松动，重新插拔后短时正常",
            "会议室视频设备无法投屏，识别不到信号源",
            "门禁卡无法正常刷卡，多次尝试均提示无效卡",
            "办公软件授权到期，打开时提示需要激活",
            "打印区排队等待时间过长，建议评估新增设备",
        ],
        "resolutions": [
            "1. 使用分卷压缩或改用共享盘；\n2. 申请提升网盘配额；\n3. 验证上传成功。",
            "1. 更换 HDMI/VGA 线材并固定接口；\n2. 校准投影仪色彩参数；\n3. 复核画面显示正常。",
            "1. 检查线材与信号源切换；\n2. 重启视频会议终端；\n3. 更新固件后复测投屏。",
        ],
    },
}

FLOORS = ["1F", "2F", "3F", "4F", "5F", "6F"]
DEPTS = ["研发部", "市场部", "财务部", "行政部", "设计部", "人事部", "运营部", "法务部"]
OWNERS = ["张小明", "李小红", "王大伟", "赵小丽", "陈小刚", "周小敏", "吴小强", "郑小燕"]

ENGINEER_SPECS: list[tuple[str, str, str, int, float]] = [
    ("张伟", "服务器", "L3", 2, 1.5),
    ("李娜", "数据库", "L3", 1, 2.0),
    ("王强", "网络", "L2", 3, 2.5),
    ("陈静", "账号权限", "L2", 1, 1.0),
    ("刘洋", "打印机", "L1", 0, 1.5),
    ("孙磊", "硬件", "L2", 2, 3.0),
    ("何芳", "软件", "L2", 2, 2.0),
    ("马超", "邮箱", "L1", 1, 1.5),
    ("许敏", "安全事件", "L3", 1, 4.0),
]

DEVICE_MODELS: list[tuple[str, str, str]] = [
    ("网络打印机", "HP LaserJet M403dn 固件 2.9", "打印区"),
    ("办公台式机", "Windows 10 企业版 22H2", "办公区"),
    ("笔记本电脑", "Windows 11 专业版 23H2", "工位"),
    ("显示器", "Dell U2723QE", "工位"),
    ("投影仪", "Epson CB-X06 固件 1.4", "会议室"),
    ("核心路由器", "Cisco IOS-XE 17.6", "机房"),
    ("数据库服务器", "CentOS 7.9 / MySQL 8.0", "机房"),
    ("打印机", "Canon iR2625 固件 3.2", "打印区"),
]


def build_tickets(total: int, rng: random.Random) -> list[dict]:
    """按 10 类故障均匀生成历史已解决工单。"""
    fault_types = list(FAULT_TEMPLATES.keys())
    per_type = max(1, total // len(fault_types))
    rows: list[dict] = []
    for ft in fault_types:
        tpl = FAULT_TEMPLATES[ft]
        n = len(tpl["titles"])
        for i in range(per_type):
            idx = i % n
            floor = rng.choice(FLOORS)
            dept = rng.choice(DEPTS)
            owner = rng.choice(OWNERS)
            # 追加位置/部门等噪声，制造相似度梯度（贴近真实工单语料）
            suffix = f"（{floor} {dept}，报障人 {owner}，工位 {rng.randint(100, 999)}）"
            rows.append(
                {
                    "title": tpl["titles"][idx],
                    "description": tpl["descs"][idx] + suffix,
                    "fault_type": ft,
                    "resolution": tpl["resolutions"][i % len(tpl["resolutions"])],
                }
            )
    return rows


def build_devices(total: int, rng: random.Random) -> list[dict]:
    """生成设备资产；编号统一为 dev_0001 形式，便于用例引用。"""
    rows: list[dict] = []
    for i in range(total):
        dtype, dos, place = DEVICE_MODELS[i % len(DEVICE_MODELS)]
        rows.append(
            {
                "device_id": f"dev_{i + 1:04d}",
                "hostname": f"host-{i + 1:05d}",
                "device_type": dtype,
                "os": dos,
                "ip": f"10.{(20 + i // 20000) % 250}.{(i // 250) % 250}.{i % 250 + 1}",
                "location": f"{rng.choice(FLOORS)} {place}",
                "owner": rng.choice(DEPTS),
                "purchase_date": f"{rng.randint(2019, 2024)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
                "warranty_until": f"{rng.randint(2025, 2028)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
                "health_status": rng.choice(["正常", "正常", "正常", "告警", "待检修", "离线"]),
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 API 性能测试用的合成基准数据集")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="目标库文件路径（默认 data/benchmark.db）")
    parser.add_argument("--tickets", type=int, default=20000, help="历史已解决工单条数（默认 20000）")
    parser.add_argument("--devices", type=int, default=2000, help="设备资产条数（默认 2000）")
    parser.add_argument("--seed", type=int, default=42, help="随机种子，保证可复现（默认 42）")
    parser.add_argument("--chunk", type=int, default=1000, help="分批提交大小（默认 1000）")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
        for suffix in ("-wal", "-shm"):
            side = Path(str(db_path) + suffix)
            if side.exists():
                side.unlink()

    # 必须在导入 app.* 之前设置：config.py 在导入期即固化 settings 单例
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    sys.path.insert(0, str(BASE_DIR))

    from app.constants import MODE_AUTO, STATUS_RESOLVED  # noqa: PLC0415
    from app.db.base import Base, SessionLocal, engine, init_db  # noqa: PLC0415
    from app.db.models import DeviceAsset, Engineer, Ticket  # noqa: PLC0415

    init_db()
    rng = random.Random(args.seed)
    t0 = time.perf_counter()

    ticket_rows = build_tickets(args.tickets, rng)
    device_rows = build_devices(args.devices, rng)

    session = SessionLocal()
    try:
        for start in range(0, len(ticket_rows), args.chunk):
            batch = ticket_rows[start : start + args.chunk]
            session.add_all(
                [
                    Ticket(
                        title=item["title"],
                        description=item["description"],
                        fault_type=item["fault_type"],
                        urgency=rng.choice(["P2", "P3"]),
                        status=STATUS_RESOLVED,
                        resolution_mode=MODE_AUTO,
                        resolution=item["resolution"],
                    )
                    for item in batch
                ]
            )
            session.commit()

        for start in range(0, len(device_rows), args.chunk):
            session.add_all([DeviceAsset(**item) for item in device_rows[start : start + args.chunk]])
            session.commit()

        session.add_all(
            [
                Engineer(
                    name=name,
                    specialty=specialty,
                    level=level,
                    contact=f"{name}@corp.com",
                    active_tickets=active,
                    avg_hours=avg,
                )
                for name, specialty, level, active, avg in ENGINEER_SPECS
            ]
        )
        session.commit()
    finally:
        session.close()

    elapsed = time.perf_counter() - t0
    print(f"基准数据集生成完成：{db_path}")
    print(f"  tickets        = {len(ticket_rows)}")
    print(f"  device_assets  = {len(device_rows)}")
    print(f"  engineers      = {len(ENGINEER_SPECS)}")
    print(f"  耗时           = {elapsed:.2f}s")
    print(f"  文件大小       = {db_path.stat().st_size / 1024 / 1024:.2f} MB")
    print("\n提示：基准测试请用环境变量覆盖数据库，例如")
    print(f'  $env:DATABASE_URL="sqlite:///{db_path.as_posix()}"; $env:LLM_MODE="mock"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())