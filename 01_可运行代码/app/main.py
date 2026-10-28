from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from app.api import auth, history, responses, surveys
from app.config import Settings
from database.schema import initialize

WEB = Path(__file__).resolve().parents[1] / "web"


def create_app(settings: Settings | None = None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(application):
        client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=3000, tz_aware=True)
        try:
            client.admin.command("ping")
            db = client[settings.mongodb_db]
            initialize(db)
            application.state.db = db
            application.state.settings = settings
            yield
        finally:
            client.close()

    application = FastAPI(title="MongoDB 问卷系统", version="0.8.0", lifespan=lifespan)

    @application.middleware('http')
    async def refresh_frontend(request, call_next):
        response = await call_next(request)
        if not request.url.path.startswith(('/api/', '/health', '/docs', '/openapi.json')):
            response.headers['Cache-Control'] = 'no-cache'
        return response

    @application.exception_handler(RequestValidationError)
    async def request_validation_error(request: Request, exc: RequestValidationError):
        # Raw invalid input may contain NaN/Infinity; reflecting it in a JSON
        # error response can itself fail serialization. Keep safe diagnostics.
        errors = [{key: error[key] for key in ("type", "loc", "msg")} for error in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": errors})

    @application.exception_handler(PyMongoError)
    async def database_error(request: Request, exc: PyMongoError):
        return JSONResponse(status_code=503, content={"detail": "数据库服务暂不可用"})

    application.include_router(auth.router)
    application.include_router(surveys.router)
    application.include_router(responses.router)
    application.include_router(history.router)
    application.mount("/static", StaticFiles(directory=WEB), name="static")

    @application.get("/", include_in_schema=False)
    def home():
        return FileResponse(WEB / "index.html")

    @application.get("/edit/{survey_id}", include_in_schema=False)
    def edit_page(survey_id: str):
        return FileResponse(WEB / "editor.html")

    @application.get('/rules/{survey_id}', include_in_schema=False)
    def rules_page(survey_id: str):
        return FileResponse(WEB / 'rules.html')

    @application.get("/survey/{share_id}", include_in_schema=False)
    def fill_page(share_id: str):
        return FileResponse(WEB / "fill.html")

    @application.get('/statistics/{survey_id}', include_in_schema=False)
    def statistics_page(survey_id: str):
        return FileResponse(WEB / 'statistics.html')

    @application.get('/response/{response_id}', include_in_schema=False)
    def response_page(response_id: str):
        return FileResponse(WEB / 'response.html')

    @application.get("/health", tags=["运行状态"])
    def health(request: Request):
        request.app.state.db.command("ping")
        return {"status": "ok", "database": settings.mongodb_db, "stage": "framework", "version": "0.8.0"}

    return application


app = create_app()

