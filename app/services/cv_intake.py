"""Validating and storing uploaded CV files.

Nothing here knows about Telegram. The handler downloads the bytes;
this module decides whether they are acceptable and where they live.
That split is what lets the same logic serve a web upload later
without being rewritten.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)


# Day 4 extracts text from PDF and DOCX. Accepting anything else here
# would mean storing a file that the next stage cannot read, and
# discovering that a day later.
ALLOWED_EXTENSIONS: dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "docx",
}

# Both formats have a fixed signature in their first bytes. DOCX is a
# ZIP container, so it starts with the ZIP magic.
MAGIC_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "pdf": (b"%PDF-",),
    "docx": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
}


class CVValidationError(Exception):
    """The uploaded file is not a CV we can accept.

    The message is written to be shown to the user directly, so it
    explains what to do rather than what went wrong internally.
    """


@dataclass(frozen=True)
class StoredCV:
    """Where a validated CV ended up on disk."""

    file_type: str
    storage_path: str
    size_bytes: int


class CVIntakeService:
    """Validates CV uploads and writes them to the storage directory."""

    def __init__(self, storage_dir: str | None = None) -> None:
        self._storage_dir = Path(storage_dir or settings.cv_storage_dir)

    # -- validation -------------------------------------------------------

    def validate_metadata(
        self,
        file_name: str | None,
        size_bytes: int | None,
    ) -> str:
        """Check what Telegram tells us before downloading anything.

        Returns the normalized file type ("pdf" or "docx").

        This runs first precisely because it is cheap. Telegram reports
        the name and size in the update itself, so a 40 MB video can be
        rejected without a single byte crossing the network.
        """
        if not file_name:
            raise CVValidationError(
                "That file has no name, so I can't tell what format it is. "
                "Please send a PDF or DOCX."
            )

        extension = Path(file_name).suffix.lower()
        file_type = ALLOWED_EXTENSIONS.get(extension)

        if file_type is None:
            raise CVValidationError(
                f"I can't read {extension or 'that format'} files. "
                "Please send your CV as a PDF or DOCX."
            )

        if size_bytes is not None and size_bytes > settings.max_cv_size_bytes:
            actual_mb = size_bytes / (1024 * 1024)
            raise CVValidationError(
                f"That file is {actual_mb:.1f} MB, and my limit is "
                f"{settings.max_cv_size_mb} MB. If it's a scan, try "
                "exporting a text PDF instead — I can't read scans anyway."
            )

        if size_bytes is not None and size_bytes == 0:
            raise CVValidationError("That file is empty. Please try again.")

        return file_type

    def validate_content(self, file_type: str, data: bytes) -> None:
        """Check the bytes actually match the claimed format.

        A file called cv.pdf is not necessarily a PDF. The extension is
        a claim by whoever named the file; the magic bytes are evidence.
        Day 4's parser would fail confusingly on a mismatch, so it is
        caught here where the user is still in the conversation and can
        just send a different file.
        """
        signatures = MAGIC_SIGNATURES[file_type]

        if not any(data.startswith(signature) for signature in signatures):
            raise CVValidationError(
                f"That file is named like a {file_type.upper()} but its "
                "contents aren't one. Try re-exporting it and sending again."
            )

    # -- storage ----------------------------------------------------------

    def build_path(self, user_id: int, file_type: str) -> Path:
        """Choose the on-disk location for a new upload.

        The stored name is a UUID rather than the user's own file name.
        Two reasons: an uploaded name like "../../.env" would otherwise
        escape the storage directory, and two people both sending
        "resume.pdf" must not collide. The original name is kept in the
        cvs.file_name column, which is where it belongs.
        """
        directory = self._storage_dir / str(user_id)
        return directory / f"{uuid.uuid4().hex}.{file_type}"

    def save(self, user_id: int, file_type: str, data: bytes) -> StoredCV:
        """Validate the bytes and write them to disk."""
        self.validate_content(file_type, data)

        if len(data) > settings.max_cv_size_bytes:
            raise CVValidationError(
                f"That file is larger than {settings.max_cv_size_mb} MB."
            )

        path = self.build_path(user_id, file_type)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

        logger.info(
            "Stored CV for user_id=%s type=%s bytes=%s",
            user_id,
            file_type,
            len(data),
        )

        return StoredCV(
            file_type=file_type,
            storage_path=str(path),
            size_bytes=len(data),
        )

    def delete(self, user_id: int, storage_path: str) -> None:
        """Remove a stored CV file from disk.

        Deleting a `cvs` row does not delete the file it pointed to —
        the database and the filesystem are two separate stores, and
        only the caller knows when both should go. This exists so
        cleanup code (a dry-run test user, a future "delete my data"
        flow) can remove the file safely instead of reaching for
        shutil.rmtree directly.

        The resolved path must live inside this user's own
        subdirectory of the configured storage root, not just
        somewhere under the root — so a corrupted or spoofed
        storage_path can never be used to delete another user's file,
        or anything outside storage entirely. A missing file is a
        no-op rather than an error: the desired end state, the file
        being gone, already holds.
        """
        target = Path(storage_path).resolve()
        user_dir = (self._storage_dir / str(user_id)).resolve()

        if not target.is_relative_to(user_dir):
            raise ValueError(
                f"Refusing to delete {storage_path}: not inside the "
                f"storage directory for user {user_id} ({user_dir})."
            )

        target.unlink(missing_ok=True)
