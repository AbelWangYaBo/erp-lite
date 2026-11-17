"""RBAC 权限表：硬编码 ROLE_ACTIONS（PRD-v2 §2.5 矩阵，DESIGN-v2 §2）。

YAGNI：演示系统角色固定，不做可配置权限；admin 恒全量。
动作键约定：{域}.{动作}，写动作与敏感查询需显式授权，列表查询全员可读。
JWT 签发 role claim；改角色后需重新登录以刷新 token（README 注明）。
"""

from erp_lite.models.users import Role

# 全量动作集（admin 持有）
ALL_ACTIONS: frozenset[str] = frozenset(
    {
        # 采购
        "po.create",
        "po.update",
        "po.approve",
        "po.receive",
        "po.void",
        "po.close",
        # 销售
        "so.create",
        "so.update",
        "so.approve",
        "so.ship",
        "so.void",
        "so.close",
        # 生产
        "mo.create",
        "mo.release",
        "mo.issue",
        "mo.receive",
        "mo.close",
        "bom.write",
        "mrp.run",
        "mrp.convert",
        # 质量（GMP 检验放行/不合格处置）
        "inspect.judge",
        "batch.dispose",
        # 追溯（GMP 记录可及性：全员）
        "batch.trace",
        # 主数据/资质/财务/用户/审计
        "master.write",
        "partner_cert.write",
        "partner_cert.audit",
        "settlement.create",
        "users.manage",
        "audit.read",
    }
)

# PRD-v2 §2.5 权限矩阵：角色 → 动作集
ROLE_ACTIONS: dict[str, set[str]] = {
    Role.admin.value: set(ALL_ACTIONS),
    Role.purchase.value: {
        "po.create", "po.update", "po.approve", "po.void", "po.close",
        "so.create", "so.update", "so.approve", "so.void", "so.close",
        "partner_cert.write",  # 供应商资质录入
        "batch.trace", "audit.read",
    },
    Role.warehouse.value: {
        "po.receive", "so.ship",
        "batch.trace", "audit.read",
    },
    Role.production.value: {
        "mo.create", "mo.release", "mo.issue", "mo.receive", "mo.close",
        "bom.write", "mrp.run", "mrp.convert",
        "batch.trace", "audit.read",
    },
    Role.qa.value: {
        "inspect.judge", "batch.dispose",
        "partner_cert.audit",  # 供应商资质审核
        "batch.trace", "audit.read",
    },
}


def actions_for(role: str | Role) -> set[str]:
    """角色 → 动作集副本；未知角色返回空集（拒绝一切写动作）。"""
    key = role.value if isinstance(role, Role) else str(role)
    return set(ROLE_ACTIONS.get(key, set()))
