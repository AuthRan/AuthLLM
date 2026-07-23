"""FastAPI inference server. Depends on ashugpt.api.service, which is the
only bridge to the actual model/tokenizer/generation code -- app.py and
schemas.py know nothing about transformers, tensors, or sampling.
"""

from ashugpt.api.schemas import GenerateRequest, GenerateResponse, HealthResponse
from ashugpt.api.service import GenerateResult, InferenceService

__all__ = [
    "GenerateRequest",
    "GenerateResponse",
    "HealthResponse",
    "GenerateResult",
    "InferenceService",
]
