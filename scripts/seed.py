"""幂等种子脚本：连 DATABASE_URL，按 code 判存在，重复执行不报错不重复插入。

用法（任选其一）：
  # 宿主机直连（需要 db 暴露端口时）
  cd backend && DATABASE_URL=postgresql+psycopg://erp:erp123@localhost:5432/erp \
      uv run python ../scripts/seed.py
  # compose 环境（db 不暴露宿主机端口，拷进 api 容器执行）
  docker compose cp scripts/seed.py api:/app/scripts/seed.py
  docker compose exec api uv run python scripts/seed.py

种子内容：2 仓库、2 物料（RAW 螺丝 M3×8 / FG 成品支架）、1 客户、1 供应商、
FG 的 BOM（2×RAW）、5 个演示账号（admin/admin123、purchase/p123、warehouse/w123、
production/p123456、qa/q123，账号名即角色名，密码错开避免同密风险）。
"""

import os
import sys

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

# 兼容两种运行方式：backend venv 内已安装 erp_lite；否则把 backend/src 加入 path
try:
    import erp_lite  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend", "src"))

from erp_lite.models import Bom, BomLine, Material, Partner, User, Warehouse  # noqa: E402
from erp_lite.services.security import hash_password  # noqa: E402

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://erp:erp123@localhost:5432/erp"
)

created: list[str] = []


def get_or_create(session: Session, model, code_field: str, code: str, **kwargs):
    obj = session.scalar(select(model).where(getattr(model, code_field) == code))
    if obj is not None:
        return obj
    obj = model(**{code_field: code}, **kwargs)
    session.add(obj)
    session.flush()
    created.append(f"{model.__tablename__}:{code}")
    return obj


def main() -> None:
    engine = create_engine(DATABASE_URL)
    with Session(engine) as db:
        wh_main = get_or_create(
            db, Warehouse, "code", "MAIN", name="原料仓"
        )
        wh_fg = get_or_create(db, Warehouse, "code", "FG", name="成品仓")

        raw = get_or_create(
            db, Material, "code", "RAW-SCREW-M3X8",
            name="螺丝 M3×8", spec="GB/T 819", unit="个",
            category="raw", default_warehouse_id=wh_main.id,
        )
        fg = get_or_create(
            db, Material, "code", "FG-BRACKET",
            name="成品支架", spec="A-01", unit="个",
            category="finished", default_warehouse_id=wh_fg.id,
        )

        get_or_create(
            db, Partner, "code", "SUP-001",
            name="标准件供应商", type="supplier", contact="李供应商",
        )
        get_or_create(
            db, Partner, "code", "CUS-001",
            name="机械加工客户", type="customer", contact="王客户",
        )

        # FG 的 BOM：2×RAW（一物一 BOM，已存在即跳过）
        bom = db.scalar(select(Bom).where(Bom.material_id == fg.id))
        if bom is None:
            bom = Bom(material_id=fg.id, status="active")
            db.add(bom)
            db.flush()
            db.add(BomLine(bom_id=bom.id, material_id=raw.id, qty_per=2, loss_pct=0))
            created.append("boms:FG-BRACKET(2×RAW-SCREW-M3X8)")

        # v2 演示五账号：账号名即角色名，逐个按 username 判存在（幂等）。
        # 密码错开避免同密风险；改角色后需重新登录以刷新 JWT 里的 role claim。
        demo_accounts = [
            ("admin", "admin123"),
            ("purchase", "p123"),
            ("warehouse", "w123"),
            ("production", "p123456"),
            ("qa", "q123"),
        ]
        for username, password in demo_accounts:
            if db.scalar(select(User).where(User.username == username)) is not None:
                continue
            db.add(
                User(
                    username=username,
                    password_hash=hash_password(password),
                    role=username,
                )
            )
            created.append(f"users:{username}")

        db.commit()

    if created:
        print("seed 完成，新建：")
        for item in created:
            print(f"  + {item}")
    else:
        print("seed 完成：全部已存在，未重复插入（幂等）")


if __name__ == "__main__":
    main()
