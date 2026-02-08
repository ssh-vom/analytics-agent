from fastapi import FastAPI

app = FastAPI()


@app.post("/api/threads")
async def threads():
    pass
