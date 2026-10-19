import asyncio
from contextlib import asynccontextmanager
import json

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import uvicorn

from .config import ROOT, settings, validate_as_of
from .corpus import Corpus
from .harness import Harness
from .mcp_client import connect_mcp
from .memory import Memory
from .storage import connect, init_db, now


@asynccontextmanager
async def lifespan(app):
    init_db()
    settings.validate_model()
    with connect() as conn:
        conn.execute("UPDATE runs SET status='interrupted',ended_at=%s WHERE status='running'", (now(),))
        conn.execute("UPDATE turns SET status='interrupted',answer='上次运行被中断；原始工具结果仍保留。' WHERE status='running'")
    app.state.corpus = Corpus()
    app.state.memory = Memory()
    app.state.locks = {}
    async with connect_mcp() as bridge:
        app.state.harness = Harness(app.state.corpus, bridge)
        yield


app = FastAPI(title="知报 · 中文财报研究 Agent", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")


class SessionInput(BaseModel):
    title: str = Field(default="新研究", max_length=100)


class ChatInput(BaseModel):
    session_id: str = Field(min_length=1, max_length=50)
    question: str = Field(min_length=1, max_length=4000)
    as_of: str = "2025-08-31"
    mode: str = "agent"


@app.get("/")
def home():
    return FileResponse(ROOT / "web" / "index.html")


@app.get("/api/status")
def status():
    return {"name": "知报", "cutoff": settings.cutoff, "model": settings.model, "configured": settings.configured,
            "context_tokens": settings.context_tokens, "max_steps": settings.max_steps,
            "stats": app.state.corpus.stats(), "default_mode": "agent" if settings.configured else "retrieval"}


@app.get("/api/documents")
def documents(as_of: str = "2025-08-31"):
    try:
        cutoff = validate_as_of(as_of)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return [x for x in app.state.corpus.documents if x["published"] <= cutoff]


@app.get("/api/documents/{document_id}/page/{page}")
def page(document_id: str, page: int, as_of: str = "2025-08-31"):
    try:
        return app.state.corpus.read_page(document_id, page, as_of)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@app.get("/api/documents/{document_id}/pdf")
def pdf(document_id: str, as_of: str = "2025-08-31"):
    try:
        cutoff = validate_as_of(as_of)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    with connect() as conn:
        row = conn.execute("SELECT path FROM documents WHERE id=%s AND published<=%s", (document_id, cutoff)).fetchone()
    if not row:
        raise HTTPException(404, "报告不存在或超出截止日")
    path = (ROOT / row["path"]).resolve()
    if not path.is_relative_to((settings.data_dir / "raw").resolve()):
        raise HTTPException(400, "无效报告路径")
    return FileResponse(path, media_type="application/pdf")


@app.post("/api/sessions")
def create_session(body: SessionInput):
    return {"id": app.state.memory.create_session(body.title)}


@app.get("/api/sessions")
def sessions():
    with connect() as conn:
        return [dict(x) for x in conn.execute("SELECT * FROM sessions ORDER BY updated_at DESC LIMIT 100")]


@app.get("/api/sessions/{session_id}")
def session(session_id: str):
    try:
        return {"turns": app.state.memory.history(session_id)}
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@app.get("/api/sessions/{session_id}/latest-run")
def latest_run(session_id: str):
    with connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE session_id=%s ORDER BY seq DESC LIMIT 1", (session_id,)).fetchone()
    if not row:
        return None
    return {**dict(row), "events": json.loads(row["events"]), "usage": json.loads(row["usage"])}


@app.post("/api/chat")
async def chat(body: ChatInput):
    lock = app.state.locks.setdefault(body.session_id, asyncio.Lock())
    if lock.locked():
        raise HTTPException(409, "本会话已有研究在运行，请等待完成")
    async with lock:
        try:
            return await app.state.harness.run(body.session_id, body.question, body.as_of, body.mode)
        except ValueError as exc:
            raise HTTPException(422, str(exc))


@app.get("/api/artifacts/{artifact_id}")
def artifact(artifact_id: str, session_id: str, as_of: str = "2025-08-31", offset: int = 0):
    try:
        return app.state.memory.artifact(artifact_id, session_id, as_of, offset, 6500)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")
