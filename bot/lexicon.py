import gspread
import random
import re
from bot.config import config
from aiogram.filters import Filter
from aiogram.types import Message

class Lexicon:
    START_WELCOME = "Welcome!"
    ASK_FULLNAME = "Enter your name:"
    SUCCESS_ACTION = "Done!"

    @classmethod
    def load_from_sheet(cls):
        try:
            gc = gspread.service_account_from_dict(config.GOOGLE_CREDS)
            sh = gc.open_by_url(config.GOOGLE_SHEET_CONFIG_URL)
            worksheet = sh.get_worksheet(0)
            
            records = worksheet.get_all_values()
            
            for row in records:
                if len(row) >= 2:
                    raw_key, value = row[0], row[1]
                    clean_key = re.sub(r'\W+', '_', raw_key).strip('_').upper()
                    
                    if clean_key:
                        setattr(cls, clean_key, value)
        except Exception as e:
            print(f"Failed to load strings from Google Sheets: {e}")

def select_random_line(key):
    value = getattr(Lexicon, key)
    return random.choice(value.split(';;'))

class LexiconFilter(Filter):
    def __init__(self, key: str):
        self.key = key

    async def __call__(self, message: Message) -> bool:
        expected_text = getattr(Lexicon, self.key, None)
        return message.text == expected_text