import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

MONGODB_URL = os.getenv("MONGODB_URL")

if not MONGODB_URL:
    raise ValueError("MONGODB_URL environment variable is missing from .env file")

# Initialize the MongoDB client asynchronously
client = AsyncIOMotorClient(MONGODB_URL)

# Access the "cargo_manager" database
db = client.get_database("cargo_manager")

async def test_connection():
    """Helper function to test database connection on startup"""
    try:
        # Send a ping to confirm a successful connection
        await client.admin.command('ping')
        print("Successfully connected to MongoDB.")
    except Exception as e:
        print(f"Failed to connect to MongoDB: {e}")
