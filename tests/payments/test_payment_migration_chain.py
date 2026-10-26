"""Read-only checks for payment schema declarations and Alembic ownership."""
import ast
from pathlib import Path
from typing import TypeAlias

from app.models.payment import Payment


ROOT = Path(__file__).resolve().parents[2]
VERSIONS = ROOT / "alembic" / "versions"
RevisionParents: TypeAlias = str | tuple[str, ...] | None


def _revision_metadata(path: Path) -> tuple[str, RevisionParents]:
    tree = ast.parse(path.read_text())
    values = {}

    def record(name: str, value_node: ast.expr) -> None:
        if name in values:
            raise ValueError(f"{path.name} declares {name} more than once")
        try:
            values[name] = ast.literal_eval(value_node)
        except (ValueError, TypeError) as error:
            raise ValueError(f"{path.name} has a non-literal {name} declaration") from error

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}:
                    record(target.id, node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id in {"revision", "down_revision"}:
                if node.value is None:
                    raise ValueError(f"{path.name} has no value for {node.target.id}")
                record(node.target.id, node.value)

    missing = {"revision", "down_revision"} - values.keys()
    if missing:
        missing_names = ", ".join(sorted(missing))
        raise ValueError(f"{path.name} is missing top-level migration metadata: {missing_names}")

    revision = values["revision"]
    parents = values["down_revision"]
    if not isinstance(revision, str) or not revision.strip():
        raise ValueError(f"{path.name} has an invalid revision identifier: {revision!r}")
    if isinstance(parents, list):
        parents = tuple(parents)
    if parents is not None and not (
        isinstance(parents, str) and parents.strip()
        or isinstance(parents, tuple) and parents and all(isinstance(parent, str) and parent.strip() for parent in parents)
    ):
        raise ValueError(f"{path.name} has invalid down_revision metadata: {parents!r}")
    return revision, parents


def _parents_as_tuple(parents: RevisionParents) -> tuple[str, ...]:
    if parents is None:
        return ()
    return (parents,) if isinstance(parents, str) else parents


def test_alembic_revision_graph_is_connected_and_payment_migration_reaches_head():
    metadata = [_revision_metadata(path) for path in sorted(VERSIONS.glob("*.py"))]
    revision_to_parent = dict(metadata)
    assert len(revision_to_parent) == len(metadata), "duplicate Alembic revision IDs"
    revisions = set(revision_to_parent)
    referenced_parents = {
        parent
        for parents in revision_to_parent.values()
        for parent in _parents_as_tuple(parents)
    }
    unresolved = referenced_parents - revisions
    assert not unresolved, f"migrations reference missing parents: {sorted(unresolved)}"

    roots = {revision for revision, parents in revision_to_parent.items() if parents is None}
    heads = revisions - referenced_parents
    assert len(roots) == 1, f"expected one Alembic root, found {sorted(roots)}"
    assert len(heads) == 1, f"expected one Alembic head, found {sorted(heads)}"

    children: dict[str, list[str]] = {revision: [] for revision in revisions}
    for revision, parents in revision_to_parent.items():
        for parent in _parents_as_tuple(parents):
            children[parent].append(revision)
    reachable = set()
    pending = list(roots)
    while pending:
        revision = pending.pop()
        if revision in reachable:
            continue
        reachable.add(revision)
        pending.extend(children[revision])
    assert reachable == revisions, f"unreachable or cyclic migrations: {sorted(revisions - reachable)}"

    visit_state: dict[str, int] = {}

    def visit(revision: str) -> None:
        if visit_state.get(revision) == 1:
            raise AssertionError(f"cycle detected in Alembic history at {revision}")
        if visit_state.get(revision) == 2:
            return
        visit_state[revision] = 1
        for child in children[revision]:
            visit(child)
        visit_state[revision] = 2

    for revision in revisions:
        visit(revision)

    assert revision_to_parent["20261014_03"] == "20261014_02"
    assert revision_to_parent["20261015_01"] == "20261014_03"
    assert "20261014_03" in _ancestors(next(iter(heads)), revision_to_parent)


def _ancestors(revision: str, revision_to_parent: dict[str, RevisionParents]) -> set[str]:
    ancestors = set()
    pending = list(_parents_as_tuple(revision_to_parent[revision]))
    while pending:
        parent = pending.pop()
        if parent in ancestors:
            continue
        ancestors.add(parent)
        pending.extend(_parents_as_tuple(revision_to_parent[parent]))
    return ancestors


def test_revision_metadata_rejects_files_missing_required_fields(tmp_path):
    malformed = [
        ("revision = 'r1'\n", "down_revision"),
        ("down_revision = None\n", "revision"),
    ]
    for source, missing_field in malformed:
        path = tmp_path / "malformed_migration.py"
        path.write_text(source)
        try:
            _revision_metadata(path)
        except ValueError as error:
            assert missing_field in str(error)
        else:
            raise AssertionError(f"missing {missing_field} should be rejected")


def test_pg_order_id_has_one_schema_migration_and_matching_unique_index():
    migration_sources = {
        path.name: path.read_text()
        for path in VERSIONS.glob("*.py")
        if "pg_order_id" in path.read_text()
    }
    assert set(migration_sources) == {"20261014_03_payment_midtrans_order_id.py"}
    migration = migration_sources["20261014_03_payment_midtrans_order_id.py"]
    assert 'sa.Column("pg_order_id", sa.String(100), nullable=True)' in migration
    assert 'op.create_index("uq_payments_pg_order_id", "payments", ["pg_order_id"], unique=True)' in migration

    assert Payment.__table__.c.pg_order_id.type.length == 100
    indexes = [index for index in Payment.__table__.indexes if index.name == "uq_payments_pg_order_id"]
    assert len(indexes) == 1
    assert indexes[0].unique is True
    assert tuple(column.name for column in indexes[0].columns) == ("pg_order_id",)
