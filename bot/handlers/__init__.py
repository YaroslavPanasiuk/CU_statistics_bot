from aiogram import Dispatcher
from bot.middlewares import VolunteerMiddleware
from . import admin, common


def register_handlers(dp: Dispatcher):
    # Outer, so the volunteer row is resolved before any router filter runs.
    dp.update.outer_middleware(VolunteerMiddleware())

    # Order is load-bearing: each of these routers ends in a catch-all.
    #
    # admin.router goes first so administration keeps working outside the
    # semester, and unavailable_router goes before the volunteer handlers so
    # that out-of-semester messages get an explanation instead of the
    # questionnaire. Both were previously impossible: unavailable_router was
    # never included at all, leaving BotAvailable as dead code.
    dp.include_router(admin.router)               # admin commands, always available
    dp.include_router(common.unavailable_router)   # outside the semester
    dp.include_router(common.registered_router)   # registered users
    dp.include_router(common.router)              # unregistered users
    dp.include_router(common.fallback_router)     # registered users, unknown command
