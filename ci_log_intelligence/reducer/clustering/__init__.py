from __future__ import annotations

from typing import Iterable, List

from ...models import Anchor, AnchorCluster, ParsedLine


def build_clusters(anchors: Iterable[Anchor], parsed_lines: Iterable[ParsedLine]) -> List[AnchorCluster]:
    anchors_list = sorted(
        anchors,
        key=lambda anchor: (anchor.line_number, -anchor.severity, anchor.type),
    )
    if not anchors_list:
        return []

    line_to_step = {line.line_number: line.step_id for line in parsed_lines}
    clusters: list[AnchorCluster] = []
    current_anchors: list[Anchor] = [anchors_list[0]]
    current_step = line_to_step.get(anchors_list[0].line_number)
    cluster_index = 1

    for anchor in anchors_list[1:]:
        anchor_step = line_to_step.get(anchor.line_number)
        previous_anchor = current_anchors[-1]
        is_same_step = anchor_step == current_step
        is_close = (anchor.line_number - previous_anchor.line_number) < 10

        if is_same_step and is_close:
            current_anchors.append(anchor)
            continue

        clusters.append(
            AnchorCluster(
                cluster_id=f"cluster-{cluster_index}",
                anchors=list(current_anchors),
                step_id=current_step,
            )
        )
        cluster_index += 1
        current_anchors = [anchor]
        current_step = anchor_step

    clusters.append(
        AnchorCluster(
            cluster_id=f"cluster-{cluster_index}",
            anchors=list(current_anchors),
            step_id=current_step,
        )
    )
    return clusters


__all__ = ["build_clusters"]
