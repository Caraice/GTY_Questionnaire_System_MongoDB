import os
import sys

from pymongo import MongoClient
from pymongo.errors import PyMongoError

try:
    with MongoClient(os.getenv("MONGODB_URI", "mongodb://127.0.0.1:27019"), serverSelectionTimeoutMS=800) as client:
        client.admin.command("ping")
except PyMongoError:
    sys.exit(1)

