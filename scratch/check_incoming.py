import psycopg2
import json
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except:
        pass

def main():
    try:
        conn = psycopg2.connect('postgresql://postgres:postgres123@localhost:5432/evolution')
        cur = conn.cursor()
        
        # Buscar mensagens privadas recebidas (fromMe = false, remoteJid com @s.whatsapp.net)
        cur.execute("""
            SELECT id, key, "pushName", "messageTimestamp", "messageType", message 
            FROM "Message" 
            WHERE key::text LIKE '%"fromMe": false%'
              AND key::text LIKE '%@s.whatsapp.net%'
              AND key::text NOT LIKE '%@g.us%'
            ORDER BY "messageTimestamp" DESC 
            LIMIT 10
        """)
        rows = cur.fetchall()
        
        if not rows:
            print("❌ Nenhuma mensagem privada recebida encontrada!")
            print("\nTentando busca alternativa...")
            cur.execute("""
                SELECT id, key, "pushName", "messageTimestamp", "messageType" 
                FROM "Message" 
                WHERE key::text LIKE '%false%'
                ORDER BY "messageTimestamp" DESC 
                LIMIT 10
            """)
            rows2 = cur.fetchall()
            for r in rows2:
                key = r[1] if isinstance(r[1], dict) else json.loads(r[1]) if r[1] else {}
                print(f"  Push: {r[2]} | fromMe: {key.get('fromMe')} | remoteJid: {key.get('remoteJid')} | Type: {r[4]}")
        else:
            print(f"✅ Encontradas {len(rows)} mensagens privadas recebidas:\n")
            for r in rows:
                key = r[1] if isinstance(r[1], dict) else json.loads(r[1]) if r[1] else {}
                msg = r[5] if isinstance(r[5], dict) else json.loads(r[5]) if r[5] else {}
                text = msg.get("conversation", msg.get("extendedTextMessage", {}).get("text", ""))
                print(f"De: {key.get('remoteJid')} ({r[2]})")
                print(f"Texto: {text[:100] if text else '[sem texto]'}")
                print(f"Timestamp: {r[3]}")
                print("-" * 50)
                
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
