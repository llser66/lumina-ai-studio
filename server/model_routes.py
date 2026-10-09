import uuid

from fastapi import APIRouter, Depends, Request, Response

from server.model_schemas import (
    ConnectionCreate,
    OAuthAnswer,
    OAuthCallback,
    OAuthStart,
    PreferenceUpdate,
)
from server.security import current_user, digest, mutation_user, rate_limit

router = APIRouter(prefix="/api/models", tags=["Model connections"])


@router.get("/config")
def configuration(request: Request, user=Depends(current_user)):
    return request.app.state.models.configuration(user["id"], request.app.state.provider_ready)


@router.post("/connections", status_code=201)
def create_connection(body: ConnectionCreate, request: Request, user=Depends(mutation_user)):
    rate_limit(request, "model_save", limit=10, user_id=user["id"])
    return request.app.state.models.create_key(user["id"], body)


@router.put("/preferences")
def save_preferences(body: PreferenceUpdate, request: Request, user=Depends(mutation_user)):
    request.app.state.models.save_preferences(user["id"], body)
    return request.app.state.models.configuration(user["id"], request.app.state.provider_ready)


@router.post("/connections/{connection_id}/check")
async def check(connection_id: uuid.UUID, request: Request, user=Depends(mutation_user)):
    rate_limit(request, "model_check", limit=5, user_id=user["id"])
    return await request.app.state.models.check(str(connection_id), user["id"])


@router.delete("/connections/{connection_id}", status_code=204)
async def remove(connection_id: uuid.UUID, request: Request, user=Depends(mutation_user)):
    await request.app.state.models.remove(str(connection_id), user["id"])
    return Response(status_code=204)


@router.post("/oauth", status_code=202)
async def start_oauth(body: OAuthStart, request: Request, user=Depends(mutation_user)):
    rate_limit(request, "model_oauth", limit=5, user_id=user["id"])
    return await request.app.state.models.start_oauth(
        user["id"], digest(request.cookies["lumina_session"]), body
    )


@router.get("/oauth/{flow_id}")
def oauth_status(flow_id: uuid.UUID, request: Request, user=Depends(current_user)):
    service = request.app.state.models
    return service.public_flow(service.flow(str(flow_id), user["id"]))


@router.post("/oauth/{flow_id}/answer")
async def oauth_answer(
    flow_id: uuid.UUID, body: OAuthAnswer, request: Request, user=Depends(mutation_user)
):
    await request.app.state.models.answer(str(flow_id), user["id"], body)
    return {"ok": True}


@router.post("/oauth/{flow_id}/cancel", status_code=204)
async def oauth_cancel(flow_id: uuid.UUID, request: Request, user=Depends(mutation_user)):
    await request.app.state.models.cancel(str(flow_id), user["id"])
    return Response(status_code=204)


@router.post("/oauth/{flow_id}/callback")
async def oauth_callback(
    flow_id: uuid.UUID, body: OAuthCallback, request: Request, user=Depends(mutation_user)
):
    await request.app.state.models.callback(str(flow_id), user["id"], body)
    return {"ok": True}
