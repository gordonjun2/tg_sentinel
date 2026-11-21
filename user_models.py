"""User survey/approval models shared by database.py and user_store.py."""

from dataclasses import dataclass
from enum import Enum
from datetime import datetime
from typing import Dict, List, Optional


class UserState(Enum):
    IDLE = "idle"
    IN_SURVEY = "in_survey"
    PENDING_APPROVAL = "pending_approval"
    PENDING_REJECTION = "pending_rejection"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass
class UserData:
    user_id: int
    username: Optional[str]
    state: UserState
    current_question: int
    answers: Dict[str, str]
    join_datetime: datetime
    invite_links: List[str] = None  # List of invite link IDs given to this user
    approval_group: Optional[str] = (
        None  # Which group the approval was for: "main" or "sub"
    )
    rejection_message_id: Optional[int] = (
        None  # Message ID for rejection reason request
    )
