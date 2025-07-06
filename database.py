import sqlite3
import json
from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from datetime import datetime, timezone


class UserState(Enum):
    IDLE = "idle"
    IN_SURVEY = "in_survey"
    PENDING_APPROVAL = "pending_approval"
    PENDING_REJECTION = "pending_rejection"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass
class TranscriptionStatus:
    file_path: str
    percentage: float
    start_time: datetime
    is_completed: bool = False
    is_extracting_insights: bool = False
    is_fully_completed: bool = False
    error: Optional[str] = None


@dataclass
class UserData:
    user_id: int
    username: Optional[str]
    state: UserState
    current_question: int
    answers: Dict[str, str]
    join_datetime: datetime
    invite_links: List[
        str] = None  # List of invite link IDs given to this user
    rejection_message_id: Optional[
        int] = None  # Message ID for rejection reason request


class Database:

    def __init__(self, db_path: str = "bot_data.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        """Initialize the database and create tables if they don't exist."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # Create users table if not exists
            cursor.execute('''
                SELECT name FROM sqlite_master WHERE type='table' AND name='users'
            ''')
            table_exists = cursor.fetchone() is not None

            if not table_exists:
                # Create users table
                cursor.execute('''
                    CREATE TABLE users (
                        user_id INTEGER PRIMARY KEY,
                        username TEXT,
                        state TEXT,
                        current_question INTEGER,
                        answers TEXT,
                        join_datetime TEXT,
                        invite_links TEXT,
                        rejection_message_id INTEGER
                    )
                ''')
            else:
                # Check and add columns as before
                cursor.execute('PRAGMA table_info(users)')
                columns = [col[1] for col in cursor.fetchall()]
                if 'join_datetime' not in columns:
                    cursor.execute('''
                        ALTER TABLE users 
                        ADD COLUMN join_datetime TEXT 
                        DEFAULT CURRENT_TIMESTAMP
                    ''')
                if 'invite_links' not in columns:
                    cursor.execute('''
                        ALTER TABLE users 
                        ADD COLUMN invite_links TEXT 
                        DEFAULT '[]'
                    ''')
                if 'rejection_message_id' not in columns:
                    cursor.execute('''
                        ALTER TABLE users 
                        ADD COLUMN rejection_message_id INTEGER 
                        DEFAULT NULL
                    ''')

            # Create transcription_status table if not exists
            cursor.execute('''
                SELECT name FROM sqlite_master WHERE type='table' AND name='transcription_status'
            ''')
            table_exists = cursor.fetchone() is not None

            if not table_exists:
                cursor.execute('''
                    CREATE TABLE transcription_status (
                        file_path TEXT PRIMARY KEY,
                        percentage REAL,
                        start_time TEXT,
                        is_completed BOOLEAN,
                        is_extracting_insights BOOLEAN,
                        is_fully_completed BOOLEAN,
                        error TEXT
                    )
                ''')
            else:
                # Check if is_extracting_insights column exists
                cursor.execute('PRAGMA table_info(transcription_status)')
                columns = [col[1] for col in cursor.fetchall()]
                if 'is_extracting_insights' not in columns:
                    cursor.execute('''
                        ALTER TABLE transcription_status 
                        ADD COLUMN is_extracting_insights BOOLEAN 
                        DEFAULT 0
                    ''')
                if 'is_fully_completed' not in columns:
                    cursor.execute('''
                        ALTER TABLE transcription_status 
                        ADD COLUMN is_fully_completed BOOLEAN 
                        DEFAULT 0
                    ''')

            conn.commit()

    def _user_data_from_row(self, row: tuple) -> UserData:
        """Convert a database row to UserData object."""
        user_id, username, state, current_question, answers, join_datetime, invite_links, rejection_message_id = row
        return UserData(
            user_id=user_id,
            username=username,
            state=UserState(state),
            current_question=current_question,
            answers=json.loads(answers) if answers else {},
            join_datetime=datetime.fromisoformat(join_datetime)
            if join_datetime else datetime.now(timezone.utc),
            invite_links=json.loads(invite_links) if invite_links else [],
            rejection_message_id=rejection_message_id)

    def get_user(self, user_id: int) -> Optional[UserData]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT user_id, username, state, current_question, answers, join_datetime, invite_links, rejection_message_id FROM users WHERE user_id = ?',
                (user_id, ))
            row = cursor.fetchone()
            return self._user_data_from_row(row) if row else None

    def create_user(self, user_id: int, username: Optional[str]) -> UserData:
        user = UserData(user_id=user_id,
                        username=username,
                        state=UserState.IDLE,
                        current_question=0,
                        answers={},
                        join_datetime=datetime.now(timezone.utc),
                        invite_links=[],
                        rejection_message_id=None)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                INSERT INTO users (user_id, username, state, current_question, answers, join_datetime, invite_links, rejection_message_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (user.user_id, user.username, user.state.value,
                 user.current_question, json.dumps(user.answers),
                 user.join_datetime.isoformat(), json.dumps(
                     user.invite_links), user.rejection_message_id))
            conn.commit()
        return user

    def update_user(self, user_data: UserData) -> None:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                UPDATE users
                SET username = ?, state = ?, current_question = ?, answers = ?, join_datetime = ?, invite_links = ?, rejection_message_id = ?
                WHERE user_id = ?
                ''',
                (user_data.username, user_data.state.value,
                 user_data.current_question, json.dumps(
                     user_data.answers), user_data.join_datetime.isoformat(),
                 json.dumps(user_data.invite_links),
                 user_data.rejection_message_id, user_data.user_id))
            conn.commit()

    def get_pending_requests(self) -> List[UserData]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT user_id, username, state, current_question, answers, join_datetime, invite_links, rejection_message_id
                FROM users
                WHERE state = ?
                ''', (UserState.PENDING_APPROVAL.value, ))
            return [self._user_data_from_row(row) for row in cursor.fetchall()]

    def get_all_users(self) -> List[UserData]:
        """Retrieve all users from the database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT user_id, username, state, current_question, answers, join_datetime, invite_links, rejection_message_id FROM users'
            )
            return [self._user_data_from_row(row) for row in cursor.fetchall()]

    def get_active_transcription(self) -> Optional[TranscriptionStatus]:
        """Get the currently active transcription if any."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT file_path, percentage, start_time, is_completed, is_extracting_insights, is_fully_completed, error
                FROM transcription_status
                WHERE is_fully_completed = 0 AND error IS NULL
                ORDER BY start_time DESC
                LIMIT 1
            ''')
            row = cursor.fetchone()

            if row:
                file_path, percentage, start_time, is_completed, is_extracting_insights, is_fully_completed, error = row
                return TranscriptionStatus(
                    file_path=file_path,
                    percentage=percentage,
                    start_time=datetime.fromisoformat(start_time),
                    is_completed=bool(is_completed),
                    is_extracting_insights=bool(is_extracting_insights),
                    is_fully_completed=bool(is_fully_completed),
                    error=error)
            return None

    def start_transcription(self, file_path: str) -> None:
        """Start a new transcription task."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                INSERT INTO transcription_status (file_path, percentage, start_time, is_completed, is_extracting_insights, is_fully_completed, error)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (file_path, 0.0, datetime.now(
                    timezone.utc).isoformat(), False, False, False, None))
            conn.commit()

    def start_insight_extraction(self, file_path: str) -> None:
        """Mark a transcription as starting insight extraction."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                UPDATE transcription_status
                SET is_extracting_insights = 1
                WHERE file_path = ?
            ''', (file_path, ))
            conn.commit()

    def complete_insight_extraction(self, file_path: str) -> None:
        """Mark insight extraction as complete and set as fully completed."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                UPDATE transcription_status
                SET is_extracting_insights = 0, is_completed = 1, is_fully_completed = 1
                WHERE file_path = ?
            ''', (file_path, ))
            conn.commit()

    def update_transcription_progress(self, file_path: str,
                                      percentage: float) -> None:
        """Update the progress of a transcription task."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                UPDATE transcription_status
                SET percentage = ?
                WHERE file_path = ? AND is_completed = 0
            ''', (percentage, file_path))
            conn.commit()

    def complete_transcription(self,
                               file_path: str,
                               error: Optional[str] = None) -> None:
        """Mark a transcription as complete or failed."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                UPDATE transcription_status
                SET is_completed = 1, error = ?
                WHERE file_path = ?
            ''', (error, file_path))
            conn.commit()


# Ensure the data directory exists
Path(Path(__file__).parent / "data").mkdir(exist_ok=True)

# Global instance with database in the data directory
db = Database(str(Path(__file__).parent / "data" / "bot_data.db"))
