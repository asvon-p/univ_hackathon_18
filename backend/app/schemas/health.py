from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    app_env: str
    contract_version: str
    db: str
    db_error: str | None = None
    adapters: dict[str, str]
    gpu: str | None = None
