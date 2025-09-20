from datetime import datetime
from domain.phrase import Phrase

class Quote:
    def __init__(self, phrases: list[Phrase], date: datetime):
        self.phrases = phrases
        self.date = date