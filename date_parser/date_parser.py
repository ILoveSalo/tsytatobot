from datetime import datetime

class DateParser:
    def parse_string_to_date(self, date: str):
        """Convert user input → datetime"""
        if date.lower().strip() in ("today", "📅 today"):
            return datetime.today()
        return datetime.strptime(date, "%d.%m.%Y")

    def parse_date_to_string(self, date: datetime):
        """Convert datetime → \"dd.mm.yyyy\""""
        return date.strftime('%d.%m.%Y')