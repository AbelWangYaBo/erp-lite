"""编号服务测试：同日连续递增、格式、跨前缀/跨日期独立。"""

import re
from datetime import date

from erp_lite.models import DocSequence
from erp_lite.services.numbering import next_doc_no


def test_serial_increment_same_day(db_session):
    d = date(2026, 11, 17)
    seqs = [next_doc_no(db_session, "PO", day=d) for _ in range(3)]
    assert seqs == ["PO20261117001", "PO20261117002", "PO20261117003"]
    db_session.commit()


def test_format_pattern(db_session):
    no = next_doc_no(db_session, "SO", day=date(2026, 1, 5))
    assert re.fullmatch(r"SO\d{8}\d{3}", no)
    assert no == "SO20260105001"


def test_prefix_independent(db_session):
    d = date(2026, 11, 17)
    assert next_doc_no(db_session, "PO", day=d) == "PO20261117001"
    assert next_doc_no(db_session, "SO", day=d) == "SO20261117001"
    assert next_doc_no(db_session, "PO", day=d) == "PO20261117002"


def test_date_independent(db_session):
    assert next_doc_no(db_session, "PO", day=date(2026, 11, 17)) == "PO20261117001"
    assert next_doc_no(db_session, "PO", day=date(2026, 11, 18)) == "PO20261118001"


def test_sequence_row_updated(db_session):
    d = date(2026, 11, 17)
    for _ in range(5):
        next_doc_no(db_session, "MO", day=d)
    db_session.commit()
    row = db_session.query(DocSequence).filter_by(prefix="MO", seq_date="20261117").one()
    assert row.last_no == 5


def test_first_number_unique_conflict_retry(db_session):
    """回归：首次取号唯一约束冲突 → rollback 后重试（最多 3 次）取号成功。

    模拟并发竞争：本事务 flush 时撞上另一事务已提交的当日序列行（uq_doc_seq_prefix_date）。
    """
    from sqlalchemy.exc import IntegrityError

    d = date(2026, 11, 17)
    assert next_doc_no(db_session, "PO", day=d) == "PO20261117001"
    db_session.commit()

    real_flush = db_session.flush
    calls = {"n": 0}

    def flaky_flush(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise IntegrityError(
                "INSERT INTO doc_sequences ...", {},
                Exception("uq_doc_seq_prefix_date"),
            )
        return real_flush(*args, **kwargs)

    db_session.flush = flaky_flush  # type: ignore[method-assign]
    try:
        assert next_doc_no(db_session, "PO", day=d) == "PO20261117002"
    finally:
        db_session.flush = real_flush  # type: ignore[method-assign]
    assert calls["n"] >= 2  # 冲突后重试成功
    db_session.commit()
    row = db_session.query(DocSequence).filter_by(prefix="PO", seq_date="20261117").one()
    assert row.last_no == 2
