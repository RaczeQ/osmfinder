"""
OpenStreetMap extracts tree.

This module contains function for displaying available extracts in the tree form.
"""

from typing import Any, Callable

import numpy as np
from rich.tree import Tree

from osmfinder._typing import OsmExtractsIndex
from osmfinder.extract import OsmExtractSource


def get_available_extracts_as_rich_tree(
    source_enum: OsmExtractSource,
    osm_extract_source_index_functions: dict[OsmExtractSource, Callable[..., OsmExtractsIndex]],
    use_full_names: bool = False,
) -> Tree:
    """Transform available OSM extracts into a tree from the Rich library."""
    if source_enum == OsmExtractSource.any:
        root = Tree("All extracts")
        for other_source_enum, get_index_function in osm_extract_source_index_functions.items():
            branch_id = other_source_enum.value
            branch = root.add(branch_id)
            _add_index_to_tree(branch_id, branch, get_index_function(), use_full_names)
    else:
        root_id = source_enum.value
        root = Tree(root_id)
        _add_index_to_tree(
            root_id, root, osm_extract_source_index_functions[source_enum](), use_full_names
        )

    return root


def _add_index_to_tree(
    root_id: str, tree: Tree, index: OsmExtractsIndex, use_full_names: bool
) -> None:
    """
    Build Rich tree branches for a single extracts index.

    Children are grouped by their parent only once (``O(N)``), so the tree is built without
    re-filtering the whole index for every node (which would make it ``O(N^2)`` and very slow
    for large flat indexes like the Movisda grid).

    Args:
        root_id (str): Id of the source root used to find top-level children.
        tree (Tree): Tree object from Rich library.
        index (OsmExtractsIndex): List of available OSM extracts.
        use_full_names (bool): Whether to display full name, or short name of the extract.
    """
    children_by_parent: dict[str, list[dict[str, Any]]] = _group_children_by_parent(index)

    create_rich_tree_branch(root_id, tree, children_by_parent, use_full_names)

    # Attach loose parents - referenced as a parent, but not present as an extract id.
    loose_parents = sorted(set(children_by_parent).difference(index.ids).difference([root_id]))
    for loose_parent in loose_parents:
        branch = tree.add(loose_parent)
        create_rich_tree_branch(loose_parent, branch, children_by_parent, use_full_names)


def _group_children_by_parent(index: OsmExtractsIndex) -> dict[str, list[dict[str, Any]]]:
    """Group extract records by their parent id, with children pre-sorted by name."""
    # Sort by name (stable) so children appear alphabetically within each parent group.
    sort_indices = np.argsort(index.names, kind="stable")
    children_by_parent: dict[str, list[dict[str, Any]]] = {}
    for idx in sort_indices:
        parent = str(index.parents[idx])
        children_by_parent.setdefault(parent, []).append(
            {
                "id": str(index.ids[idx]),
                "name": str(index.names[idx]),
                "file_name": str(index.file_names[idx]),
                "url": str(index.urls[idx]),
                "area": float(index.areas[idx]),
            }
        )
    return children_by_parent


def create_rich_tree_branch(
    parent_id: str,
    tree: Tree,
    children_by_parent: dict[str, list[dict[str, Any]]],
    use_full_names: bool,
) -> None:
    """
    Iterate OSM extracts recursively and create tree branches.

    Args:
        parent_id (str): Current id of the extract used to find children.
        tree (Tree): Tree object from Rich library.
        children_by_parent (dict[str, list[dict[str, Any]]]): Extract records grouped by parent id,
            with children pre-sorted by name.
        use_full_names (bool): Whether to display full name, or short name of the extract.
            Full name contains all parents of the extract.
    """
    for matching_child in children_by_parent.get(parent_id, []):
        name = matching_child["file_name"] if use_full_names else matching_child["name"]
        url = matching_child["url"]
        area = human_format(matching_child["area"])
        branch = tree.add(f":globe_with_meridians: [link={url}]{name}[/link] ({area} km\u00b2)")

        create_rich_tree_branch(matching_child["id"], branch, children_by_parent, use_full_names)


def human_format(num: float) -> str:
    """Change big number into a human readable format."""
    num = float(f"{num:.3g}")
    magnitude = 0
    while abs(num) >= 1000:
        magnitude += 1
        num /= 1000.0
    return "{}{}".format(
        f"{num:f}".rstrip(".0"),
        ["", "K", "M", "B"][magnitude],
    )