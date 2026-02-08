from fastapi import FastAPI
from meta import init_meta_db

app = FastAPI(title="Agent Core Backend")


@app.on_event("startup")
def startup() -> None:
    init_meta_db()


@app.get("/")
async def root():
    return {"message": "Hello World"}
