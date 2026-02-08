from fastapi import FastAPI

try:
    from backend.meta import init_meta_db
    from backend.threads import router as threads_router
    from backend.worldlines import router as wordlines_router
    from backend.tools import router as tools_router
    from backend.artifacts import router as artifacts_router
except ModuleNotFoundError:
    from meta import init_meta_db
    from threads import router as threads_router
    from worldlines import router as wordlines_router
    from tools import router as tools_router
    from artifacts import router as artifacts_router

app = FastAPI(title="Agent Core Backend")


@app.on_event("startup")
def startup() -> None:
    init_meta_db()


app.include_router(threads_router)
app.include_router(wordlines_router)
app.include_router(tools_router)
app.include_router(artifacts_router)


@app.get("/")
async def root():
    return {"message": "Hello World"}
