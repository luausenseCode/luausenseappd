"""luauwhitelist - веб-дашборд (FastAPI): Key / Loader / External Key."""
from __future__ import annotations

import os
import re
import secrets

import db
from config import DATA_DIR, PORT, PUBLIC_URL, WEB_PASSWORD

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOADER_DIR = os.path.join(DATA_DIR, "loaders")
os.makedirs(LOADER_DIR, exist_ok=True)

SAFE_NAME = re.compile(r"^[A-Za-z0-9_\-]{8,64}\.(lua|txt|luau)$")

app = FastAPI(title="luauwhitelist dashboard", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

SESSIONS: set[str] = set()


# --------------------------------------------------------------------------- #
#  auth
# --------------------------------------------------------------------------- #
def require_auth(request: Request) -> bool:
    token = request.cookies.get("lw_session")
    if token not in SESSIONS:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return True


def render(request: Request, name: str, context: dict | None = None, status_code: int = 200):
    """Совместимо с актуальной сигнатурой Starlette (request=..., name=...)."""
    return templates.TemplateResponse(
        request=request, name=name, context=context or {}, status_code=status_code
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return render(request, "login.html", {"error": None})


@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    if not secrets.compare_digest(password, WEB_PASSWORD):
        return render(request, "login.html", {"error": "Wrong password"}, status_code=401)
    token = secrets.token_urlsafe(32)
    SESSIONS.add(token)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie("lw_session", token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 7)
    return response


@app.get("/logout")
async def logout(request: Request):
    token = request.cookies.get("lw_session")
    SESSIONS.discard(token or "")
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("lw_session")
    return response


# --------------------------------------------------------------------------- #
#  dashboard
# --------------------------------------------------------------------------- #
def key_status(row) -> tuple[str, str]:
    """(статус, css-класс)"""
    if row["banned"]:
        return "banned", "bad-red"
    if db.key_expired(row):
        return "expired", "bad-grey"
    if row["used_by"]:
        return "redeemed", "bad-blue"
    return "active", "bad-green"


def decorate(rows):
    out = []
    for r in rows:
        status, cls = key_status(r)
        out.append({
            "key": r["key"],
            "kind": r["kind"],
            "status": status,
            "cls": cls,
            "user": r["used_by"],
            "hwid": r["hwid"] or "-",
            "created_at": (r["created_at"] or "").replace("T", " "),
            "expires_at": (r["expires_at"] or "never").replace("T", " "),
            "resets": r["resets"] or 0,
            "executions": r["executions"] or 0,
            "note": r["note"] or "",
        })
    return out


def loadstring_for(url: str) -> str:
    return f'loadstring(game:HttpGet("{url}"))()'


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, tab: str = "key", msg: str = "", _auth=Depends(require_auth)):
    script_keys = decorate(db.list_keys("script", limit=500))
    ext_keys = decorate(db.list_keys("external", limit=500))
    loaders = []
    for l in db.list_loaders(limit=100):
        url = l["source_url"] or (f"{PUBLIC_URL}/raw/{l['filename']}" if l["filename"] else "")
        loaders.append({
            "id": l["id"],
            "name": l["name"],
            "url": url,
            "loadstring": loadstring_for(url) if url else "",
            "created_at": (l["created_at"] or "").replace("T", " "),
        })
    return render(request, "dashboard.html", {
        "tab": tab,
        "msg": msg,
        "script_keys": script_keys,
        "ext_keys": ext_keys,
        "loaders": loaders,
        "stats": db.stats(),
        "public_url": PUBLIC_URL,
    })


# --------------------------------------------------------------------------- #
#  keys
# --------------------------------------------------------------------------- #
def _to_int(value, default=None, low=None, high=None):
    """Пустые поля формы приходят пустой строкой - не роняем запрос."""
    try:
        result = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    if low is not None:
        result = max(low, result)
    if high is not None:
        result = min(high, result)
    return result


@app.post("/keys/generate")
async def keys_generate(request: Request, kind: str = Form("script"),
                        count: str = Form("100"), days: str = Form(""),
                        note: str = Form(""), _auth=Depends(require_auth)):
    kind = "external" if kind == "external" else "script"
    count = _to_int(count, 100, 1, 500)
    days = _to_int(days, None, 1, 3650)
    keys = db.create_keys(kind, count, days=days, note=note.strip())
    tab = "extkey" if kind == "external" else "key"
    return RedirectResponse(f"/?tab={tab}&msg={len(keys)}%20keys%20created", status_code=303)


@app.post("/keys/delete")
async def keys_delete(request: Request, key: str = Form(...), _auth=Depends(require_auth)):
    ok = db.delete_key(key)
    return RedirectResponse("/?tab=key&msg=" + ("key%20deleted" if ok else "key%20not%20found"),
                            status_code=303)


@app.post("/keys/ban")
async def keys_ban(request: Request, key: str = Form(...), _auth=Depends(require_auth)):
    db.set_banned(key, True)
    return RedirectResponse("/?tab=key&msg=key%20banned", status_code=303)


@app.post("/keys/unban")
async def keys_unban(request: Request, key: str = Form(...), _auth=Depends(require_auth)):
    db.set_banned(key, False)
    return RedirectResponse("/?tab=key&msg=key%20unbanned", status_code=303)


@app.post("/keys/reset")
async def keys_reset(request: Request, key: str = Form(...), _auth=Depends(require_auth)):
    new = db.reset_hwid(key)
    msg = f"hwid%20reset:%20{new}" if new else "key%20not%20found"
    return RedirectResponse(f"/?tab=key&msg={msg}", status_code=303)


# --------------------------------------------------------------------------- #
#  loader
# --------------------------------------------------------------------------- #
@app.post("/loader/make")
async def loader_make(request: Request, name: str = Form(""),
                      file: UploadFile = File(None), source_url: str = Form(""),
                      _auth=Depends(require_auth)):
    url = (source_url or "").strip()
    filename = ""

    if file is not None and file.filename:
        raw = await file.read()
        if raw.strip():
            ext = os.path.splitext(file.filename)[1] or ".lua"
            filename = secrets.token_hex(8) + (ext if ext.startswith(".") else "." + ext)
            with open(os.path.join(LOADER_DIR, filename), "wb") as f:
                f.write(raw)
            url = f"{PUBLIC_URL}/raw/{filename}"

    if not url:
        return RedirectResponse("/?tab=loader&msg=attach%20a%20.lua%20file%20or%20a%20url",
                                status_code=303)

    db.add_loader(name or (file.filename if file and file.filename else "loader"),
                  filename=filename, source_url="" if filename else url)
    return RedirectResponse(f"/?tab=loader&msg=loader%20created", status_code=303)


@app.post("/loader/delete")
async def loader_delete(request: Request, loader_id: int = Form(...), _auth=Depends(require_auth)):
    row = db.get_loader(int(loader_id))
    if row and row["filename"]:
        path = os.path.join(LOADER_DIR, row["filename"])
        if os.path.exists(path):
            os.remove(path)
    db.delete_loader(int(loader_id))
    return RedirectResponse("/?tab=loader&msg=loader%20deleted", status_code=303)


@app.get("/raw/{filename}")
async def raw_lua(filename: str):
    """Сырой .lua файл - именно на него ссылается loadstring(game:HttpGet(...))."""
    if not SAFE_NAME.match(filename):
        raise HTTPException(status_code=404, detail="not found")
    path = os.path.join(LOADER_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="not found")
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    return PlainTextResponse(content, media_type="text/plain; charset=utf-8",
                             headers={"Cache-Control": "no-store"})


@app.get("/healthz")
async def healthz():
    return {"ok": True}


def main() -> None:
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
