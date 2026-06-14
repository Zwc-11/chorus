"""ModelPort adapters: the concrete providers behind the model seam.

All adapters implement :class:`murmur.core.model_port.ModelPort`. The HTTP
adapters use only the standard library, so the base install needs no vendor SDK.
"""

from murmur.adapters.models.fake import FakeModel, RecordedCall, StochasticFakeModel
from murmur.adapters.models.ollama import OllamaModel
from murmur.adapters.models.openai_compatible import (
    DEFAULT_PRICES,
    OpenAICompatibleModel,
    urllib_transport,
)

__all__ = [
    "DEFAULT_PRICES",
    "FakeModel",
    "OllamaModel",
    "OpenAICompatibleModel",
    "RecordedCall",
    "StochasticFakeModel",
    "urllib_transport",
]
