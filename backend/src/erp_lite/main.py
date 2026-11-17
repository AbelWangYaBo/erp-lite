"""erp-lite 后端入口：最小 FastAPI app（S0），模型与路由由后续阶段补充。"""

from fastapi import FastAPI

app = FastAPI(title="erp-lite", version="0.1.0")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
