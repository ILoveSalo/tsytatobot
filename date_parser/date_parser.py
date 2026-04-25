from datetime import datetime


class DateParser:
    def parse_string_to_date(self, date: str) -> datetime:
        """Convert user input to datetime."""
        if not date:
            raise ValueError("Date text is empty")

        normalized = date.strip().casefold()
        if normalized in ("today", "📅 today"):
            return datetime.today()
        return datetime.strptime(date.strip(), "%d.%m.%Y")

    def parse_date_to_string(self, date: datetime) -> str:
        """Convert datetime to dd.mm.yyyy."""
        return date.strftime("%d.%m.%Y")
