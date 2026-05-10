from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    cozytouch_username: str
    cozytouch_password: str
    cozytouch_server: str = "atlantic_cozytouch"
    cozytouch_token: str | None = None

    # Where presets are persisted. Override with PRESETS_FILE env var.
    # Local dev default works out of the box; docker-compose overrides to /data/presets.json
    # and mounts ./data on the host so you can back up the JSON easily.
    presets_file: str = "./data/presets.json"

    log_level: str = "INFO"
    port: int = 8000


settings = Settings()  # type: ignore[call-arg]
