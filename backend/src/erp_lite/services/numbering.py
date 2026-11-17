"""单据编号：SELECT ... FOR UPDATE 行锁取号，格式 {PREFIX}{YYYYMMDD}{seq:03d}。"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from erp_lite.models import DocSequence

_MAX_ATTEMPTS = 3


def next_doc_no(db: Session, prefix: str, day: date | None = None) -> str:
    """取下一个单据编号。同一天内按前缀连续递增，跨天/跨前缀互不影响。

    在 PostgreSQL 上 `with_for_update()` 对命中行加行锁防并发重号；
    SQLite 忽略 FOR UPDATE（测试环境单连接串行，无并发问题）。
    并发首单：两个事务同时发现序列行不存在而插入时，后者触发唯一约束
    uq_doc_seq_prefix_date 冲突 → rollback 后重试（最多 3 次），重试时行已被
    前者提交，改走「锁行递增」分支。注意 rollback 会丢弃本事务未提交写入，
    因此取号须在事务早期（写入业务数据前）调用。
    """
    seq_date = (day or date.today()).strftime("%Y%m%d")
    for attempt in range(_MAX_ATTEMPTS):
        try:
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
        except IntegrityError:
            db.rollback()
            if attempt == _MAX_ATTEMPTS - 1:
                raise
    raise RuntimeError("unreachable")  # pragma: no cover
