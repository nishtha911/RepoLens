"""Unit tests for GraphStore using in-memory database."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from reposcope.contracts.edge import Edge as ContractEdge
from reposcope.contracts.symbol import Symbol as ContractSymbol
from reposcope.graph.store import GraphStore
from reposcope.models.base import Base
from reposcope.models.file import File
from reposcope.models.repository import Repository
from reposcope.models.symbol import Symbol


@pytest.fixture
def test_session() -> Session:
    """Create an in-memory SQLite database and return a session."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def sample_repo_and_file(test_session: Session) -> tuple[Repository, File]:
    """Create a sample repository and file for graph testing."""
    repo = Repository(full_name="org/repo", remote_url="https://github.com/org/repo")
    test_session.add(repo)
    test_session.flush()

    file_obj = File(repository_id=repo.id, path="app/main.py", language="python")
    test_session.add(file_obj)
    test_session.flush()
    return repo, file_obj


def test_bulk_insert_symbols_dict_and_models(
    test_session: Session, sample_repo_and_file: tuple[Repository, File]
):
    _, file_obj = sample_repo_and_file
    store = GraphStore(session=test_session)

    symbols_data = [
        {"name": "fetch_user", "kind": "function", "start_line": 10, "end_line": 20},
        ContractSymbol(
            file_id=file_obj.id,
            name="save_user",
            kind="function",
            start_line=25,
            end_line=35,
        ),
        Symbol(
            file_id=file_obj.id,
            name="User",
            kind="class",
            start_line=40,
            end_line=60,
        ),
    ]

    inserted = store.bulk_insert_symbols(symbols_data, file_id=file_obj.id)
    assert len(inserted) == 3
    assert all(s.id is not None for s in inserted)
    assert all(s.file_id == file_obj.id for s in inserted)

    by_file = store.get_symbols_by_file_id(file_obj.id)
    assert len(by_file) == 3
    assert [s.name for s in by_file] == ["fetch_user", "save_user", "User"]


def test_bulk_insert_edges_and_deduplication(
    test_session: Session, sample_repo_and_file: tuple[Repository, File]
):
    _, file_obj = sample_repo_and_file
    store = GraphStore(session=test_session)

    symbols = store.bulk_insert_symbols(
        [
            {"name": "foo", "kind": "function", "start_line": 1, "end_line": 5},
            {"name": "bar", "kind": "function", "start_line": 6, "end_line": 10},
        ],
        file_id=file_obj.id,
    )
    s1, s2 = symbols[0].id, symbols[1].id

    edges_data = [
        {"source_symbol_id": s1, "target_symbol_id": s2, "kind": "CALLS", "confidence": 1.0},
        # Duplicate in same batch
        {"source_symbol_id": s1, "target_symbol_id": s2, "kind": "CALLS", "confidence": 1.0},
        ContractEdge(source_symbol_id=s2, target_symbol_id=s1, kind="CALLS", confidence=0.8),
    ]

    inserted = store.bulk_insert_edges(edges_data, ignore_duplicates=True)
    assert len(inserted) == 2

    # Second batch with duplicate of existing edge
    more_edges = [
        {"source_symbol_id": s1, "target_symbol_id": s2, "kind": "CALLS", "confidence": 1.0},
    ]
    inserted_second = store.bulk_insert_edges(more_edges, ignore_duplicates=True)
    assert len(inserted_second) == 0

    outgoing = store.get_edges_by_source(s1)
    assert len(outgoing) == 1
    assert outgoing[0].target_symbol_id == s2
    assert outgoing[0].confidence == 1.0


def test_get_neighbors_directional(
    test_session: Session, sample_repo_and_file: tuple[Repository, File]
):
    _, file_obj = sample_repo_and_file
    store = GraphStore(session=test_session)

    symbols = store.bulk_insert_symbols(
        [
            {"name": "caller_fn", "kind": "function", "start_line": 1, "end_line": 5},
            {"name": "target_fn", "kind": "function", "start_line": 6, "end_line": 10},
            {"name": "callee_fn", "kind": "function", "start_line": 11, "end_line": 15},
        ],
        file_id=file_obj.id,
    )
    caller, target, callee = symbols[0].id, symbols[1].id, symbols[2].id

    store.bulk_insert_edges(
        [
            {"source_symbol_id": caller, "target_symbol_id": target, "kind": "CALLS"},
            {"source_symbol_id": target, "target_symbol_id": callee, "kind": "CALLS"},
        ]
    )

    # Inbound neighbors of target (who calls target?)
    in_neighbors = store.get_neighbors(target, direction="in")
    assert len(in_neighbors) == 1
    edge_in, node_in = in_neighbors[0]
    assert edge_in.source_symbol_id == caller
    assert node_in.name == "caller_fn"

    # Outbound neighbors of target (who does target call?)
    out_neighbors = store.get_neighbors(target, direction="out")
    assert len(out_neighbors) == 1
    edge_out, node_out = out_neighbors[0]
    assert edge_out.target_symbol_id == callee
    assert node_out.name == "callee_fn"

    # Both directions
    both_neighbors = store.get_neighbors(target, direction="both")
    assert len(both_neighbors) == 2


def test_find_symbols_by_criteria(
    test_session: Session, sample_repo_and_file: tuple[Repository, File]
):
    repo, file_obj = sample_repo_and_file
    store = GraphStore(session=test_session)

    store.bulk_insert_symbols(
        [
            {"name": "Alpha", "kind": "class", "start_line": 1, "end_line": 20},
            {"name": "alpha_helper", "kind": "function", "start_line": 21, "end_line": 30},
            {"name": "Beta", "kind": "class", "start_line": 31, "end_line": 50},
        ],
        file_id=file_obj.id,
    )

    classes = store.find_symbols(repository_id=repo.id, kind="class")
    assert len(classes) == 2
    assert {c.name for c in classes} == {"Alpha", "Beta"}

    by_name = store.find_symbols(file_id=file_obj.id, name="alpha_helper")
    assert len(by_name) == 1
    assert by_name[0].kind == "function"


def test_delete_repository_graph(
    test_session: Session, sample_repo_and_file: tuple[Repository, File]
):
    repo, file_obj = sample_repo_and_file
    store = GraphStore(session=test_session)

    symbols = store.bulk_insert_symbols(
        [
            {"name": "one", "kind": "function", "start_line": 1, "end_line": 5},
            {"name": "two", "kind": "function", "start_line": 6, "end_line": 10},
        ],
        file_id=file_obj.id,
    )
    store.bulk_insert_edges(
        [{"source_symbol_id": symbols[0].id, "target_symbol_id": symbols[1].id, "kind": "CALLS"}]
    )

    store.delete_repository_graph(repo.id)

    assert len(store.get_symbols_by_repository_id(repo.id)) == 0
    assert len(store.get_edges_for_repository(repo.id)) == 0
