"""Model registry.

Importing every model here guarantees that anything importing this
package registers all tables on Base.metadata. Alembic depends on
this to detect the full schema.
"""

from app.db.base import Base
from app.db.models.cv import CV, CVVersion, ExtractionStatus
from app.db.models.job import Job, JobSkill
from app.db.models.profile import Profile
from app.db.models.recommendation import (
    FeedbackAction,
    Notification,
    NotificationStatus,
    Recommendation,
    UserFeedback,
)
from app.db.models.skill import Skill
from app.db.models.user import OnboardingState, User, UserPreference

__all__ = [
    "Base",
    "CV",
    "CVVersion",
    "ExtractionStatus",
    "FeedbackAction",
    "Job",
    "JobSkill",
    "Notification",
    "NotificationStatus",
    "OnboardingState",
    "Profile",
    "Recommendation",
    "Skill",
    "User",
    "UserFeedback",
    "UserPreference",
]
