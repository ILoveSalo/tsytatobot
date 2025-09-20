from date_parser.date_parser import DateParser
from domain.quote import Quote

class QuoteTextGenerator:
    def __init__(self, date_parser: DateParser):
        self.date_parser = date_parser

    def get_unique_names(self, quote: Quote):
        """Collect unique speaker names in a quote"""
        return {phrase.speaker.name for phrase in quote.phrases}

    def generate_tags(self, quote: Quote):
        """Generate hashtags for all speakers in a quote"""
        return " ".join(f"#{speaker}" for speaker in self.get_unique_names(quote))

    def generate_quote(self, quote: Quote):
        """Format quote text for preview/posting"""
        if len(quote.phrases) == 1:
            # Single phrase → "text" - speaker, date + tags
            return f"\"{quote.phrases[0].text}\" - {quote.phrases[0].speaker.name}, {self.date_parser.parse_date_to_string(quote.date)}\n"

        # Multiple phrases → dialogue format
        result = "".join(f"{phrase.speaker.name}: {phrase.text}\n" for phrase in quote.phrases)
        result += f"{self.date_parser.parse_date_to_string(quote.date)}\n"
        return result

    def generate_quote_with_tags(self, quote: Quote):
        """Generate quote text with tags"""
        result = self.generate_quote(quote)
        result += f"\n{self.generate_tags(quote)}"
        return result