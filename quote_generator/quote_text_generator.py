import re

from date_parser.date_parser import DateParser
from domain.quote import Quote


class QuoteTextGenerator:
    def __init__(self, date_parser: DateParser):
        self.date_parser = date_parser

    def get_unique_names(self, quote: Quote) -> list[str]:
        """Collect unique speaker names in quote order."""
        seen: set[str] = set()
        unique: list[str] = []
        for phrase in quote.phrases:
            if not phrase.speaker:
                continue
            name = phrase.speaker.name
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            unique.append(name)
        return unique

    def _name_to_hashtag(self, name: str) -> str:
        normalized = re.sub(r"\s+", "_", name.strip())
        normalized = re.sub(r"[^\w]", "", normalized, flags=re.UNICODE)
        return f"#{normalized}" if normalized else ""

    def generate_tags(self, quote: Quote) -> str:
        """Generate hashtags for all speakers in a quote."""
        tags = [self._name_to_hashtag(name) for name in self.get_unique_names(quote)]
        return " ".join(tag for tag in tags if tag)

    def generate_quote(self, quote: Quote) -> str:
        if len(quote.phrases) == 1:
            return quote.phrases[0].text

        result = "".join(f"{phrase.speaker.name}: {phrase.text}\n" for phrase in quote.phrases)
        return f"{result}\n"

    def generate_quote_with_name(self, quote: Quote) -> str:
        if len(quote.phrases) == 1:
            return f'"{self.generate_quote(quote)}" - {quote.phrases[0].speaker.name}, '

        return self.generate_quote(quote)

    def generate_quote_with_date(self, quote: Quote) -> str:
        result = self.generate_quote_with_name(quote)
        result += f"{self.date_parser.parse_date_to_string(quote.date)}\n"
        return result

    def generate_quote_with_tags(self, quote: Quote) -> str:
        result = self.generate_quote_with_date(quote)
        tags = self.generate_tags(quote)
        if tags:
            result += f"\n{tags}"
        return result
