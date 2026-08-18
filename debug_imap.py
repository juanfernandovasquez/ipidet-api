import imaplib, os
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import email as email_lib
from dotenv import load_dotenv
load_dotenv()

conn = imaplib.IMAP4_SSL('imap.gmail.com', 993)
conn.login(os.getenv('GMAIL_ADDRESS'), os.getenv('GMAIL_APP_PASSWORD'))
conn.select('INBOX')

now = datetime.now(timezone.utc)
since = now - timedelta(minutes=10)
search_since = since - timedelta(days=1)
date_str = search_since.strftime('%d-%b-%Y')

print(f'Ahora UTC:    {now}')
print(f'Filtro desde: {since}')
print(f'IMAP SINCE:   {date_str}')

_, data = conn.search(None, f'UNSEEN SINCE {date_str}')
ids = data[0].split()
print(f'\nEmails UNSEEN SINCE {date_str}: {len(ids)}')

for uid in ids:
    # BODY.PEEK[] no marca como leido
    _, msg_data = conn.fetch(uid, '(BODY.PEEK[])')
    raw = msg_data[0][1]
    msg = email_lib.message_from_bytes(raw)
    date_hdr = msg.get('Date', '')
    try:
        received_at = parsedate_to_datetime(date_hdr).astimezone(timezone.utc)
        pasa = received_at >= since
    except Exception as e:
        received_at = f'ERROR: {e}'
        pasa = False

    print(f'\n  UID: {uid.decode()}')
    print(f'  From:        {msg.get("From","")[:60]}')
    print(f'  Subject:     {msg.get("Subject","")[:60]}')
    print(f'  Date hdr:    {date_hdr}')
    print(f'  received_at: {received_at}')
    print(f'  Pasa filtro: {pasa}')

# Ver cuantos UNSEEN hay en total vs SINCE
_, d_all = conn.search(None, 'UNSEEN')
print(f'\nTotal UNSEEN en inbox: {len(d_all[0].split())}')

# Ver los emails de juan especificamente
_, d_juan = conn.search(None, 'UNSEEN FROM "juanvasquezvergara"')
print(f'UNSEEN de juanvasquezvergara: {len(d_juan[0].split())}')
if d_juan[0].split():
    for uid in d_juan[0].split():
        _, f = conn.fetch(uid, '(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])')
        print(f'  UID {uid.decode()}: {f[0][1].decode("utf-8","ignore").strip()}')

conn.logout()
