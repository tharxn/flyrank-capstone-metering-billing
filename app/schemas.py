from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

TokenCount = Annotated[int, Field(strict=True, ge=0, le=10_000_000)]


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_tokens: TokenCount = 0
    cached_input_tokens: TokenCount = 0
    output_tokens: TokenCount = 0
    reasoning_tokens: TokenCount = 0

    @model_validator(mode="after")
    def inclusive_categories(self):
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached_input_tokens must be a subset of input_tokens")
        if self.reasoning_tokens > self.output_tokens:
            raise ValueError("reasoning_tokens must be a subset of output_tokens")
        return self
