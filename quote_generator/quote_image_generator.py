import io
import math
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from date_parser.date_parser import DateParser
from domain.quote import Quote
from quote_generator.quote_text_generator import QuoteTextGenerator


class QuoteImageGenerator:
    CANVAS_WIDTH = 1100
    CANVAS_HEIGHT = 512

    def __init__(self, quote_text_generator: QuoteTextGenerator, date_parser: DateParser):
        self.quote_text_generator = quote_text_generator
        self.date_parser = date_parser

        assets_dir = Path("assets")
        self._font_path_main = str(assets_dir / "SourceSans3-BoldItalic.ttf")
        self._font_path_secondary = str(assets_dir / "Gabriela-Regular.ttf")

        self._quote_sign_img = self._safe_open_rgba(assets_dir / "quote-sign.png")
        self._scroll_img = self._safe_open_rgba(assets_dir / "scroll.png")
        self._base_gradient = self._create_radial_gradient(
            (self.CANVAS_WIDTH, self.CANVAS_HEIGHT),
            inner_color=(44, 211, 189, 255),
            outer_color=(36, 129, 117, 255),
        )

    def _safe_open_rgba(self, path: Path) -> Image.Image:
        return Image.open(path).convert("RGBA")

    def _load_font(self, font_path: str, font_size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        try:
            return ImageFont.truetype(font_path, font_size)
        except OSError:
            return ImageFont.load_default()

    def _create_radial_gradient(self, size: tuple[int, int], inner_color: tuple[int, int, int, int], outer_color: tuple[int, int, int, int]) -> Image.Image:
        width, height = size
        center_x, center_y = width // 2, height // 2
        max_radius = math.sqrt(center_x ** 2 + center_y ** 2)

        gradient = Image.new("RGBA", (width, height), outer_color)
        draw = ImageDraw.Draw(gradient)

        for y in range(height):
            for x in range(width):
                dx = x - center_x
                dy = y - center_y
                dist = min(1, math.sqrt(dx * dx + dy * dy) / max_radius)

                r = int(inner_color[0] + (outer_color[0] - inner_color[0]) * dist)
                g = int(inner_color[1] + (outer_color[1] - inner_color[1]) * dist)
                b = int(inner_color[2] + (outer_color[2] - inner_color[2]) * dist)
                a = int(inner_color[3] + (outer_color[3] - inner_color[3]) * dist)
                draw.point((x, y), (r, g, b, a))

        return gradient

    def crop_transparency(self, image: Image.Image) -> Image.Image:
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        bbox = image.getbbox()
        if bbox:
            return image.crop(bbox)
        return image

    def add_main_sticker_to_canvas(self, image_file: bytes, height: int, canvas: Image.Image) -> Image.Image:
        sticker_img = Image.open(io.BytesIO(image_file)).convert("RGBA")
        sticker_img = self.crop_transparency(sticker_img)

        zoom_factor = 1.7
        zoomed_size = (int(sticker_img.width * zoom_factor), int(sticker_img.height * zoom_factor))
        sticker_zoomed = sticker_img.resize(zoomed_size, Image.LANCZOS).convert("LA").convert("RGBA")

        sticker_alpha = sticker_zoomed.split()[3]
        sticker_alpha = sticker_alpha.point(lambda pixel: pixel * 0.25)
        sticker_zoomed.putalpha(sticker_alpha)

        canvas.paste(sticker_zoomed, (512 - zoomed_size[0], 0), sticker_zoomed)

        sticker_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        sticker_layer.paste(sticker_img, (0, height - sticker_img.height), sticker_img)
        return Image.alpha_composite(canvas, sticker_layer)

    def fit_text_to_box(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font_path: str,
        max_width: int,
        max_height: int,
        start_size: int = 60,
        min_size: int = 20,
        line_spacing: float = 1.2,
    ) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, list[str]]:
        font_size = start_size
        fallback_font = ImageFont.load_default()
        fallback_lines = [text] if text else [""]

        while font_size >= min_size:
            font = self._load_font(font_path, font_size)
            lines: list[str] = []

            for paragraph in text.split("\n"):
                words = paragraph.split()
                if not words:
                    lines.append("")
                    continue

                current_line: list[str] = []
                for word in words:
                    test_line = " ".join(current_line + [word])
                    line_width = draw.textlength(test_line, font=font)
                    if line_width <= max_width:
                        current_line.append(word)
                    else:
                        if current_line:
                            lines.append(" ".join(current_line))
                        current_line = [word]
                if current_line:
                    lines.append(" ".join(current_line))
                lines.append("")

            if lines and lines[-1] == "":
                lines.pop()

            bbox = draw.textbbox((0, 0), "A", font=font)
            line_height = bbox[3] - bbox[1]
            total_height = int(line_height * len(lines) * line_spacing)

            if total_height <= max_height:
                return font, lines or [""]
            font_size -= 2

        return fallback_font, fallback_lines

    def _get_main_speaker_name(self, quote: Quote) -> str:
        names = [phrase.speaker.name for phrase in quote.phrases if phrase.speaker]
        if not names:
            return "Unknown"

        counts = Counter(names)
        # Keep quote order when frequencies are equal.
        return max(counts, key=lambda name: (counts[name], -names.index(name)))

    def generate_quote_image(self, quote: Quote, images: dict[str, bytes]) -> io.BytesIO:
        canvas = self._base_gradient.copy().convert("RGBA")

        scroll_img = self._scroll_img.copy()
        scroll_alpha = scroll_img.split()[3]
        scroll_alpha = scroll_alpha.point(lambda pixel: pixel * 0.33)
        scroll_img.putalpha(scroll_alpha)

        scroll_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        scroll_layer.paste(scroll_img, (self.CANVAS_WIDTH - scroll_img.width - 10, 0), scroll_img)
        canvas = Image.alpha_composite(canvas, scroll_layer)

        draw = ImageDraw.Draw(canvas)
        text = self.quote_text_generator.generate_quote(quote)

        text_x = 552
        text_y = 60
        text_box_width = self.CANVAS_WIDTH - text_x - 40
        text_box_height = self.CANVAS_HEIGHT - 120

        font, wrapped_lines = self.fit_text_to_box(
            draw,
            text,
            self._font_path_main,
            text_box_width,
            text_box_height,
            start_size=60,
        )

        bbox = draw.textbbox((0, 0), "A", font=font)
        line_height = bbox[3] - bbox[1]
        total_height = int(line_height * len(wrapped_lines) * 1.2)
        y_offset = text_y + (text_box_height - total_height) // 2

        first_line = wrapped_lines[0] if wrapped_lines else ""
        first_line_width = draw.textlength(first_line, font=font)
        first_line_x = int(text_x + (text_box_width - first_line_width) // 2)
        first_line_y = y_offset

        for line in wrapped_lines:
            line_width = draw.textlength(line, font=font)
            x = int(text_x + (text_box_width - line_width) // 2)
            draw.text((x, int(y_offset)), line, font=font, fill=(255, 255, 255, 255))
            y_offset += int(line_height * 1.2)

        secondary_font = self._load_font(self._font_path_secondary, 40)

        speaker_name = self._get_main_speaker_name(quote)
        _, speaker_height = draw.textbbox((0, 0), speaker_name, font=secondary_font)[2:]
        draw.text((532, self.CANVAS_HEIGHT - speaker_height - 10), speaker_name, font=secondary_font, fill=(255, 255, 255, 255))

        date_text = self.date_parser.parse_date_to_string(quote.date)
        date_width, date_height = draw.textbbox((0, 0), date_text, font=secondary_font)[2:]
        draw.text((self.CANVAS_WIDTH - date_width - 40, self.CANVAS_HEIGHT - date_height - 10), date_text, font=secondary_font, fill=(255, 255, 255, 255))

        quote_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        quote_layer.paste(
            self._quote_sign_img,
            (first_line_x - self._quote_sign_img.width, first_line_y - self._quote_sign_img.height // 3),
            self._quote_sign_img,
        )
        canvas = Image.alpha_composite(canvas, quote_layer)

        if images:
            canvas = self.add_main_sticker_to_canvas(next(iter(images.values())), self.CANVAS_HEIGHT, canvas)

        output = io.BytesIO()
        output.name = "sticker_with_text.png"
        canvas.save(output, format="PNG")
        output.seek(0)
        return output
