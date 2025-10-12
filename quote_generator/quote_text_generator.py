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
        if len(quote.phrases) == 1:
            # Single phrase → "text" - speaker, date + tags
            return f"{quote.phrases[0].text}"

        # Multiple phrases → dialogue format
        result = "".join(f"{phrase.speaker.name}: {phrase.text}\n" for phrase in quote.phrases)
        result += "\n"
        return result

    def generate_quote_with_name(self, quote: Quote):
        if len(quote.phrases) == 1:
            # Single phrase → "text" - speaker, date + tags
            return f"\"{self.generate_quote(quote)}\" - {quote.phrases[0].speaker.name}, "

        return self.generate_quote(quote)

    def generate_quote_with_date(self, quote: Quote):
        """Format quote text for preview/posting"""
        result = self.generate_quote_with_name(quote)
        result += f"{self.date_parser.parse_date_to_string(quote.date)}\n"
        return result

    def generate_quote_with_tags(self, quote: Quote):
        """Generate quote text with tags"""
        result = self.generate_quote_with_date(quote)
        result += f"\n{self.generate_tags(quote)}"
        return result