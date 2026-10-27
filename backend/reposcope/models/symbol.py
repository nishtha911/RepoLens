from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from reposcope.models.base import Base


class Symbol(Base):
    __tablename__ = "symbols"

    id: Mapped[int] = mapped_column(primary_key=True)  # symbol id

    file_id: Mapped[int] = mapped_column(
        ForeignKey("files.id"),
        index=True,
    )  # belongs to which file

    name: Mapped[str] = mapped_column(Text)
    qualname: Mapped[str | None] = mapped_column(Text, nullable=True)  # fully-qualified name e.g. "MyClass.my_method"
    kind: Mapped[str] = mapped_column(String(50))  # what type of symbol ex: function, class, method

    start_line: Mapped[int] = mapped_column()
    end_line: Mapped[int] = mapped_column()