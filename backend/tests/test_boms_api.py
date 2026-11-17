"""BOM API 测试：维护/查看/整体替换/成环检测（直接+间接）/用量校验/多级允许。"""

from sqlalchemy import select

from erp_lite.models import (
    AuditRecord,
    Bom,
    BomLine,
    Material,
    MaterialCategory,
    Warehouse,
)


def _seed_mats(db_session, codes=("FG", "SEMI", "RAW")):
    wh = Warehouse(code="W1", name="一号仓")
    mats = [
        Material(code=c, name=f"物料{c}", unit="个", category=MaterialCategory.finished)
        for c in codes
    ]
    db_session.add_all([wh, *mats])
    db_session.commit()
    return {m.code: m for m in mats}


def _put_bom(client, parent, child_ids, qty_per=2, loss_pct=0):
    return client.put(
        f"/api/v1/boms/{parent.id}",
        json={
            "lines": [
                {"material_id": cid, "qty_per": qty_per, "loss_pct": loss_pct}
                for cid in child_ids
            ]
        },
    )


def test_put_and_get_bom_with_lines(client, db_session):
    mats = _seed_mats(db_session)
    resp = _put_bom(client, mats["FG"], [mats["SEMI"].id, mats["RAW"].id])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["material_id"] == mats["FG"].id
    assert len(body["lines"]) == 2
    assert float(body["lines"][0]["qty_per"]) == 2

    resp = client.get(f"/api/v1/boms/{mats['FG'].id}")
    assert resp.status_code == 200
    got = resp.json()
    assert {l["material_id"] for l in got["lines"]} == {mats["SEMI"].id, mats["RAW"].id}
    # 留痕：bom 建档有 audit record
    recs = client.get(
        "/api/v1/audit/records", params={"doc_type": "bom", "doc_id": got["id"]}
    ).json()
    assert recs["total"] >= 1


def test_get_bom_without_bom_404(client, db_session):
    mats = _seed_mats(db_session)
    resp = client.get(f"/api/v1/boms/{mats['FG'].id}")
    assert resp.status_code == 404


def test_put_bom_replaces_lines(client, db_session):
    mats = _seed_mats(db_session)
    _put_bom(client, mats["FG"], [mats["SEMI"].id, mats["RAW"].id])
    resp = _put_bom(client, mats["FG"], [mats["RAW"].id])
    assert resp.status_code == 200
    assert [l["material_id"] for l in resp.json()["lines"]] == [mats["RAW"].id]
    lines = db_session.scalars(
        select(BomLine).join(Bom).where(Bom.material_id == mats["FG"].id)
    ).all()
    assert len(lines) == 1  # 旧行被整体替换，不残留


def test_bom_direct_cycle_400(client, db_session):
    mats = _seed_mats(db_session)
    resp = _put_bom(client, mats["FG"], [mats["FG"].id])  # 自己是自己的子件
    assert resp.status_code == 400
    assert "环" in resp.json()["detail"]


def test_bom_indirect_cycle_400(client, db_session):
    mats = _seed_mats(db_session)
    # 先建 B→A，再建 A→B：A→B→A 间接成环
    r1 = _put_bom(client, mats["SEMI"], [mats["FG"].id])
    assert r1.status_code == 200, r1.text
    r2 = _put_bom(client, mats["FG"], [mats["SEMI"].id])
    assert r2.status_code == 400
    assert "环" in r2.json()["detail"]


def test_bom_qty_per_must_be_positive(client, db_session):
    mats = _seed_mats(db_session)
    resp = _put_bom(client, mats["FG"], [mats["RAW"].id], qty_per=0)
    assert resp.status_code == 400
    assert "用量" in resp.json()["detail"]


def test_bom_loss_pct_range(client, db_session):
    mats = _seed_mats(db_session)
    resp = _put_bom(client, mats["FG"], [mats["RAW"].id], loss_pct=101)
    assert resp.status_code == 400
    assert "损耗" in resp.json()["detail"]


def test_bom_child_with_own_bom_allowed(client, db_session):
    mats = _seed_mats(db_session)
    # 子件 SEMI 自带 BOM → 允许（多级靠递归展开）
    r1 = _put_bom(client, mats["SEMI"], [mats["RAW"].id])
    assert r1.status_code == 200
    r2 = _put_bom(client, mats["FG"], [mats["SEMI"].id])
    assert r2.status_code == 200, r2.text
