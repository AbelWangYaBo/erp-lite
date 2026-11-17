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
