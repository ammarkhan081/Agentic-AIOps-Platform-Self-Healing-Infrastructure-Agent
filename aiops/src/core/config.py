from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LLM
    llm_provider: str = "groq"
    groq_api_key: str = ""
    groq_model_heavy: str = "llama3-70b-8192"
    groq_model_light: str = "llama3-8b-8192"
    openai_api_key: str = ""
    openai_model_heavy: str = "gpt-4o"
    openai_model_light: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"

    # LangSmith
    langchain_tracing_v2: str = "true"
    langchain_api_key: str = ""
    langchain_project: str = "ashia-aiops"
    langsmith_project_url: str = ""
    demo_video_url: str = ""
    public_deployment_url: str = ""

    # Database
    database_url: str = "postgresql://ashia:ashia_secret@localhost:5432/ashia_db"
    redis_url: str = "redis://localhost:6379"
    pinecone_api_key: str = ""
    pinecone_index_name: str = "ashia-incidents"
    pinecone_namespace: str = "production"

    # Observability stack
    prometheus_url: str = "http://localhost:9090"
    loki_url: str = "http://localhost:3100"
    jaeger_url: str = "http://localhost:16686"
    order_service_url: str = "http://localhost:8002"
    user_service_url: str = "http://localhost:8001"
    api_gateway_url: str = "http://localhost:8000"

    # Slack / API surface
    slack_webhook_url: str = ""
    frontend_base_url: str = "http://localhost:3000"
    api_base_url: str = "http://localhost:8080"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000,http://testserver"

    # JWT
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 15
    jwt_refresh_token_expire_days: int = 7

    # App
    app_env: str = "development"
    app_port: int = 8080
    log_level: str = "INFO"

    # Anomaly detection
    anomaly_zscore_threshold: float = 2.5
    anomaly_consecutive_readings: int = 3
    monitor_poll_interval_seconds: int = 30
    max_retry_count: int = 3
    hitl_timeout_minutes: int = 15
    auto_monitor_enabled: bool = True

    def get_cors_origins(self) -> List[str]:
        origins = [item.strip() for item in self.cors_origins.split(",") if item.strip()]
        return origins or ["http://localhost:3000", "http://127.0.0.1:3000", "http://testserver"]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
