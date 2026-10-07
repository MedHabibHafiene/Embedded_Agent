"""Router modules: one file per endpoint group, all wired in app/main.py."""

from app.routers import build, chat, flash, generate, health, jobs

__all__ = ["build", "chat", "flash", "generate", "health", "jobs"]
