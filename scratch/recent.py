import psycopg2, json, sys
sys.stdout.reconfigure(encoding='utf-8')
conn = psycopg2.connect('postgresql://postgres:postgres123@localhost:5432/evolution')
cur = conn.cursor()
cur.execute("""SELECT key, "pushName", "messageTimestamp" FROM "Message" WHERE "messageTimestamp" > 1780350000 ORDER BY "messageTimestamp" DESC LIMIT 20""")
rows = cur.fetchall()
print(f"Found {len(rows)} messages after timestamp 1780350000:")
for r in rows:
    key = r[0]
    from_me = key.get('fromMe', '?')
    remote = key.get('remoteJid', 'unknown')
    print(f"  ts={r[2]} | fromMe={from_me} | push={r[1]} | jid={remote}")
