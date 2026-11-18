"""RBAC 权限表：硬编码 ROLE_ACTIONS（v3 八角色矩阵，DESIGN-v3 §3）。

YAGNI：演示系统角色固定，不做可配置权限；admin 恒全量。
动作键约定：{域}.{动作}，写动作与敏感查询需显式授权，台账/追溯类全员可读。
JWT 签发 role claim 仅作留痕；鉴权以数据库中的当前角色为准，
角色变更即时生效（无需重新登录，README 注明）。
"""

from erp_lite.models.users import Role

# 全量动作集（admin 持有）
ALL_ACTIONS: frozenset[str] = frozenset(
    {
        # 采购
        "po.create",
        "po.update",
        "po.approve",
        "po.reject",
        "po.receive",
        "po.void",
        "po.close",
        "purchase_return.create",
        # 销售
        "so.create",
        "so.update",
        "so.approve",
        "so.reject",
        "so.ship",
        "so.void",
        "so.close",
        "sales_return.create",
        # 生产
        "mo.create",
        "mo.release",
        "mo.reject",
        "mo.void",
        "mo.issue",
        "mo.unissue",
        "mo.receive",
        "mo.close",
        "bom.write",
        "mrp.run",
        "mrp.convert",
        # 质量（v3 双段制：qc 录入 / qa 判定与处置）
        "batch.inspect",
        "batch.judge",
        "batch.dispose",
        # 追溯（GMP 记录可及性：全员）
        "batch.trace",
        # 主数据/资质/财务/盘点/用户/审计
        "master.write",
        "partner_cert.write",
        "partner_cert.audit",
        "partner_cert.revoke",
        "ap.read",
        "ar.read",
        "settlement.create",
        "settlement.reverse",
        "statement.read",
        "stockcount.create",
        "stockcount.post",
        "users.manage",
        "audit.read",
    }
)

# DESIGN-v3 §3 权限矩阵：角色 → 动作集
ROLE_ACTIONS: dict[str, set[str]] = {
    Role.admin.value: set(ALL_ACTIONS),
    Role.purchase.value: {
        "po.create", "po.update", "po.approve", "po.reject", "po.void", "po.close",
        "partner_cert.write",  # 供应商资质录入
        "ap.read",  # 采购相关应付可见
        "batch.trace", "audit.read",
    },
    Role.sales.value: {
        "so.create", "so.update", "so.approve", "so.reject", "so.void", "so.close",
        "sales_return.create",
        "ar.read",  # 销售相关应收可见
        "batch.trace", "audit.read",
    },
    Role.warehouse.value: {
        "po.receive", "so.ship",
        "stockcount.create",  # 建单/录实盘（post 归 finance）
        "batch.trace", "audit.read",
    },
    Role.production.value: {
        "mo.create", "mo.release", "mo.reject", "mo.void",
        "mo.issue", "mo.unissue", "mo.receive", "mo.close",
        "bom.write", "mrp.run", "mrp.convert",
        "batch.trace", "audit.read",
    },
    Role.qc.value: {
        "batch.inspect",  # 录入检项与建议结论
        "batch.trace", "audit.read",
    },
    Role.qa.value: {
        "batch.judge", "batch.dispose",  # 复核判定 + 不合格处置
        "purchase_return.create",  # 处置(return) 生成退货单
        "partner_cert.audit", "partner_cert.revoke",
        "batch.trace", "audit.read",
    },
    Role.finance.value: {
        "ap.read", "ar.read",
        "settlement.create", "settlement.reverse", "statement.read",
        "stockcount.post",
        "batch.trace", "audit.read",
    },
}


def actions_for(role: str | Role) -> set[str]:
    """角色 → 动作集副本；未知角色返回空集（拒绝一切写动作）。"""
    key = role.value if isinstance(role, Role) else str(role)
    return set(ROLE_ACTIONS.get(key, set()))
