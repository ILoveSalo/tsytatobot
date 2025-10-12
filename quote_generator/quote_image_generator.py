from date_parser.date_parser import DateParser
from domain.quote import Quote

from PIL import Image, ImageDraw, ImageFont
import io
import math

from quote_generator.quote_text_generator import QuoteTextGenerator

class QuoteImageGenerator:
    def __init__(self, quote_text_generator: QuoteTextGenerator, date_parser: DateParser):
        self.quote_text_generator = quote_text_generator
        self.date_parser = date_parser

    def crop_transparency(self, img: Image.Image) -> Image.Image:
        """Crop transparent borders from RGBA image."""
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        bbox = img.getbbox()  # bounding box of non-zero regions in alpha
        if bbox:
            return img.crop(bbox)
        return img

    def create_radial_gradient(self, size, inner_color, outer_color):
        """Generate a radial gradient image (RGBA)."""
        width, height = size
        center_x, center_y = width // 2, height // 2
        max_radius = math.sqrt(center_x ** 2 + center_y ** 2)

        gradient = Image.new("RGBA", (width, height), outer_color)
        draw = ImageDraw.Draw(gradient)

        for y in range(height):
            for x in range(width):
                # Distance from center
                dx = x - center_x
                dy = y - center_y
                dist = math.sqrt(dx * dx + dy * dy) / max_radius

                # Clamp [0..1]
                dist = min(1, dist)

                # Interpolate colors
                r = int(inner_color[0] + (outer_color[0] - inner_color[0]) * dist)
                g = int(inner_color[1] + (outer_color[1] - inner_color[1]) * dist)
                b = int(inner_color[2] + (outer_color[2] - inner_color[2]) * dist)
                a = int(inner_color[3] + (outer_color[3] - inner_color[3]) * dist)

                draw.point((x, y), (r, g, b, a))

        return gradient

    def add_main_sticker_to_canvas(self, image_file, height, canvas):
        sticker_img = Image.open(io.BytesIO(image_file)).convert("RGBA")

        # --- Crop empty space ---
        sticker_img = self.crop_transparency(sticker_img)

        # --- Make second sticker zoomed + semitransparent ---
        zoom_factor = 1.7
        zoomed_size = (int(sticker_img.width * zoom_factor), int(sticker_img.height * zoom_factor))
        sticker_zoomed = sticker_img.resize(zoomed_size, Image.LANCZOS)

        # Convert zoomed image to grayscale but keep alpha
        sticker_zoomed = sticker_zoomed.convert("LA").convert("RGBA")

        # Reduce opacity
        sticker_alpha = sticker_zoomed.split()[3]  # get alpha channel
        sticker_alpha = sticker_alpha.point(lambda p: p * 0.25)  # 25% transparent
        sticker_zoomed.putalpha(sticker_alpha)

        # Zoomed sticker layer
        canvas.paste(sticker_zoomed, (512 - zoomed_size[0], 0), sticker_zoomed)

        # Original sticker layer (bottom)
        sticker_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        sticker_layer.paste(sticker_img, (0, height - sticker_img.height), sticker_img)
        canvas = Image.alpha_composite(canvas, sticker_layer)

        return canvas

    def fit_text_to_box(self, draw, text, font_path, max_width, max_height, start_size=60, min_size=20,
                        line_spacing=1.2):
        """Wrap and resize text to fit into given box dimensions, respecting newlines."""
        font_size = start_size
        while font_size >= min_size:
            font = ImageFont.truetype(font_path, font_size)
            lines = []

            paragraphs = text.split("\n")

            for para in paragraphs:
                words = para.split()
                current_line = []
                for word in words:
                    test_line = ' '.join(current_line + [word])
                    line_width = draw.textlength(test_line, font=font)
                    if line_width <= max_width:
                        current_line.append(word)
                    else:
                        if current_line:
                            lines.append(' '.join(current_line))
                        current_line = [word]
                if current_line:
                    lines.append(' '.join(current_line))

                lines.append("")

            # remove trailing empty line
            if lines and lines[-1] == "":
                lines.pop()

            # Calculate total height
            bbox = draw.textbbox((0, 0), "A", font=font)
            line_height = bbox[3] - bbox[1]
            total_height = int(line_height * len(lines) * line_spacing)

            if total_height <= max_height:
                return font, lines  # success
            font_size -= 2  # shrink and retry

        return font, lines

    def generate_quote_image(self, quote: Quote, images: dict):
        # 2. Open asset images
        quote_sign_img = Image.open("assets/quote-sign.png").convert("RGBA")
        scroll_img = Image.open("assets/scroll.png").convert("RGBA")

        # 3. Create radial gradient background
        new_width = 1100
        new_height = 512
        canvas = self.create_radial_gradient(
            (new_width, new_height),
            inner_color=(44, 211, 189, 255),  # center color
            outer_color=(36, 129, 117, 255)  # edge color
        )

        # Reduce opacity
        scroll_alpha = scroll_img.split()[3]  # get alpha channel
        scroll_alpha = scroll_alpha.point(lambda p: p * 0.33)  # 33% transparent
        scroll_img.putalpha(scroll_alpha)

        # Ensure canvas is RGBA
        canvas = canvas.convert("RGBA")

        # Scroll image layer
        scroll_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        scroll_layer.paste(scroll_img, (new_width - scroll_img.width - 10, 0), scroll_img)
        canvas = Image.alpha_composite(canvas, scroll_layer)

        # 4. Draw text
        draw = ImageDraw.Draw(canvas)
        try:
            font = ImageFont.truetype("assets/SourceSans3-BoldItalic.ttf", 60)  # needs Arial installed
        except:
            font = ImageFont.load_default()

        text = self.quote_text_generator.generate_quote(quote)

        # Define text area (right half of canvas)
        text_x = 512 + 40
        text_y = 60
        text_box_width = new_width - text_x - 40
        text_box_height = new_height - 120

        font, wrapped_lines = self.fit_text_to_box(
            draw,
            text,
            "assets/SourceSans3-BoldItalic.ttf",
            text_box_width,
            text_box_height,
            start_size=60
        )

        # Draw centered vertically
        bbox = draw.textbbox((0, 0), "A", font=font)
        line_height = bbox[3] - bbox[1]
        total_height = int(line_height * len(wrapped_lines) * 1.2)
        y_offset = text_y + (text_box_height - total_height) // 2

        # Compute first line's position for quote sign
        first_line = wrapped_lines[0]
        first_line_width = draw.textlength(first_line, font=font)
        first_line_x = int(text_x + (text_box_width - first_line_width) // 2)
        first_line_y = y_offset  # actual start of first line

        for line in wrapped_lines:
            line_width = draw.textlength(line, font=font)
            x = int(text_x + (text_box_width - line_width) // 2)
            draw.text((x, int(y_offset)), line, font=font, fill=(255, 255, 255, 255))
            y_offset += int(line_height * 1.2)

        # 4b. Draw second text
        try:
            font2 = ImageFont.truetype("assets/Gabriela-Regular.ttf", 40)  # smaller
        except:
            font2 = ImageFont.load_default()

        text2 = quote.phrases[0].speaker.name #TODO: choose main speaker
        text2_width, text2_height = draw.textbbox((0, 0), text2, font=font2)[2:]
        x2 = 512 + 20
        y2 = new_height - text2_height - 10  # 10 px space below first text
        draw.text((x2, y2), text2, font=font2, fill=(255, 255, 255, 255))

        # 4c. Draw third text
        text3 = self.date_parser.parse_date_to_string(quote.date)
        text3_width, text3_height = draw.textbbox((0, 0), text2, font=font2)[2:]
        x3 = new_width - text3_width - 40
        y3 = new_height - text3_height - 10  # 10 px space below first text
        draw.text((x3, y3), text3, font=font2, fill=(255, 255, 255, 255))

        # 4️⃣ Quote sign (if you want it on top)
        quote_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        quote_layer.paste(
            quote_sign_img,
            (first_line_x - quote_sign_img.width, first_line_y - quote_sign_img.height // 3),
            quote_sign_img
        )
        canvas = Image.alpha_composite(canvas, quote_layer)

        #
        if len(images.values()) > 0:
            canvas = self.add_main_sticker_to_canvas(list(images.values())[0], new_height, canvas) #TODO: use many images or chosen one

        # 5. Save to memory as PNG
        output = io.BytesIO()
        output.name = "sticker_with_text.png"
        canvas.save(output, format="PNG")
        output.seek(0)

        return output