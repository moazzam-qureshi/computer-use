"""
Reset every job's apply_status back to 'pending' so the apply driver can
re-pick any of them.

Usage:
    uv run reset_jobs.py
"""
from __future__ import annotations

import db


def main() -> None:
    conn = db.connect()
    db.init_schema(conn)
    cur = conn.execute(
        """
        UPDATE jobs
        SET
            apply_status = 'pending',
            apply_status_changed_at = datetime('now'),
            apply_status_history = json_set(
                COALESCE(apply_status_history, '[]'),
                '$[#]',
                json_object('status', 'pending', 'at', datetime('now'))
            )
        WHERE apply_status IN ('awaiting_review', 'applied', 'skipped', 'failed')
        """
    )
    conn.commit()
    print(f"Reset {cur.rowcount} job(s) back to apply_status='pending'.")

    # Show counts per status after reset
    counts = conn.execute(
        "SELECT apply_status, count(*) AS n FROM jobs GROUP BY apply_status ORDER BY apply_status"
    ).fetchall()
    print("Current apply_status distribution:")
    for row in counts:
        print(f"  {row['apply_status']}: {row['n']}")
    conn.close()


if __name__ == "__main__":
    main()
