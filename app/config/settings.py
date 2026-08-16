from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    env: str = "local"
    host: str = "0.0.0.0"
    port: int = 4226
    log_level: str = "INFO"

    mongodb_url: str = "mongodb://localhost:27017"
    mongodb_database: str = "clinic_platform"

    # Clinic-wide defaults (seeded branches carry their own copies)
    clinic_timezone: str = "Asia/Kolkata"
    clinic_currency: str = "INR"

    # Booking rules
    min_booking_notice_minutes: int = 30
    availability_horizon_days: int = 21
    # Sessions younger than this are offered for dropped-call resume
    session_resume_window_minutes: int = 30
    # Unanswered outbound calls younger than this are treated as pending callbacks
    callback_window_hours: int = 48

    # LiveKit (web calls + SIP dispatch)
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""
    worker_agent_name: str = "clinic-voice-agent"

    # Worker -> platform auth. Empty means open (local dev only).
    internal_service_key: str = ""

    # PMS write-back target: "mock" (built-in Cliniko-shaped API) or "cliniko"
    # (a real Cliniko account via its REST API)
    pms_provider: str = "mock"

    # Real Cliniko
    cliniko_api_key: str = ""
    # Shard comes from the API key suffix (e.g. -au4 -> api.au4.cliniko.com)
    cliniko_base_url: str = ""
    cliniko_user_agent: str = "Apollo Clinic Voice Agent (abhisek@usesidecar.com)"

    # Mock PMS
    pms_base_url: str = "http://localhost:4226/pms/api/v1"
    pms_api_key: str = "pms_test_key"
    # 0.0 - 1.0 probability that a PMS write returns 503 (chaos testing)
    pms_chaos_failure_rate: float = 0.0
    pms_retry_interval_seconds: int = 20
    pms_max_retry_attempts: int = 10

    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # Eval-harness support: allows POST /api/v1/admin/reset (auto-on for env=local)
    allow_test_reset: bool = False

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
