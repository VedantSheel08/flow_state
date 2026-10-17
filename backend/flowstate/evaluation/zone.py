"""Measurement scopes for congestion evaluation (docs/CONGESTION_ACCEPTANCE.md).

A scope is fixed *before* a run and never chosen after seeing results. The default scope is the interior
arterial lanes: every motor lane of the Main/King/Queen street edges (main and turn-bay parts), excluding fringe
entry/exit stubs, sidewalks, cycle-only lanes and internal junction lanes. For the Riverside District that is
6.273 lane-km. Density and occupancy always divide by the *whole* scope length, including empty lanes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ..network.info import NetInfo

BlockKey = tuple[str, str, int]  # street, direction, segment index


@dataclass
class Block:
    """One directed physical block: the main part followed by its turn bay (if any), ending at a junction."""

    key: BlockKey
    edges: list[str]  # upstream -> downstream
    to_node: str
    from_node: str
    length: float
    lanes: dict[str, list[str]] = field(default_factory=dict)  # edge -> motor lanes


@dataclass
class Zone:
    id: str
    description: str
    edges: list[str]
    lanes: list[str]
    lane_km: float
    blocks: dict[BlockKey, Block]
    edge_block: dict[str, BlockKey]

    def lane_length(self, net: "NetInfo", lane: str) -> float:
        return net.lanes[lane].length


def _motor_lanes(net: "NetInfo", edge: str) -> list[str]:
    return net.car_lanes(edge)


def build_zone(net: "NetInfo", zone_id: str, kinds: tuple[str, ...], classes: tuple[str, ...] | None, description: str) -> Zone:
    edges = [e.id for e in net.edges.values() if e.kind in kinds and (classes is None or e.cls in classes) and _motor_lanes(net, e.id)]
    lanes = [lane for e in edges for lane in _motor_lanes(net, e)]
    lane_km = sum(net.lanes[l].length for l in lanes) / 1000.0
    grouped: dict[BlockKey, list[str]] = {}
    for e in edges:
        info = net.edges[e]
        if info.kind == "street" and info.street:
            grouped.setdefault((info.street, info.dir, info.seg), []).append(e)
    blocks: dict[BlockKey, Block] = {}
    edge_block: dict[str, BlockKey] = {}
    for key, members in grouped.items():
        # upstream -> downstream: the bay part is downstream of the main part
        members.sort(key=lambda x: (net.edges[x].part == "bay", x))
        first, last = net.edges[members[0]], net.edges[members[-1]]
        blocks[key] = Block(
            key=key, edges=members, to_node=last.to, from_node=first.frm,
            length=sum(net.edges[x].length for x in members),
            lanes={x: _motor_lanes(net, x) for x in members},
        )
        for x in members:
            edge_block[x] = key
    return Zone(zone_id, description, edges, lanes, lane_km, blocks, edge_block)


def interior_arterials(net: "NetInfo") -> Zone:
    return build_zone(net, "interior_arterials", ("street",), ("arterial", "arterial_bike"),
                      "motor lanes of the interior arterial street edges (main + turn bay), no fringe stubs")


def interior_streets(net: "NetInfo") -> Zone:
    return build_zone(net, "interior", ("street",), None, "motor lanes of every interior street edge")


def whole_network(net: "NetInfo") -> Zone:
    return build_zone(net, "network", ("street", "entry", "exit"), None, "motor lanes of every non-internal edge, including fringe stubs")


def blocks_of(zone: Zone) -> list[Block]:
    return list(zone.blocks.values())
