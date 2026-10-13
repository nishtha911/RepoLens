from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from reposcope.models.base import Base


class Edge(Base):
    __tablename__ = "edges"

    id: Mapped[int] = mapped_column(primary_key=True) #edge id

    source_symbol_id: Mapped[int] = mapped_column(
        ForeignKey("symbols.id"),
        index=True,
    )#whewre does symbol start

    target_symbol_id: Mapped[int] = mapped_column(
        ForeignKey("symbols.id"),
        index=True,
    )#where symbol ends

    kind: Mapped[str] = mapped_column(String(50))#what type of connection ex:function call
    confidence: Mapped[float | None] = mapped_column(nullable=True, default=None)

    __table_args__ = (
        UniqueConstraint(
            "source_symbol_id",
            "target_symbol_id",
            "kind",
            name="uq_edges_source_target_kind",
        ),
    )

    """ex-def main():
           load_data()
           
           will be represented as main â†’(edge)-> load_data ;kind: calls, main is calling load data
    """ 