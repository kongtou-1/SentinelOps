from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.api.dependencies import CurrentUser, Database, require_permission
from app.models import User
from app.schemas.notifications import (
    InhibitionInput,
    InhibitionResponse,
    MailSettingsInput,
    MailSettingsResponse,
    MailTestInput,
    MailTestResponse,
    MaintenanceInput,
    MaintenanceResponse,
    NotificationPolicyInput,
    NotificationPolicyResponse,
    NotificationResponse,
    OperationsAuditResponse,
)
from app.services.operations import mail_settings, notifications, response, suppression

router = APIRouter(prefix="/api/v1", tags=["notifications"])
ReadUser = Annotated[User, Depends(require_permission("resources:read"))]
HandleUser = Annotated[User, Depends(require_permission("incidents:handle"))]
ManageUser = Annotated[User, Depends(require_permission("rules:manage"))]
SystemUser = Annotated[User, Depends(require_permission("system:read"))]
AuditUser = Annotated[User, Depends(require_permission("audit:read"))]
MailAdmin = Annotated[User, Depends(require_permission("users:manage"))]
Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=100)]


@router.get("/system/mail-settings", response_model=MailSettingsResponse)
def platform_mail(db: Database, user: SystemUser):
    return mail_settings.read(db)


@router.put("/system/mail-settings", response_model=MailSettingsResponse)
def configure_platform_mail(body: MailSettingsInput, db: Database, user: MailAdmin):
    return mail_settings.configure(db, body, user)


@router.post("/system/mail-settings/test", response_model=MailTestResponse)
def test_platform_mail(body: MailTestInput, db: Database, user: MailAdmin):
    return mail_settings.send_test(db, body, user)


@router.get("/notifications", response_model=list[NotificationResponse])
def feed(
    response: Response,
    db: Database,
    user: CurrentUser,
    unread_only: bool = False,
    offset: Offset = 0,
    limit: Limit = 50,
):
    return notifications.listing(db, user, response, unread_only, offset, limit)


@router.get("/notifications/count")
def unread(db: Database, user: CurrentUser):
    return notifications.unread_count(db, user)


@router.post("/notifications/{identifier}/read", response_model=NotificationResponse)
def mark_read(identifier: UUID, db: Database, user: CurrentUser):
    return notifications.mark_read(db, identifier, user)


@router.delete("/notifications/{identifier}", status_code=204)
def delete_notification(identifier: UUID, db: Database, user: CurrentUser):
    notifications.remove(db, identifier, user)


@router.get("/incidents/{incident_id}/response")
def incident_response(incident_id: UUID, db: Database, user: ReadUser):
    return response.detail(db, incident_id)


@router.post("/incidents/{incident_id}/accept")
def accept_incident(incident_id: UUID, db: Database, user: HandleUser):
    return response.accept_incident(db, incident_id, user)


@router.get("/system/notification-policy", response_model=NotificationPolicyResponse)
def policy(db: Database, user: SystemUser):
    return notifications.read_policy(db)


@router.put("/system/notification-policy", response_model=NotificationPolicyResponse)
def configure_policy(body: NotificationPolicyInput, db: Database, user: ManageUser):
    return notifications.configure_policy(db, body, user)


@router.get("/maintenance-windows", response_model=list[MaintenanceResponse])
def windows(
    response: Response, db: Database, user: ReadUser, offset: Offset = 0, limit: Limit = 50
):
    return suppression.listing(db, response, offset, limit)


@router.post("/maintenance-windows", response_model=MaintenanceResponse, status_code=201)
def create_window(body: MaintenanceInput, db: Database, user: ManageUser):
    return suppression.configure_window(db, body, user)


@router.put("/maintenance-windows/{identifier}", response_model=MaintenanceResponse)
def update_window(identifier: UUID, body: MaintenanceInput, db: Database, user: ManageUser):
    return suppression.configure_window(db, body, user, identifier)


@router.delete("/maintenance-windows/{identifier}", status_code=204)
def delete_window(
    identifier: UUID, expected_version: Annotated[int, Query(ge=1)], db: Database, user: ManageUser
):
    suppression.remove_window(db, identifier, expected_version, user)


@router.get("/alert-inhibitions", response_model=list[InhibitionResponse])
def inhibitions(
    response: Response, db: Database, user: ReadUser, offset: Offset = 0, limit: Limit = 50
):
    return suppression.inhibition_listing(db, response, offset, limit)


@router.post("/alert-inhibitions", response_model=InhibitionResponse, status_code=201)
def create_inhibition(body: InhibitionInput, db: Database, user: ManageUser):
    return suppression.create_inhibition(db, body, user)


@router.delete("/alert-inhibitions/{identifier}", status_code=204)
def delete_inhibition(identifier: UUID, db: Database, user: ManageUser):
    suppression.remove_inhibition(db, identifier, user)


@router.get("/system/operations-audit-logs", response_model=list[OperationsAuditResponse])
def audit_logs(
    response: Response,
    db: Database,
    user: AuditUser,
    offset: Offset = 0,
    limit: Limit = 50,
    scope: Literal["notifications"] | None = None,
):
    return notifications.audit_listing(db, response, offset, limit, scope)
