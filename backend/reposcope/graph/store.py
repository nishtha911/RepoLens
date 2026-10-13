"""GraphStore: PostgreSQL storage and retrieval for code symbols and edges.

Provides bulk ingestion, relational lookups, neighborhood traversal,
and golden graph validation for Track 1 Code Intelligence.
"""

from collections.abc import Sequence
from typing import Any, Literal

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from reposcope.contracts.edge import Edge as ContractEdge
from reposcope.contracts.symbol import Symbol as ContractSymbol
from reposcope.db.session import SessionLocal
from reposcope.models.edge import Edge
from reposcope.models.file import File
from reposcope.models.repository import Repository
from reposcope.models.symbol import Symbol


NeighborDirection = Literal["in", "out", "both"]


class GraphStore:
    """Manages persistence and graph queries over symbols and edges in PostgreSQL."""

    def __init__(self, session: Session | None = None) -> None:
        self._session = session
        self._owns_session = session is None

    @property
    def session(self) -> Session:
        if self._session is None:
            self._session = SessionLocal()
        return self._session

    def __enter__(self) -> "GraphStore":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._owns_session and self._session is not None:
            if exc_type is not None:
                self._session.rollback()
            else:
                self._session.commit()
            self._session.close()
            self._session = None

    def commit(self) -> None:
        """Commit the active transaction."""
        self.session.commit()

    def rollback(self) -> None:
        """Roll back the active transaction."""
        self.session.rollback()

    # -------------------------------------------------------------------------
    # Symbol Storage & Retrieval
    # -------------------------------------------------------------------------

    def bulk_insert_symbols(
        self,
        symbols: Sequence[Symbol | ContractSymbol | dict[str, Any]],
        file_id: int | None = None,
    ) -> list[Symbol]:
        """Bulk insert symbols into the database.

        Accepts ORM Symbol models, Pydantic ContractSymbols, or plain dictionaries.
        Returns the list of persisted ORM Symbol instances with IDs populated.
        """
        orm_symbols: list[Symbol] = []

        for item in symbols:
            if isinstance(item, Symbol):
                sym = item
                if file_id is not None and sym.file_id is None:
                    sym.file_id = file_id
            elif isinstance(item, ContractSymbol):
                sym = Symbol(
                    id=item.id,
                    file_id=item.file_id or file_id,
                    name=item.name,
                    kind=item.kind,
                    start_line=item.start_line,
                    end_line=item.end_line,
                )
            elif isinstance(item, dict):
                target_file_id = item.get("file_id", file_id)
                if target_file_id is None:
                    raise ValueError("file_id must be provided for symbol")
                sym = Symbol(
                    id=item.get("id"),
                    file_id=target_file_id,
                    name=item["name"],
                    kind=item["kind"],
                    start_line=item["start_line"],
                    end_line=item["end_line"],
                )
            else:
                raise TypeError(f"Unsupported symbol type: {type(item)}")

            orm_symbols.append(sym)

        self.session.add_all(orm_symbols)
        self.session.flush()
        return orm_symbols

    def get_symbol_by_id(self, symbol_id: int) -> Symbol | None:
        """Retrieve a single symbol by its primary key ID."""
        return self.session.get(Symbol, symbol_id)

    def get_symbols_by_file_id(self, file_id: int) -> list[Symbol]:
        """Retrieve all symbols defined in a specific file."""
        stmt = (
            select(Symbol)
            .where(Symbol.file_id == file_id)
            .order_by(Symbol.start_line, Symbol.id)
        )
        return list(self.session.scalars(stmt).all())

    def get_symbols_by_repository_id(self, repository_id: int) -> list[Symbol]:
        """Retrieve all symbols in a repository across all its files."""
        stmt = (
            select(Symbol)
            .join(File, Symbol.file_id == File.id)
            .where(File.repository_id == repository_id)
            .order_by(File.path, Symbol.start_line, Symbol.id)
        )
        return list(self.session.scalars(stmt).all())

    def get_symbol_by_name(self, file_id: int, name: str) -> Symbol | None:
        """Retrieve the first symbol with the given name in a file."""
        stmt = (
            select(Symbol)
            .where(and_(Symbol.file_id == file_id, Symbol.name == name))
            .limit(1)
        )
        return self.session.scalars(stmt).first()

    def find_symbols(
        self,
        repository_id: int | None = None,
        file_id: int | None = None,
        kind: str | None = None,
        name: str | None = None,
    ) -> list[Symbol]:
        """Query symbols matching the given criteria."""
        stmt = select(Symbol)
        if repository_id is not None:
            stmt = stmt.join(File, Symbol.file_id == File.id).where(
                File.repository_id == repository_id
            )
        if file_id is not None:
            stmt = stmt.where(Symbol.file_id == file_id)
        if kind is not None:
            stmt = stmt.where(Symbol.kind == kind)
        if name is not None:
            stmt = stmt.where(Symbol.name == name)
        stmt = stmt.order_by(Symbol.id)
        return list(self.session.scalars(stmt).all())

    # -------------------------------------------------------------------------
    # Edge Storage & Retrieval
    # -------------------------------------------------------------------------

    def bulk_insert_edges(
        self,
        edges: Sequence[Edge | ContractEdge | dict[str, Any]],
        ignore_duplicates: bool = True,
    ) -> list[Edge]:
        """Bulk insert edges into the database.

        Accepts ORM Edge models, Pydantic ContractEdges, or plain dictionaries.
        Deduplicates within the batch and avoids inserting existing duplicates
        when ignore_duplicates is True.
        """
        orm_edges: list[Edge] = []
        seen_keys: set[tuple[int, int, str]] = set()

        for item in edges:
            if isinstance(item, Edge):
                edge = item
            elif isinstance(item, ContractEdge):
                edge = Edge(
                    id=item.id,
                    source_symbol_id=item.source_symbol_id,
                    target_symbol_id=item.target_symbol_id,
                    kind=item.kind,
                    confidence=item.confidence,
                )
            elif isinstance(item, dict):
                edge = Edge(
                    id=item.get("id"),
                    source_symbol_id=item["source_symbol_id"],
                    target_symbol_id=item["target_symbol_id"],
                    kind=item["kind"],
                    confidence=item.get("confidence"),
                )
            else:
                raise TypeError(f"Unsupported edge type: {type(item)}")

            key = (edge.source_symbol_id, edge.target_symbol_id, edge.kind)
            if ignore_duplicates and key in seen_keys:
                continue
            seen_keys.add(key)
            orm_edges.append(edge)

        if not orm_edges:
            return []

        if ignore_duplicates:
            # Query any pre-existing edges matching (source, target, kind) tuples
            conditions = [
                and_(
                    Edge.source_symbol_id == e.source_symbol_id,
                    Edge.target_symbol_id == e.target_symbol_id,
                    Edge.kind == e.kind,
                )
                for e in orm_edges
            ]
            existing = self.session.scalars(
                select(Edge).where(or_(*conditions))
            ).all()
            existing_keys = {
                (ex.source_symbol_id, ex.target_symbol_id, ex.kind)
                for ex in existing
            }
            orm_edges = [
                e
                for e in orm_edges
                if (e.source_symbol_id, e.target_symbol_id, e.kind)
                not in existing_keys
            ]

        if orm_edges:
            self.session.add_all(orm_edges)
            self.session.flush()

        return orm_edges

    def get_edge_by_id(self, edge_id: int) -> Edge | None:
        """Retrieve a single edge by its primary key ID."""
        return self.session.get(Edge, edge_id)

    def get_edges_by_source(self, source_symbol_id: int) -> list[Edge]:
        """Retrieve all outgoing edges from a source symbol."""
        stmt = (
            select(Edge)
            .where(Edge.source_symbol_id == source_symbol_id)
            .order_by(Edge.id)
        )
        return list(self.session.scalars(stmt).all())

    def get_edges_by_target(self, target_symbol_id: int) -> list[Edge]:
        """Retrieve all incoming edges to a target symbol."""
        stmt = (
            select(Edge)
            .where(Edge.target_symbol_id == target_symbol_id)
            .order_by(Edge.id)
        )
        return list(self.session.scalars(stmt).all())

    def get_edges_between(
        self,
        source_symbol_id: int,
        target_symbol_id: int,
    ) -> list[Edge]:
        """Retrieve edges directly connecting source to target."""
        stmt = (
            select(Edge)
            .where(
                and_(
                    Edge.source_symbol_id == source_symbol_id,
                    Edge.target_symbol_id == target_symbol_id,
                )
            )
            .order_by(Edge.id)
        )
        return list(self.session.scalars(stmt).all())

    def get_edges_for_repository(self, repository_id: int) -> list[Edge]:
        """Retrieve all edges where both source and target belong to the repository."""
        file_ids_subq = (
            select(File.id)
            .where(File.repository_id == repository_id)
            .scalar_subquery()
        )
        symbol_ids_subq = (
            select(Symbol.id)
            .where(Symbol.file_id.in_(file_ids_subq))
            .scalar_subquery()
        )
        stmt = (
            select(Edge)
            .where(
                and_(
                    Edge.source_symbol_id.in_(symbol_ids_subq),
                    Edge.target_symbol_id.in_(symbol_ids_subq),
                )
            )
            .order_by(Edge.id)
        )
        return list(self.session.scalars(stmt).all())

    def get_neighbors(
        self,
        symbol_id: int,
        direction: NeighborDirection = "both",
        kinds: Sequence[str] | None = None,
    ) -> list[tuple[Edge, Symbol]]:
        """Retrieve 1-hop neighbor symbols connected to symbol_id.

        Direction:
        - 'out': outgoing edges (symbol is caller/source, neighbor is callee/target)
        - 'in': incoming edges (symbol is callee/target, neighbor is caller/source)
        - 'both': union of incoming and outgoing neighbors

        Optionally filtered by edge kinds. Returns list of (Edge, neighbor_Symbol).
        """
        results: list[tuple[Edge, Symbol]] = []

        if direction in ("out", "both"):
            stmt_out = (
                select(Edge, Symbol)
                .join(Symbol, Edge.target_symbol_id == Symbol.id)
                .where(Edge.source_symbol_id == symbol_id)
            )
            if kinds:
                stmt_out = stmt_out.where(Edge.kind.in_(kinds))
            stmt_out = stmt_out.order_by(Edge.id)
            results.extend(self.session.execute(stmt_out).all())

        if direction in ("in", "both"):
            stmt_in = (
                select(Edge, Symbol)
                .join(Symbol, Edge.source_symbol_id == Symbol.id)
                .where(Edge.target_symbol_id == symbol_id)
            )
            if kinds:
                stmt_in = stmt_in.where(Edge.kind.in_(kinds))
            stmt_in = stmt_in.order_by(Edge.id)
            results.extend(self.session.execute(stmt_in).all())

        return results

    # -------------------------------------------------------------------------
    # Golden Graph & Fixture Ingestion / Export
    # -------------------------------------------------------------------------

    def import_golden_graph(
        self,
        graph_data: dict[str, Any],
        repository_full_name: str | None = None,
        remote_url: str = "https://github.com/example/repo",
    ) -> dict[str, Any]:
        """Import a complete golden graph fixture (files, symbols, edges).

        Preserves fixture IDs and associations. Returns summary mapping dict.
        """
        repo_name = repository_full_name or graph_data.get(
            "repository", "golden_repo"
        )

        repo = self.session.scalars(
            select(Repository).where(Repository.full_name == repo_name)
        ).first()

        if repo is None:
            repo = Repository(full_name=repo_name, remote_url=remote_url)
            self.session.add(repo)
            self.session.flush()

        # Clean existing files/symbols/edges for this repo to ensure clean idempotent state
        self.delete_repository_graph(repo.id)

        # 1. Insert files
        file_map: dict[int, File] = {}
        for f_data in graph_data["files"]:
            file_obj = File(
                id=f_data.get("id"),
                repository_id=repo.id,
                path=f_data["path"],
                language=f_data.get("language", "python"),
            )
            self.session.add(file_obj)
            file_map[f_data["id"]] = file_obj

        self.session.flush()

        # 2. Insert symbols
        symbol_map: dict[int, Symbol] = {}
        for s_data in graph_data["symbols"]:
            target_file = file_map[s_data["file_id"]]
            symbol_obj = Symbol(
                id=s_data.get("id"),
                file_id=target_file.id,
                name=s_data["name"],
                kind=s_data["kind"],
                start_line=s_data["start_line"],
                end_line=s_data["end_line"],
            )
            self.session.add(symbol_obj)
            symbol_map[s_data["id"]] = symbol_obj

        self.session.flush()

        # 3. Insert edges
        edges_to_insert: list[Edge] = []
        for e_data in graph_data["edges"]:
            source_sym = symbol_map[e_data["source_symbol_id"]]
            target_sym = symbol_map[e_data["target_symbol_id"]]
            edge_obj = Edge(
                id=e_data.get("id"),
                source_symbol_id=source_sym.id,
                target_symbol_id=target_sym.id,
                kind=e_data["kind"],
                confidence=e_data.get("confidence"),
            )
            edges_to_insert.append(edge_obj)

        self.session.add_all(edges_to_insert)
        self.session.flush()

        return {
            "repository_id": repo.id,
            "files_count": len(file_map),
            "symbols_count": len(symbol_map),
            "edges_count": len(edges_to_insert),
        }

    def export_graph(self, repository_id: int) -> dict[str, Any]:
        """Export the stored graph for a repository matching the golden fixture structure."""
        repo = self.session.get(Repository, repository_id)
        if repo is None:
            raise ValueError(f"Repository with id {repository_id} not found")

        files = list(
            self.session.scalars(
                select(File)
                .where(File.repository_id == repository_id)
                .order_by(File.id)
            ).all()
        )
        file_ids = [f.id for f in files]

        symbols = list(
            self.session.scalars(
                select(Symbol)
                .where(Symbol.file_id.in_(file_ids))
                .order_by(Symbol.id)
            ).all()
        )
        symbol_ids = [s.id for s in symbols]

        edges = list(
            self.session.scalars(
                select(Edge)
                .where(
                    and_(
                        Edge.source_symbol_id.in_(symbol_ids),
                        Edge.target_symbol_id.in_(symbol_ids),
                    )
                )
                .order_by(Edge.id)
            ).all()
        )

        return {
            "repository": repo.full_name,
            "files_count": len(files),
            "symbols_count": len(symbols),
            "edges_count": len(edges),
            "files": [
                {
                    "id": f.id,
                    "path": f.path,
                }
                for f in files
            ],
            "symbols": [
                {
                    "id": s.id,
                    "file_id": s.file_id,
                    "name": s.name,
                    "kind": s.kind,
                    "start_line": s.start_line,
                    "end_line": s.end_line,
                }
                for s in symbols
            ],
            "edges": [
                {
                    "id": e.id,
                    "source_symbol_id": e.source_symbol_id,
                    "target_symbol_id": e.target_symbol_id,
                    "kind": e.kind,
                    "confidence": e.confidence,
                }
                for e in edges
            ],
        }

    def delete_repository_graph(self, repository_id: int) -> None:
        """Delete all edges, symbols, and files belonging to a repository."""
        files = self.session.scalars(
            select(File).where(File.repository_id == repository_id)
        ).all()
        file_ids = [f.id for f in files]

        if not file_ids:
            return

        symbols = self.session.scalars(
            select(Symbol).where(Symbol.file_id.in_(file_ids))
        ).all()
        symbol_ids = [s.id for s in symbols]

        if symbol_ids:
            # Delete edges touching any of these symbols
            edges = self.session.scalars(
                select(Edge).where(
                    or_(
                        Edge.source_symbol_id.in_(symbol_ids),
                        Edge.target_symbol_id.in_(symbol_ids),
                    )
                )
            ).all()
            for edge in edges:
                self.session.delete(edge)

            for sym in symbols:
                self.session.delete(sym)

        for file_obj in files:
            self.session.delete(file_obj)

        self.session.flush()
