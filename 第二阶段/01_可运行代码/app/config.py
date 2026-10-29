import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    mongodb_uri: str
    mongodb_db: str
    session_hours: int = 24

    @classmethod
    def from_env(cls):
        return cls(
            mongodb_uri=os.getenv("MONGODB_URI", "mongodb://127.0.0.1:27019"),
            mongodb_db=os.getenv("MONGODB_DB", "survey_stage1"),
        )

