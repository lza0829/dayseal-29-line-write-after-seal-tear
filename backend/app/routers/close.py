from fastapi import APIRouter, Body
from app.services import close_service as svc

router = APIRouter(prefix="/close", tags=["close"])


@router.get("/preview")
def preview(business_day: str | None = None):
    return svc.preview(business_day)


@router.post("/seal")
def seal(body: dict = Body(...)):
    return svc.seal(body)


@router.post("/reopen")
def reopen(body: dict = Body(...)):
    return svc.reopen(body)


@router.get("/batches")
def batches():
    return svc.list_batches()


@router.get("/variances")
def variances():
    return svc.list_variances()


@router.get("/conflicts")
def conflicts(business_day: str | None = None):
    return svc.list_conflicts(business_day)


@router.get("/integrity")
def integrity(business_day: str | None = None):
    return svc.day_integrity(business_day)


@router.post("/repair")
def repair(body: dict = Body(default={})):
    return svc.repair_sealed(body or {})
