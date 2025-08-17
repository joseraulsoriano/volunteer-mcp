#!/usr/bin/env python3
import os
from typing import Any, Dict, List, Optional

from sqlalchemy import create_engine, Integer, Float, String, DateTime, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker
from sqlalchemy.exc import IntegrityError
from datetime import datetime


def _build_engine_url() -> str:
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    return "sqlite:///data/education_jobs.db"


engine = create_engine(_build_engine_url(), echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class University(Base):
    __tablename__ = "universities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nombre: Mapped[str] = mapped_column(String(256), index=True)
    siglas: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    tipo: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    rvoe: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    campuses: Mapped[List["Campus"]] = relationship("Campus", back_populates="university", cascade="all, delete-orphan")


class Campus(Base):
    __tablename__ = "campuses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    university_id: Mapped[int] = mapped_column(ForeignKey("universities.id"))
    campus: Mapped[str] = mapped_column(String(256))
    ciudad: Mapped[str] = mapped_column(String(128))
    estado: Mapped[str] = mapped_column(String(128))
    lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    lng: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    university: Mapped[University] = relationship("University", back_populates="campuses")


class JobPosting(Base):
    __tablename__ = "job_postings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(256), index=True)
    organization: Mapped[Optional[str]] = mapped_column(String(256))
    location: Mapped[Optional[str]] = mapped_column(String(256))
    area: Mapped[Optional[str]] = mapped_column(String(128))
    career: Mapped[Optional[str]] = mapped_column(String(128))
    link: Mapped[str] = mapped_column(String(512), unique=True)
    source: Mapped[Optional[str]] = mapped_column(String(256))
    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


def init_db() -> None:
    os.makedirs("data", exist_ok=True)
    Base.metadata.create_all(engine)


def save_jobs(items: List[Dict[str, Any]]) -> int:
    if not items:
        return 0
    init_db()
    saved = 0
    with SessionLocal() as session:
        for it in items:
            link = str(it.get("link") or it.get("url") or "")
            if not link:
                continue
            # construir objeto
            posted_at = None
            pts = it.get("posted_at")
            if isinstance(pts, str):
                try:
                    posted_at = datetime.fromisoformat(pts)
                except Exception:
                    posted_at = None
            job = JobPosting(
                title=(it.get("title") or it.get("position") or "").strip(),
                organization=(it.get("organization") or it.get("org") or None),
                location=it.get("location") or None,
                area=((it.get("area") or "")[:128] or None) if it.get("area") else None,
                career=(it.get("career") if isinstance(it.get("career"), str) else ",".join(it.get("career", []))) or None,
                link=link,
                source=it.get("source") or None,
                posted_at=posted_at,
            )
            try:
                session.add(job)
                session.commit()
                saved += 1
            except IntegrityError:
                session.rollback()
                # Duplicado por UNIQUE(link): ignorar
                continue
    return saved


def _job_to_dict(j: JobPosting) -> Dict[str, Any]:
    return {
        "id": j.id,
        "title": j.title,
        "organization": j.organization,
        "location": j.location,
        "area": j.area,
        "career": j.career,
        "link": j.link,
        "source": j.source,
        "posted_at": j.posted_at.isoformat() if j.posted_at else None,
        "created_at": j.created_at.isoformat() if j.created_at else None,
    }


def list_jobs(
    q: Optional[str] = None,
    location: Optional[str] = None,
    area: Optional[str] = None,
    career: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> Dict[str, Any]:
    init_db()
    with SessionLocal() as session:
        query = session.query(JobPosting)
        if q:
            like = f"%{q}%"
            query = query.filter(JobPosting.title.ilike(like))
        if location:
            like = f"%{location}%"
            query = query.filter(JobPosting.location.ilike(like))
        if area:
            like = f"%{area}%"
            query = query.filter(JobPosting.area.ilike(like))
        if career:
            like = f"%{career}%"
            query = query.filter(JobPosting.career.ilike(like))
        total = query.count()
        items = query.order_by(JobPosting.created_at.desc()).offset(offset).limit(limit).all()
        return {"total": total, "items": [_job_to_dict(i) for i in items]}


