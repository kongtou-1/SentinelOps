from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import CurrentUser, Database, require_permission
from app.models import User
from app.schemas.agent_config import (
    AgentModule,
    ChannelInput,
    ChannelOrder,
    ChannelTestResponse,
    ConfigInput,
    ConfigResponse,
    VersionInput,
)
from app.services import agent_config as service

router = APIRouter(prefix="/api/v1/agent-configs", tags=["agent-configs"])
ReadUser = Annotated[User, Depends(require_permission("system:read"))]


def administrator(user: CurrentUser):
    if user.role.code != "admin":
        raise HTTPException(403, "Only administrators can manage Agent configuration")
    return user


AdminUser = Annotated[User, Depends(administrator)]


@router.get("", response_model=list[ConfigResponse])
def listing(db: Database, user: ReadUser):
    return service.listing(db)


@router.put("/{module}", response_model=ConfigResponse)
def update(module: AgentModule, body: ConfigInput, db: Database, user: AdminUser):
    return service.update(db, module, body, user)


@router.post("/{module}/channels", response_model=ConfigResponse, status_code=201)
def create_channel(module: AgentModule, body: ChannelInput, db: Database, user: AdminUser):
    return service.save_channel(db, module, None, body, user)


@router.put("/{module}/channels/order", response_model=ConfigResponse)
def order(module: AgentModule, body: ChannelOrder, db: Database, user: AdminUser):
    return service.reorder(db, module, body, user)


@router.put("/{module}/channels/{identifier}", response_model=ConfigResponse)
def save_channel(
    module: AgentModule, identifier: UUID, body: ChannelInput, db: Database, user: AdminUser
):
    return service.save_channel(db, module, identifier, body, user)


@router.delete("/{module}/channels/{identifier}", response_model=ConfigResponse)
def delete_channel(
    module: AgentModule, identifier: UUID, body: VersionInput, db: Database, user: AdminUser
):
    return service.delete_channel(db, module, identifier, body.expected_version, user)


@router.post("/{module}/channels/{identifier}/test", response_model=ChannelTestResponse)
async def test_channel(
    module: AgentModule, identifier: UUID, body: VersionInput, db: Database, user: AdminUser
):
    return await service.test_channel(db, module, identifier, body.expected_version, user)
