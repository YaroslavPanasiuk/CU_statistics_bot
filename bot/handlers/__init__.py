from aiogram import Dispatcher
from . import admin, common

def register_handlers(dp: Dispatcher):
    dp.include_router(common.register_router)
    dp.include_router(common.router)
    dp.include_router(admin.router)