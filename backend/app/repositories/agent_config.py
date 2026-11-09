from sqlalchemy import select

from app.models import AgentChannel, AgentConfig

MODULES = ("diagnosis", "inspection", "report", "evaluation")


def configuration(db, module, lock=False):
    query = select(AgentConfig).where(
        AgentConfig.module == module, AgentConfig.deleted_at.is_(None)
    )
    return db.scalar(query.with_for_update() if lock else query)


def channels(db, module):
    return db.scalars(
        select(AgentChannel)
        .where(AgentChannel.module == module, AgentChannel.deleted_at.is_(None))
        .order_by(AgentChannel.position, AgentChannel.id)
    ).all()


def channel(db, module, identifier):
    return db.scalar(
        select(AgentChannel).where(
            AgentChannel.id == identifier,
            AgentChannel.module == module,
            AgentChannel.deleted_at.is_(None),
        )
    )


def probe_credential(db, identifier, base_url):
    return db.scalar(
        select(AgentChannel.encrypted_api_key).where(
            AgentChannel.id == identifier,
            AgentChannel.enabled.is_(True),
            AgentChannel.deleted_at.is_(None),
            AgentChannel.base_url == base_url,
        )
    )
