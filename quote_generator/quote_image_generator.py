from domain.quote import Quote

from PIL import Image, ImageDraw, ImageFont
import io

from quote_generator.quote_text_generator import QuoteTextGenerator

class QuoteImageGenerator:
    def __init__(self, quote_text_generator: QuoteTextGenerator):
        self.quote_text_generator = quote_text_generator

    def generate_quote_image(self, quote: Quote):
        # Create blank white image
        img = Image.new("RGB", (400, 200), color="white")
        draw = ImageDraw.Draw(img)

        # Try to load a proper font, fallback to default
        try:
            font = ImageFont.truetype("arial.ttf", 30)
        except:
            font = ImageFont.load_default()

        text = self.quote_text_generator.generate_quote(quote)

        # Get bounding box of text
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        # Center text on image
        position = ((img.width - text_width) // 2, (img.height - text_height) // 2)

        # Draw text
        draw.text(position, text, font=font, fill="black")

        # Save to buffer
        bio = io.BytesIO()
        bio.name = "quote.png"
        img.save(bio, "PNG")
        bio.seek(0)

        return bio