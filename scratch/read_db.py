import psycopg2
import json

def main():
    try:
        conn = psycopg2.connect('postgresql://postgres:postgres123@localhost:5432/evolution')
        cur = conn.cursor()
        
        # Query total count
        cur.execute('SELECT COUNT(*) FROM "Message"')
        count = cur.fetchone()[0]
        print(f"Total messages in DB: {count}")
        
        # Query latest 5 messages
        cur.execute('SELECT id, key, "pushName", "messageTimestamp", "messageType", message FROM "Message" ORDER BY id DESC LIMIT 5')
        rows = cur.fetchall()
        for r in rows:
            print(f"ID: {r[0]}")
            print(f"Key: {r[1]}")
            print(f"PushName: {r[2]}")
            print(f"Timestamp: {r[3]}")
            print(f"MessageType: {r[4]}")
            print(f"Message: {json.dumps(r[5], indent=2, ensure_ascii=False) if r[5] else 'None'}")
            print("-" * 50)
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
