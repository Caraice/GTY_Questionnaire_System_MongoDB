from pymongo import MongoClient

from app.config import Settings
from database.schema import initialize

if __name__ == "__main__":
    settings = Settings.from_env()
    with MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=3000) as client:
        initialize(client[settings.mongodb_db])
    print(f"MongoDB collections and indexes ready: {settings.mongodb_db}")

