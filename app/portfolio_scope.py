"""Namespaced portfolio state over the existing shared LINE delivery store."""
class ScopedStore:
    def __init__(self, store, identity):
        self.store, self.identity = store, identity

    def __getattr__(self, name):
        return getattr(self.store, name)

    def key(self, key):
        if self.identity == 'main' or key.startswith(('web-week:', 'research:')):
            return key
        return key+':portfolio:'+self.identity

    def get(self, key, default=''):
        if key in {'holding_draft', 'dca_updated', 'dca_updated_date'} or key.startswith('brief-wait:'):
            key = self.key(key)
        return self.store.get(key, default)

    def set(self, key, value):
        if key in {'holding_draft', 'dca_updated', 'dca_updated_date'} or key.startswith('brief-wait:'):
            key = self.key(key)
        return self.store.set(key, value)

    def kind(self, kind):
        return kind if kind in {'news_weekly', 'research_alert'} else self.key(kind)

    def brief(self, key=None, kind=None):
        return self.store.brief(key=self.key(key) if key else None, kind=self.kind(kind) if kind else None)

    def save_brief(self, key, kind, at, message, payload, ai_used):
        return self.store.save_brief(self.key(key), self.kind(kind), at, message, payload, ai_used)

    def enqueue(self, key, *args, **kwargs):
        return self.store.enqueue(self.key(key), *args, **kwargs)

    def cancel_schedule(self, key):
        return self.store.cancel_schedule(self.key(key))
