from datetime import timedelta

from sqlalchemy import case, exists, func, or_, select, text

from app.models import (
    DiagnosisReview,
    EvaluationCase,
    EvaluationRun,
    PredictionOutcome,
    PredictionResult,
)


def reviews(analysis_id):
    return (
        select(DiagnosisReview)
        .where(DiagnosisReview.analysis_id == analysis_id, DiagnosisReview.deleted_at.is_(None))
        .order_by(DiagnosisReview.created_at.desc())
    )


def cases():
    return (
        select(EvaluationCase)
        .where(EvaluationCase.deleted_at.is_(None))
        .order_by(EvaluationCase.created_at.desc())
    )


def latest_version(db, family_id):
    lock(db, str(family_id))
    return db.scalar(
        select(EvaluationCase)
        .where(EvaluationCase.family_id == family_id)
        .order_by(EvaluationCase.version.desc())
        .limit(1)
        .with_for_update()
    )


def runs(case_id=None):
    query = select(EvaluationRun).where(EvaluationRun.deleted_at.is_(None))
    return (query.where(EvaluationRun.case_id == case_id) if case_id else query).order_by(
        EvaluationRun.created_at.desc()
    )


def idempotent(db, key):
    lock(db, str(key))
    return db.scalar(select(EvaluationRun).where(EvaluationRun.idempotency_key == key))


def lock(db, key):
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": "evaluation:" + key},
    )


def claim(db):
    return db.scalar(
        select(EvaluationRun)
        .where(EvaluationRun.status == "queued", EvaluationRun.deleted_at.is_(None))
        .order_by(EvaluationRun.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )


def interrupted(db):
    return db.scalars(select(EvaluationRun).where(EvaluationRun.status == "running")).all()


def outcomes(result_id):
    return (
        select(PredictionOutcome)
        .where(PredictionOutcome.result_id == result_id, PredictionOutcome.deleted_at.is_(None))
        .order_by(PredictionOutcome.horizon_hours)
    )


def completed_predictions(db, now):
    # One immutable result may have several horizons; finalized outcomes prevent rework.
    due = [
        (
            (PredictionResult.data_end_at <= now - timedelta(hours=hours))
            & ~exists(
                select(PredictionOutcome.id).where(
                    PredictionOutcome.result_id == PredictionResult.id,
                    PredictionOutcome.horizon_hours == hours,
                )
            )
        )
        for hours in (1, 6, 24)
    ]
    return db.scalars(
        select(PredictionResult)
        .where(
            PredictionResult.status == "completed", PredictionResult.deleted_at.is_(None), or_(*due)
        )
        .order_by(PredictionResult.data_end_at)
        .limit(5)
    ).all()


def values(db, query):
    return db.scalars(query).all()


def summary(db, since):
    effective = case(
        (EvaluationRun.provider_kind == "mock", "baseline"),
        (EvaluationRun.config["profile"]["provider_kind"].as_string() == "mock", "mock_provider"),
        else_="model",
    )
    runs = db.execute(
        select(effective, EvaluationRun.status, func.count())
        .where(EvaluationRun.created_at >= since, EvaluationRun.deleted_at.is_(None))
        .group_by(effective, EvaluationRun.status)
    ).all()
    reviews = db.execute(
        select(DiagnosisReview.verdict, func.count())
        .where(DiagnosisReview.created_at >= since, DiagnosisReview.deleted_at.is_(None))
        .group_by(DiagnosisReview.verdict)
    ).all()
    outcomes = db.execute(
        select(
            PredictionOutcome.status,
            func.count(),
            func.sum(
                case(
                    (PredictionOutcome.observation["threshold_match"].as_boolean().is_(True), 1),
                    else_=0,
                )
            ),
        )
        .where(PredictionOutcome.created_at >= since, PredictionOutcome.deleted_at.is_(None))
        .group_by(PredictionOutcome.status)
    ).all()
    observations = db.execute(
        select(
            PredictionOutcome.status,
            PredictionOutcome.observation,
            PredictionOutcome.incident_links,
        ).where(PredictionOutcome.created_at >= since, PredictionOutcome.deleted_at.is_(None))
    ).all()
    return runs, reviews, outcomes, observations
