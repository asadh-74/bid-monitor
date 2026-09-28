"""Persistent project and outreach state. DATABASE_URL must point to durable Postgres on Render."""
import hashlib
import os
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(80), index=True)
    source_id: Mapped[str] = mapped_column(String(255))
    source_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    stage: Mapped[str] = mapped_column(String(80))
    deadline: Mapped[str] = mapped_column(String(100), default="")
    contractor: Mapped[str] = mapped_column(Text, default="")
    contractor_email: Mapped[str] = mapped_column(String(320), default="")
    contact_evidence: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    __table_args__ = (UniqueConstraint("source", "source_id"),)


class Outreach(Base):
    __tablename__ = "outreach"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    recipient: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="draft")
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    project: Mapped[Project] = relationship()


def engine():
    url = os.environ["DATABASE_URL"]
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return create_engine(url, pool_pre_ping=True)


def init_db():
    Base.metadata.create_all(engine())


def session():
    return sessionmaker(bind=engine(), expire_on_commit=False)()


def upsert_projects(items):
    count = 0
    with session() as db:
        for item in items:
            if not item.get("title") or not item.get("source_url"):
                continue
            source, source_id = item["source"], item.get("source_id") or hashlib.sha256(item["source_url"].encode()).hexdigest()
            record = db.scalar(select(Project).where(Project.source == source, Project.source_id == source_id))
            if record is None:
                record = Project(source=source, source_id=source_id, source_url=item["source_url"], title=item["title"], stage=item["stage"])
                db.add(record)
            for field in ("source_url", "title", "stage", "deadline", "contractor", "description"):
                if item.get(field):
                    setattr(record, field, item[field])
            # A name or a procurement specialist's email is not contractor-contact evidence.
            if item.get("contractor_email") and item.get("contact_evidence") and item.get("contractor"):
                record.contractor_email = item["contractor_email"]
                record.contact_evidence = item["contact_evidence"]
            record.last_seen = datetime.now(timezone.utc)
            count += 1
        db.commit()
    return count


def as_dict(row):
    return {col.name: getattr(row, col.name) for col in row.__table__.columns}
