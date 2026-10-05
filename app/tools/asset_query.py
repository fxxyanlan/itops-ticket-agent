"""工具2：查询设备资产信息（型号/系统/IP/位置/负责人/保修状态）。"""

from sqlalchemy import func, select

from app.db.base import session_scope
from app.db.models import DeviceAsset

SPEC = {
    "type": "function",
    "function": {
        "name": "get_device_asset",
        "description": "根据设备编号查询设备资产信息，包括型号、操作系统、IP、存放位置、责任人、采购与保修时间、健康状态",
        "parameters": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "设备编号，如 PC-1024、SRV-DB-01、PJ-03"},
            },
            "required": ["device_id"],
        },
    },
}


def run(device_id: str) -> dict:
    """工具入口：按设备编号查询资产（编号大小写不敏感）。"""
    key = (device_id or "").strip()
    if not key:
        return {"found": False, "message": "未提供设备编号"}

    with session_scope() as session:
        asset = session.scalar(
            select(DeviceAsset).where(func.lower(DeviceAsset.device_id) == key.lower())
        )
        if asset is None:
            return {"found": False, "device_id": key, "message": f"资产系统中未找到设备 {key}，可能未登记"}

        return {
            "found": True,
            "device_id": asset.device_id,
            "hostname": asset.hostname,
            "device_type": asset.device_type,
            "os": asset.os,
            "ip": asset.ip,
            "location": asset.location,
            "owner": asset.owner,
            "purchase_date": asset.purchase_date,
            "warranty_until": asset.warranty_until,
            "health_status": asset.health_status,
        }