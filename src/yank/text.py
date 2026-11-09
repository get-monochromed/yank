"""Keep media titles and downloader logs inert in real terminals."""
import re


def safe_text(text: str, multiline: bool = False) -> str:
    # Remove OSC (including clipboard escapes), CSI and remaining controls.
    text = re.sub(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)', '', text)
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
    pattern = r'[\x00-\x08\x0b-\x1f\x7f-\x9f]' if multiline else r'[\x00-\x1f\x7f-\x9f]'
    return re.sub(pattern, ' ', text)
