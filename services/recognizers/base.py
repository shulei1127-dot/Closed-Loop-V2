from typing import Protocol

from schemas.sync import RecognitionResult


class BaseRecognizer(Protocol):
    def recognize(self, raw_columns: list, raw_rows: list[dict]) -> RecognitionResult: ...

