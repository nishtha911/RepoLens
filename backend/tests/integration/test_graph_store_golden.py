"""Integration test validating GraphStore against golden_graph.json fixture.

Verifies:
1. Exact match of file inventory (15 files).
2. Exact match of symbol inventory (44 symbols).
3. Exact match of edge inventory and connectivity (45 edges).
4. Correct directional neighbor retrieval across the stored graph.
"""

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from reposcope.graph.store import GraphStore
from reposcope.models.base import Base
from reposcope.parsing.python_parser import parse_python


FIXTURE_ROOT = (
    Path(__file__).resolve().parents[1] / "fixtures" / "mini_repo"
)
GOLDEN_PATH = FIXTURE_ROOT / "golden_graph.json"


@pytest.fixture
def golden_data() -> dict:
    """Load the canonical golden graph fixture."""
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8-sig"))


@pytest.fixture
def test_db_session() -> Session:
    """Provide an isolated database session with full schema initialized."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


def test_golden_graph_exact_match_roundtrip(test_db_session: Session, golden_data: dict):
    """Validate that importing and exporting golden_graph produces an exact match."""
    store = GraphStore(session=test_db_session)

    # 1. Import golden graph
    summary = store.import_golden_graph(
        golden_data,
        repository_full_name="tests/fixtures/mini_repo",
    )
    repo_id = summary["repository_id"]

    assert summary["files_count"] == golden_data["files_count"] == 15
    assert summary["symbols_count"] == golden_data["symbols_count"] == 44
    assert summary["edges_count"] == golden_data["edges_count"] == 45

    # 2. Export stored graph
    exported = store.export_graph(repo_id)

    assert exported["repository"] == "tests/fixtures/mini_repo"
    assert exported["files_count"] == 15
    assert exported["symbols_count"] == 44
    assert exported["edges_count"] == 45

    # 3. Exact match: Files
    golden_files_by_id = {f["id"]: f["path"] for f in golden_data["files"]}
    exported_files_by_id = {f["id"]: f["path"] for f in exported["files"]}
    assert exported_files_by_id == golden_files_by_id

    # 4. Exact match: Symbols
    golden_symbols_by_id = {
        s["id"]: (s["file_id"], s["name"], s["kind"], s["start_line"], s["end_line"])
        for s in golden_data["symbols"]
    }
    exported_symbols_by_id = {
        s["id"]: (s["file_id"], s["name"], s["kind"], s["start_line"], s["end_line"])
        for s in exported["symbols"]
    }
    assert exported_symbols_by_id == golden_symbols_by_id

    # 5. Exact match: Edges
    golden_edges_by_id = {
        e["id"]: (
            e["source_symbol_id"],
            e["target_symbol_id"],
            e["kind"],
            e["confidence"],
        )
        for e in golden_data["edges"]
    }
    exported_edges_by_id = {
        e["id"]: (
            e["source_symbol_id"],
            e["target_symbol_id"],
            e["kind"],
            e["confidence"],
        )
        for e in exported["edges"]
    }
    assert exported_edges_by_id == golden_edges_by_id


def test_tree_sitter_extracted_symbols_match_golden_store(
    test_db_session: Session, golden_data: dict
):
    """Verify that parsing real source files with Tree-sitter and storing them in GraphStore

    yields the exact 44 symbol definitions present in golden_graph.json.
    """
    store = GraphStore(session=test_db_session)

    # Import files only from golden manifest
    from reposcope.models.file import File
    from reposcope.models.repository import Repository

    repo = Repository(
        full_name="tests/fixtures/mini_repo",
        remote_url="https://github.com/example/mini_repo",
    )
    test_db_session.add(repo)
    test_db_session.flush()

    file_obj_map = {}
    for f_data in golden_data["files"]:
        f = File(
            id=f_data["id"],
            repository_id=repo.id,
            path=f_data["path"],
            language="python",
        )
        test_db_session.add(f)
        file_obj_map[f_data["path"]] = f
    test_db_session.flush()

    # Parse and insert using Tree-sitter
    for f_data in golden_data["files"]:
        rel_path = f_data["path"]
        file_disk_path = FIXTURE_ROOT / rel_path
        parsed = parse_python(file_disk_path.read_bytes(), file_path=rel_path)

        symbols_payload = [
            {
                "name": sym.name,
                "kind": sym.kind,
                "start_line": sym.start_line,
                "end_line": sym.end_line,
            }
            for sym in parsed.symbols
        ]
        store.bulk_insert_symbols(symbols_payload, file_id=file_obj_map[rel_path].id)

    stored_symbols = store.get_symbols_by_repository_id(repo.id)
    assert len(stored_symbols) == golden_data["symbols_count"] == 44

    golden_symbols_set = {
        (
            golden_data["files"][s["file_id"] - 1]["path"],
            s["name"],
            s["kind"],
            s["start_line"],
            s["end_line"],
        )
        for s in golden_data["symbols"]
    }

    files_by_id = {f.id: f.path for f in file_obj_map.values()}
    stored_symbols_set = {
        (
            files_by_id[s.file_id],
            s.name,
            s.kind,
            s.start_line,
            s.end_line,
        )
        for s in stored_symbols
    }

    assert stored_symbols_set == golden_symbols_set


def test_stored_graph_neighborhood_traversal(
    test_db_session: Session, golden_data: dict
):
    """Verify that 1-hop inbound and outbound graph queries correctly reflect

    call and inheritance edges from the golden graph.
    """
    store = GraphStore(session=test_db_session)
    store.import_golden_graph(golden_data)

    # In golden_graph, find a symbol with known callers/callees (e.g. symbol 1: build_service)
    # Edge 1: (1 -> 11, CALLS)
    # Edge 2: (1 -> 16, CALLS)
    # Edge 3: (2 -> 1, CALLS)
    sym1 = store.get_symbol_by_id(1)
    assert sym1 is not None
    assert sym1.name == "build_service"

    # Outgoing edges from build_service (who does build_service call?)
    callees = store.get_neighbors(1, direction="out", kinds=["CALLS"])
    callee_ids = [edge.target_symbol_id for edge, _ in callees]
    assert set(callee_ids) == {11, 16}

    # Incoming edges to build_service (who calls build_service?)
    callers = store.get_neighbors(1, direction="in", kinds=["CALLS"])
    caller_ids = [edge.source_symbol_id for edge, _ in callers]
    assert caller_ids == [2]

    # Calls in both directions
    calls_neighbors = store.get_neighbors(1, direction="both", kinds=["CALLS"])
    call_neighbor_ids = {node.id for _, node in calls_neighbors}
    assert call_neighbor_ids == {2, 11, 16}

    # Tested-by edges (build_service -> TESTED_BY -> test_build_service)
    test_neighbors = store.get_neighbors(1, direction="out", kinds=["TESTED_BY"])
    assert len(test_neighbors) == 1
    edge_test, node_test = test_neighbors[0]
    assert edge_test.kind == "TESTED_BY"
    assert node_test.id == 21
    assert node_test.name == "test_build_service"

    # All neighbors across all edge kinds
    all_neighbors = store.get_neighbors(1, direction="both")
    neighbor_ids = {node.id for _, node in all_neighbors}
    assert neighbor_ids == {2, 11, 16, 21}
