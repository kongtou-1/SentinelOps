from datetime import UTC, datetime

from sqlalchemy import cast, or_
from sqlalchemy.dialects.postgresql import JSONB


def predicates(model, application_ids=None, versions=None):
    result = [or_(model.expires_at.is_(None), model.expires_at > datetime.now(UTC))]
    if application_ids is not None:
        result.append(
            or_(
                cast(model.application_ids, JSONB) == [],
                *[
                    cast(model.application_ids, JSONB).contains([str(identifier)])
                    for identifier in application_ids
                ],
            )
        )
    if versions is not None:
        if isinstance(versions, dict):
            result.append(
                or_(
                    cast(model.applicable_versions, JSONB) == [],
                    *[
                        (
                            (cast(model.application_ids, JSONB) == [])
                            | cast(model.application_ids, JSONB).contains([identifier])
                        )
                        & or_(
                            *[
                                cast(model.applicable_versions, JSONB).contains([version])
                                for version in values
                            ],
                            False,
                        )
                        for identifier, values in versions.items()
                    ],
                )
            )
            return result
        result.append(
            or_(
                cast(model.applicable_versions, JSONB) == [],
                *[
                    cast(model.applicable_versions, JSONB).contains([version])
                    for version in versions
                ],
            )
        )
    return result
