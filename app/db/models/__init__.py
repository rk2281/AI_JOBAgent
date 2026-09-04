"""Model registry.

Importing every model here guarantees that anything importing this
package registers all tables on Base.metadata. Alembic depends on
this to detect the full schema.
"""

from app.db.base import Base
from app.db.models.agent import AgentRun
from app.db.models.cv import CV, CVVersion, ExtractionStatus
from app.db.models.embedding import EmbeddingRun, EmbeddingStatus
from app.db.models.ingestion import IngestionReject, IngestionRun, IngestionStatus
from app.db.models.job import Job, JobSkill
from app.db.models.profile import Profile
from app.db.models.recommendation import (
    NOTIFICATION_TRIGGER_SOURCES,
    TRIGGER_SOURCE_MANUAL_TEST,
    TRIGGER_SOURCE_SCHEDULED,
    FeedbackAction,
    Notification,
    NotificationStatus,
    Recommendation,
    UserFeedback,
)
from app.db.models.scoring import ScoringRun, ScoringStatus
from app.db.models.skill import Skill
from app.db.models.user import OnboardingState, User, UserPreference

__all__ = [
    "NOTIFICATION_TRIGGER_SOURCES",
    "TRIGGER_SOURCE_MANUAL_TEST",
    "TRIGGER_SOURCE_SCHEDULED",
    "AgentRun",
    "Base",
    "CV",
    "CVVersion",
    "EmbeddingRun",
    "EmbeddingStatus",
    "ExtractionStatus",
    "FeedbackAction",
    "IngestionReject",
    "IngestionRun",
    "IngestionStatus",
    "Job",
    "JobSkill",
    "Notification",
    "NotificationStatus",
    "OnboardingState",
    "Profile",
    "Recommendation",
    "ScoringRun",
    "ScoringStatus",
    "Skill",
    "User",
    "UserFeedback",
    "UserPreference",
]
