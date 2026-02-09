
import sqlite3
import os
import datetime

OLD_DB = "../nexus/nexus.db"
NEW_DB = "nexus_history.db"

def migrate():
    if not os.path.exists(OLD_DB):
        print(f"❌ Old DB not found at {OLD_DB}")
        return

    print(f"🚀 Migrating from {OLD_DB} to {NEW_DB}...")

    # Connect to Old
    conn_old = sqlite3.connect(OLD_DB)
    curr_old = conn_old.cursor()
    
    # Connect to New
    conn_new = sqlite3.connect(NEW_DB)
    curr_new = conn_new.cursor()

    try:
        # Read all interactions
        curr_old.execute("SELECT timestamp, user_id, query, response FROM interactions")
        rows = curr_old.fetchall()
        
        print(f"Found {len(rows)} interactions to migrate.")
        
        count = 0
        for row in rows:
            ts_str, user_id, query, response = row
            
            # Parse timestamp (Old might be ISO string or other)
            # Assuming simple string for now, new DB expects string or timestamp
            # We'll just pass it through, or verify format.
            
            # 1. Insert User Message
            curr_new.execute("""
                INSERT INTO messages (date, chat_id, chat_title, sender_id, sender_name, username, message_text)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (ts_str, 0, "Migrated Chat", 0, "Old User", str(user_id), query))
            
            # 2. Insert Bot Response (if exists)
            if response:
                # Add 1 second to timestamp to maintain order if possible, or just same ts
                curr_new.execute("""
                    INSERT INTO messages (date, chat_id, chat_title, sender_id, sender_name, username, message_text)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (ts_str, 0, "Migrated Chat", 123456, "Nexus (Old)", "nexus_bot", response))
            
            count += 1
            
        conn_new.commit()
        print(f"✅ Successfully migrated {count} interactions ({count*2} messages created).")

    except Exception as e:
        print(f"❌ Migration failed: {e}")
    finally:
        conn_old.close()
        conn_new.close()

if __name__ == "__main__":
    migrate()
