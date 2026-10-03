import certifi
from pymongo import MongoClient
from config.settings import MONGODB_URI, DB_NAME

_client = MongoClient(
    MONGODB_URI,
    tlsCAFile=certifi.where(),
    maxPoolSize=50,
    minPoolSize=0,
    serverSelectionTimeoutMS=5000,
    connectTimeoutMS=5000,
    socketTimeoutMS=30000,
)
db = _client[DB_NAME]
