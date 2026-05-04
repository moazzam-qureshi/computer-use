"""Stores for the assistant: conversations, messages, audit log, system config."""
from __future__ import annotations

from typing import Any, Optional, List, Dict
from psycopg.types.json import Json

from storage.connection import Database


class ConversationStore:
    def __init__(self, db: Database):
        self._db = db

    def get_or_create(self, discord_user_id: str) -> int:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT conversation_id FROM assistant_conversations WHERE discord_user_id = %s",
                    (discord_user_id,),
                )
                row = cur.fetchone()
                if row is not None:
                    return row[0]
                cur.execute(
                    "INSERT INTO assistant_conversations (discord_user_id) VALUES (%s) RETURNING conversation_id",
                    (discord_user_id,),
                )
                return cur.fetchone()[0]

    def get_summary(self, conversation_id: int) -> Optional[str]:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT summary FROM assistant_conversations WHERE conversation_id = %s",
                    (conversation_id,),
                )
                row = cur.fetchone()
                return row[0] if row else None

    def set_summary(self, conversation_id: int, summary: str) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE assistant_conversations SET summary = %s, updated_at = now() WHERE conversation_id = %s",
                    (summary, conversation_id),
                )

    def touch(self, conversation_id: int) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE assistant_conversations SET updated_at = now() WHERE conversation_id = %s",
                    (conversation_id,),
                )


class MessageStore:
    def __init__(self, db: Database):
        self._db = db

    def append(
        self,
        conversation_id: int,
        *,
        role: str,
        content: str,
        tool_call_id: Optional[str] = None,
        tool_name: Optional[str] = None,
    ) -> int:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO assistant_messages
                        (conversation_id, role, content, tool_call_id, tool_name)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING message_id
                    """,
                    (conversation_id, role, content, tool_call_id, tool_name),
                )
                return cur.fetchone()[0]

    def recent(self, conversation_id: int, limit: int) -> List[Dict[str, Any]]:
        """Returns recent (non-archived) messages oldest-first."""
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT message_id, role, content, tool_call_id, tool_name, created_at
                    FROM assistant_messages
                    WHERE conversation_id = %s AND archived_at IS NULL
                    ORDER BY message_id DESC
                    LIMIT %s
                    """,
                    (conversation_id, limit),
                )
                rows = cur.fetchall()
        rows.reverse()
        return [
            {
                "message_id": r[0],
                "role": r[1],
                "content": r[2],
                "tool_call_id": r[3],
                "tool_name": r[4],
                "created_at": r[5],
            }
            for r in rows
        ]

    def count_active(self, conversation_id: int) -> int:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM assistant_messages WHERE conversation_id = %s AND archived_at IS NULL",
                    (conversation_id,),
                )
                return cur.fetchone()[0]

    def archive_oldest(self, conversation_id: int, n: int) -> List[int]:
        """Soft-archive the n oldest non-archived messages. Returns archived ids."""
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE assistant_messages SET archived_at = now()
                    WHERE message_id IN (
                        SELECT message_id FROM assistant_messages
                        WHERE conversation_id = %s AND archived_at IS NULL
                        ORDER BY message_id ASC LIMIT %s
                    )
                    RETURNING message_id
                    """,
                    (conversation_id, n),
                )
                return [r[0] for r in cur.fetchall()]


class AuditStore:
    def __init__(self, db: Database):
        self._db = db

    def record(
        self,
        *,
        conversation_id: Optional[int],
        tool_name: str,
        arguments: dict,
        result: dict,
        before_state: Optional[dict] = None,
        after_state: Optional[dict] = None,
    ) -> int:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO assistant_audit_log
                        (conversation_id, tool_name, arguments, result, before_state, after_state)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING audit_id
                    """,
                    (
                        conversation_id,
                        tool_name,
                        Json(arguments),
                        Json(result),
                        Json(before_state) if before_state is not None else None,
                        Json(after_state) if after_state is not None else None,
                    ),
                )
                return cur.fetchone()[0]

    def last_for_conversation(self, conversation_id: int) -> Optional[Dict[str, Any]]:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT audit_id, tool_name, arguments, result, before_state, after_state, created_at
                    FROM assistant_audit_log
                    WHERE conversation_id = %s
                    ORDER BY audit_id DESC
                    LIMIT 1
                    """,
                    (conversation_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                return {
                    "audit_id": row[0],
                    "tool_name": row[1],
                    "arguments": row[2],
                    "result": row[3],
                    "before_state": row[4],
                    "after_state": row[5],
                    "created_at": row[6],
                }


class SystemConfigStore:
    def __init__(self, db: Database):
        self._db = db

    def get(self, key: str) -> Optional[Any]:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM system_config WHERE key = %s", (key,))
                row = cur.fetchone()
                return row[0] if row else None

    def set(self, key: str, value: Any) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO system_config (key, value, updated_at)
                    VALUES (%s, %s, now())
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
                    """,
                    (key, Json(value)),
                )
