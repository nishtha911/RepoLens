from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


from reposcope.contracts.vocabulary import SymbolKind


class Symbol(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        from_attributes=True,
    )

    id: int | None = Field(default=None, gt=0)
    file_id: int = Field(gt=0)
    name: str = Field(min_length=1)
    qualname: str | None = Field(default=None, min_length=1)  # e.g. "MyClass.my_method"; None for top-level or unknown
    kind: SymbolKind
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_line_range(self) -> Self:
        if self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        return self
