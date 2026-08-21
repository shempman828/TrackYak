// Thin driver around Cytoscape.js. Python (InfluenceGraphView) talks to this
// page one-directionally via QWebEngineView.page().runJavaScript() -- these
// are the only entry points it calls.
(function () {
  let cy = null;
  let currentLayoutOptions = null;

  const tooltipEl = document.getElementById("node-tooltip");

  // Progressively shorter display candidates for a name, from most to
  // least informative: the real name; initials for every word but the
  // last, e.g. "Christina Aguilera" -> "C. Aguilera" (the last word is
  // usually the most identifying part of an artist/band name); and, only
  // if that's still not enough, initials for every word. Single-word
  // names have nothing to abbreviate, so only the name itself is offered.
  function aliasCandidates(name) {
    const words = name.split(" ").filter(Boolean);
    if (words.length <= 1) return [name];
    const last = words[words.length - 1];
    const initials = (list) => list.map((w) => w[0].toUpperCase() + ".").join(" ");
    return [name, `${initials(words.slice(0, -1))} ${last}`, initials(words)];
  }

  // node[parent] is styled width/height: 'label' with text-wrap: 'wrap'
  // (influence_graph_render.py's _build_stylesheet), so Cytoscape
  // auto-sizes each node's box to exactly contain whatever label text is
  // currently set -- no label can ever overflow its own box. Used alone
  // that would make a low-influence node's box track its *name length*
  // instead of its influence: a minor artist with a long name would
  // render bigger than a major one with a short name (confirmed
  // empirically against the real DB, see scratch/graph_repro/repro.py).
  //
  // Tries each candidate from aliasCandidates in order (most to least
  // informative) against the node's influence-based target
  // (minWidth/minHeight data, from get_node_size), stopping at the first
  // one that fits within a modest allowance -- so box size tracks
  // influence for the common case, and only a genuinely long name (one
  // where even all-initials doesn't fit) still grows its box, rather than
  // being truncated (no name is ever elided). If nothing fits the
  // allowance, falls back to whichever candidate measured smallest.
  // Either way, the result is floored at the influence-based minimum, so
  // the box can grow to fit text but never shrinks below what influence
  // dictates.
  const SIZE_ALLOWANCE = 1.25;

  function fitNodeLabel(node) {
    const fullLabel = node.data("fullLabel");
    if (!fullLabel) return;
    const minW = node.data("minWidth") || 0;
    const minH = node.data("minHeight") || 0;

    function measure(label) {
      node.data("label", label);
      node.style({ width: "label", height: "label" });
      return { w: node.width(), h: node.height() };
    }

    let best = null;
    for (const candidate of aliasCandidates(fullLabel)) {
      const dims = measure(candidate);
      if (!best || dims.w * dims.h < best.dims.w * best.dims.h) {
        best = { label: candidate, dims };
      }
      if (dims.w <= minW * SIZE_ALLOWANCE && dims.h <= minH * SIZE_ALLOWANCE) {
        best = { label: candidate, dims };
        break;
      }
    }
    measure(best.label);
    node.style({
      width: Math.max(best.dims.w, minW),
      height: Math.max(best.dims.h, minH),
    });
  }

  function applyFitNodeLabel(nodes) {
    nodes.forEach(fitNodeLabel);
  }

  // fcose is a force-directed heuristic: it settles at an energy
  // equilibrium that usually keeps nodes apart but has no hard
  // non-overlap constraint, so dense communities can still resolve with
  // pairs touching or overlapping. This deterministic pass runs after
  // every layout settles and pushes any remaining overlapping pairs
  // apart along their shallower axis until none overlap, guaranteeing
  // the end state is overlap-free regardless of what the physics
  // simulation converged to.
  function resolveOverlaps() {
    if (!cy) return;
    const nodes = cy.nodes("[parent]");
    const n = nodes.length;
    if (n < 2) return;
    const padding = 4;
    const maxIterations = 80;
    for (let iter = 0; iter < maxIterations; iter++) {
      let moved = false;
      for (let i = 0; i < n; i++) {
        const a = nodes[i];
        const aBB = a.boundingBox({ includeLabels: true });
        for (let j = i + 1; j < n; j++) {
          const b = nodes[j];
          const bBB = b.boundingBox({ includeLabels: true });
          const overlapX = Math.min(aBB.x2, bBB.x2) - Math.max(aBB.x1, bBB.x1);
          const overlapY = Math.min(aBB.y2, bBB.y2) - Math.max(aBB.y1, bBB.y1);
          if (overlapX <= -padding || overlapY <= -padding) continue;

          moved = true;
          const aPos = a.position();
          const bPos = b.position();
          if (overlapX < overlapY) {
            const push = (overlapX + padding) / 2;
            const sign = bPos.x - aPos.x >= 0 ? 1 : -1;
            a.position("x", aPos.x - sign * push);
            b.position("x", bPos.x + sign * push);
          } else {
            const push = (overlapY + padding) / 2;
            const sign = bPos.y - aPos.y >= 0 ? 1 : -1;
            a.position("y", aPos.y - sign * push);
            b.position("y", bPos.y + sign * push);
          }
        }
      }
      if (!moved) break;
    }
  }

  function positionTooltip(evt) {
    const pos = evt.renderedPosition;
    tooltipEl.style.left = `${pos.x + 14}px`;
    tooltipEl.style.top = `${pos.y + 14}px`;
  }

  function hideTooltip() {
    tooltipEl.style.display = "none";
  }

  function attachInteractionHandlers() {
    // Only leaf artist nodes carry a `parent` data field (the invisible
    // per-community compound node); this selector excludes the compounds
    // themselves from the hover highlight/tooltip.
    cy.on("mouseover", "node[parent]", (evt) => {
      const node = evt.target;
      node.addClass("hovered");
      // The displayed label can be a shortened alias (fitNodeLabel), so
      // always show the real full name on hover -- not just when it
      // differs -- so hovering is a reliable way to confirm identity.
      tooltipEl.textContent = node.data("fullLabel");
      tooltipEl.style.display = "block";
      positionTooltip(evt);
    });
    cy.on("mousemove", "node[parent]", (evt) => {
      if (tooltipEl.style.display === "block") positionTooltip(evt);
    });
    cy.on("mouseout", "node[parent]", (evt) => {
      evt.target.removeClass("hovered");
      hideTooltip();
    });
    cy.on("pan zoom", hideTooltip);
  }

  window.loadGraph = function (elements, style, layoutOptions, bgColor) {
    document.body.style.backgroundColor = bgColor;
    currentLayoutOptions = layoutOptions;

    if (cy) {
      // Update the existing Cytoscape instance in place instead of
      // destroying and recreating it. cy.destroy() tears down the whole
      // rendering canvas, which blanks the entire graph view for a frame
      // on every refresh -- visible as a whole-screen flash since the
      // graph fills nearly the whole tab. Event handlers (layoutstop,
      // hover) are bound once below, at creation, so they must not be
      // re-attached here.
      cy.style(style);
      cy.elements().remove();
      cy.add(elements);
      applyFitNodeLabel(cy.nodes("[parent]"));
      cy.layout(layoutOptions).run();
      return;
    }
    // No `layout` in the constructor -- elements must be sized (see
    // fitNodeLabel) before fcose runs, since its layout decisions depend
    // on each node's final box dimensions.
    cy = cytoscape({
      container: document.getElementById("cy"),
      elements: elements,
      style: style,
      userZoomingEnabled: true,
      userPanningEnabled: true,
      boxSelectionEnabled: false,
      // Nodes/compounds are draggable by default. With hundreds of
      // densely-packed pills covering most of the canvas, a click-drag
      // meant to pan the viewport almost always lands on a node instead
      // and repositions it rather than panning -- this is the read-only
      // layout view, not an editor, so lock every element in place.
      autoungrabify: true,
    });
    applyFitNodeLabel(cy.nodes("[parent]"));
    cy.on("layoutstop", resolveOverlaps);
    attachInteractionHandlers();
    cy.layout(layoutOptions).run();
  };

  window.fitView = function () {
    if (cy) {
      cy.fit(undefined, 40);
    }
  };

  // Used both for renaming a community's compound label and for
  // refreshing a single artist node's label in place.
  window.setLabel = function (elementId, label) {
    if (!cy) return;
    const ele = cy.getElementById(elementId);
    if (ele && ele.length) {
      // Only leaf artist nodes are sized-to-label; compound community
      // nodes (no `parent` data) auto-size to their children instead and
      // have no fullLabel/alias concept.
      if (ele.data("parent")) {
        ele.data("fullLabel", label);
        fitNodeLabel(ele);
      } else {
        ele.data("label", label);
      }
    }
  };

  window.addElements = function (elements) {
    if (!cy) return;
    cy.add(elements);
    applyFitNodeLabel(cy.nodes("[parent]"));
    if (currentLayoutOptions) {
      const opts = Object.assign({}, currentLayoutOptions, {
        fit: false,
        randomize: false,
      });
      cy.layout(opts).run();
    }
  };
})();
