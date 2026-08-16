from datetime import datetime
from gmail.client import GmailClient
from knowledge_base import db
from telegram_bot import notifications


def check_learning_opportunities(gmail: GmailClient, since: datetime):
    """
    Detects when the human replied to a tracked email thread
    and stores the response for future learning.
    """
    sent_messages = gmail.get_sent_messages(since)

    for sent in sent_messages:
        thread_id = sent["thread_id"]
        interaction = db.find_interaction_by_thread(thread_id)

        if interaction:
            db.update_interaction_response(interaction["_id"], sent["body"])
            notifications.notify_learning_opportunity(
                interaction["from"], interaction["subject"]
            )
            print(f"  [LEARN] Respuesta guardada para hilo: {interaction['subject'][:50]}")
