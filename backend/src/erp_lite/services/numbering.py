"""单据编号：SELECT ... FOR UPDATE 行锁取号，格式 {PREFIX}{YYYYMMDD}{seq:03d}。"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from erp_lite.models import DocSequence


def next_doc_no(db: Session, prefix: str, day: date | None = None) -> str:
    """取下一个单据编号。同一天内按前缀连续递增，跨天/跨前缀互不影响。

    在 PostgreSQL 上 `with_for_update()` 对命中行加行锁防并发重号；
    SQLite 忽略 FOR UPDATE（测试环境单连接串行，无并发问题）。
    注意：必须在调用方事务内调用并随事务提交/回滚。
    """
    seq_date = (day or date.today()).strftime("%Y%m%d")
    row = db.execute(
        select(DocSequence)
        .where(DocSequence.prefix == prefix, DocSequence.seq_date == seq_date)
        .with_for_update()
    ).scalar_one_or_none()
    if row is None:
        row = DocSequence(prefix=prefix, seq_date=seq_date, last_no=1)
        db.add(row)
    else:
        row.last_no += 1
    db.flush()
    return f"{prefix}{seq_date}{row.last_no:03d}"
