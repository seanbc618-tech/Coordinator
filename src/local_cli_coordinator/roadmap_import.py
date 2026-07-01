"""Safe local markdown import for roadmap draft nodes and edges."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from .roadmap_graph import add_roadmap_edge, record_roadmap_snapshot, upsert_roadmap_node

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_CHECKLIST_RE = re.compile(r"^[-*]\s+\[[ xX]\]\s+(.+)$")


def _resolve_repo_path(repo_root: Path, path: Path) -> Path:
    repo = repo_root.resolve()
    candidate = path if path.is_absolute() else (repo / path)
    resolved = candidate.resolve()
    try:
        resolved.relative_to(repo)
    except ValueError as exc:
        raise ValueError("roadmap_import_outside_repo") from exc
    return resolved


def _parse_markdown(text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    proposed_nodes: list[dict[str, Any]] = []
    proposed_edges: list[dict[str, Any]] = []
    heading_stack: list[tuple[int, str, str]] = []
    checklist_index = 0

    for line in text.splitlines():
        heading_match = _HEADING_RE.match(line.strip())
        if heading_match:
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()
            node_key = f"heading:{title}"
            proposed_nodes.append(
                {
                    "key": node_key,
                    "node_type": "milestone",
                    "title": title,
                    "priority": max(10, 100 - level * 10),
                }
            )
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            if heading_stack:
                parent_key = heading_stack[-1][2]
                proposed_edges.append(
                    {
                        "from_key": parent_key,
                        "to_key": node_key,
                        "relation": "enables",
                        "rationale": "parent heading enables child",
                    }
                )
            heading_stack.append((level, title, node_key))
            continue

        checklist_match = _CHECKLIST_RE.match(line.strip())
        if checklist_match and heading_stack:
            title = checklist_match.group(1).strip()
            checklist_index += 1
            node_key = f"checklist:{checklist_index}"
            proposed_nodes.append(
                {
                    "key": node_key,
                    "node_type": "backlog",
                    "title": title,
                    "priority": 60,
                }
            )
            parent_key = heading_stack[-1][2]
            proposed_edges.append(
                {
                    "from_key": parent_key,
                    "to_key": node_key,
                    "relation": "enables",
                    "rationale": "heading enables checklist item",
                }
            )

    return proposed_nodes, proposed_edges


def import_roadmap_markdown(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    repo_root: Path,
    path: Path,
    apply: bool = False,
) -> dict[str, Any]:
    resolved = _resolve_repo_path(repo_root, path)
    if not resolved.is_file():
        raise ValueError(f"roadmap file not found: {resolved}")

    text = resolved.read_text(encoding="utf-8", errors="replace")
    proposed_nodes, proposed_edges = _parse_markdown(text)
    warnings: list[str] = []
    if not proposed_nodes:
        warnings.append("no roadmap nodes parsed")

    result: dict[str, Any] = {
        "applied": False,
        "source_path": str(resolved),
        "proposed_nodes": proposed_nodes,
        "proposed_edges": proposed_edges,
        "warnings": warnings,
    }
    if not apply:
        return result

    key_to_id: dict[str, str] = {}
    for node in proposed_nodes:
        node_id = upsert_roadmap_node(
            conn,
            project_id=project_id,
            node_type=str(node["node_type"]),
            title=str(node["title"]),
            priority=int(node.get("priority", 50)),
            metadata={"import_key": node["key"]},
            commit=False,
        )
        key_to_id[str(node["key"])] = node_id

    for edge in proposed_edges:
        from_id = key_to_id.get(str(edge["from_key"]))
        to_id = key_to_id.get(str(edge["to_key"]))
        if not from_id or not to_id:
            warnings.append(f"skipped edge {edge}")
            continue
        add_roadmap_edge(
            conn,
            project_id=project_id,
            from_node_id=from_id,
            to_node_id=to_id,
            relation=str(edge["relation"]),
            rationale=str(edge.get("rationale", "")),
            commit=False,
        )

    record_roadmap_snapshot(
        conn,
        project_id=project_id,
        source="roadmap_md",
        summary={
            "source_path": str(resolved),
            "node_count": len(proposed_nodes),
            "edge_count": len(proposed_edges),
        },
        commit=False,
    )
    conn.commit()
    result["applied"] = True
    result["node_ids"] = list(key_to_id.values())
    return result