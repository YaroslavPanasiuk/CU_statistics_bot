import os
import json
from dotenv import load_dotenv

load_dotenv()

class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    ADMIN_IDS = os.getenv("ADMIN_IDS")
    DATABASE_URL = os.getenv("DATABASE_URL")
    GOOGLE_SHEET_CONFIG_URL = os.getenv("GOOGLE_SHEET_CONFIG_URL")
    GOOGLE_SHEET_URL = os.getenv("GOOGLE_SHEET_URL")
    GOOGLE_CREDS = json.loads(os.getenv("GOOGLE_CREDS_JSON", "{}"))
    LOCALE = "uk"
    TIMEZONE = os.getenv("TZ")
    
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN not found in .env file")

config = Config()