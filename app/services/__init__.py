"""Business logic.

Services own the rules. They take a database session, call
repositories, and return plain data. They import nothing from
app.bot — the dependency runs one way only, which is what keeps the
same logic usable from a web endpoint or a test.
"""

from app.services.cv_extraction import ExtractionResult, extract_cv
from app.services.cv_intake import CVIntakeService, CVValidationError
from app.services.cv_text import UnsupportedCVFormat, extract_raw_text
from app.services.onboarding import DocumentOutcome, OnboardingService
from app.services.profile import ProfileService
from app.services.profile_view import ProfileSnapshot, render_profile
from app.services.replies import BotReply, Button

__all__ = [
    "BotReply",
    "Button",
    "CVIntakeService",
    "CVValidationError",
    "DocumentOutcome",
    "ExtractionResult",
    "OnboardingService",
    "ProfileService",
    "ProfileSnapshot",
    "UnsupportedCVFormat",
    "extract_cv",
    "extract_raw_text",
    "render_profile",
]
