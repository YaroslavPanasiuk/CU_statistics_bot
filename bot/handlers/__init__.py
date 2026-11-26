from aiogram import Dispatcher
from . import admin, common


def register_handlers(dp: Dispatcher):
    # Order is load-bearing: each of these routers ends in a catch-all.
    dp.include_router(common.registered_router)   # registered users' own commands
    dp.include_router(admin.router)               # admin commands
    dp.include_router(common.router)              # unregistered users
    dp.include_router(common.fallback_router)     # registered users, unknown command
