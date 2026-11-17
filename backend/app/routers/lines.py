from fastapi import APIRouter, Body
from app.services import line_service as svc

router = APIRouter(prefix="/lines", tags=["lines"])


@router.get("")
def list_lines(business_day: str | None = None):
    return svc.list_lines(business_day)


@router.post("")
def create_line(body: dict = Body(...)):
    return svc.create_line(body)


@router.post("/import")
def import_lines(body: dict = Body(...)):
    return svc.import_lines(body)


@router.post("/{line_id}/reverse")
def reverse_line(line_id: int, body: dict = Body(default={})):
    return svc.reverse_line(line_id, body or {})


@router.patch("/{line_id}/day")
def move_line_day(line_id: int, body: dict = Body(...)):
    return svc.move_line_day(line_id, body)


@router.patch("/{line_id}")
def patch_line(line_id: int, body: dict = Body(...)):
    return svc.patch_line(line_id, body)


@router.delete("/{line_id}")
def delete_line(line_id: int):
    return svc.remove_line(line_id)


@router.get("/pickups")
def list_pickups(business_day: str | None = None):
    return svc.list_pickups(business_day)


@router.post("/pickups")
def add_pickup(body: dict = Body(...)):
    return svc.add_pickup(body)


@router.get("/write-paths")
def write_paths(business_day: str | None = None):
    return svc.write_path_matrix(business_day)
