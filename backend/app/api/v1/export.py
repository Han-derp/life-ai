from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from app.services import export_service

router = APIRouter(prefix="/export", tags=["export"])

MEDIA_ZIP = "application/zip"


@router.get("/json")
def get_export_json():
    """全部数据导成一个 JSON 文件（可读、可存档、可 grep）。

    ⚠️ **不含图片文件** —— 图片在 `storage/images/`，要完整备份用下面那个 ZIP ✓。
    """
    payload = export_service.export_payload()
    stamp = payload["exported_at_utc"].replace(":", "").replace("-", "")
    return JSONResponse(
        payload,
        headers={
            "Content-Disposition": (
                f'attachment; filename="life-ai-export-{stamp}.json"'
            )
        },
    )


@router.get("/backup.zip")
def get_backup_zip():
    """数据库 + 图片打包下载 —— 这是**可以直接换回去**的那一份 ✓。

    文件会**留在** `backend/storage/backups/` 里（多一层保险 ✓），
    所以反复点会积累多个备份，自己删旧的就行 ✓。
    """
    path = export_service.write_backup_zip()
    return FileResponse(
        path,
        media_type=MEDIA_ZIP,
        filename=path.name,
    )
