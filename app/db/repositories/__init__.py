"""Repositories: the only place that writes SQL against the models.

Services call repositories. Handlers call services. Nothing calls
SQLAlchemy directly from a Telegram handler — that is the rule this
layer exists to enforce.
"""

from app.db.repositories.cv import CVRepository
from app.db.repositories.profile import ProfileRepository
from app.db.repositories.skill import SkillRepository
from app.db.repositories.user import UserRepository

__all__ = ["CVRepository", "ProfileRepository", "SkillRepository", "UserRepository"]
