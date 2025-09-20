from domain.speaker import Speaker

class Phrase:
    def __init__(self, speaker: Speaker, text: str, context_text: str):
        self.text = text
        self.speaker = speaker
        self.context_text = context_text