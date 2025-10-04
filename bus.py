# bus.py
class MessageBus:
    def __init__(self): self.subs = {}
    def subscribe(self, topic, handler):
        self.subs.setdefault(topic, []).append(handler)
    def publish(self, topic, payload):
        for h in self.subs.get(topic, []): h(payload)

TOPIC_PURCHASE_REQ   = "purchase.request"
TOPIC_PURCHASE_RES   = "purchase.result"
TOPIC_RESTOCK_REQ    = "restock.request"
TOPIC_RESTOCK_DONE   = "restock.done"
