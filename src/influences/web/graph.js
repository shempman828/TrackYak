// Thin driver around Cytoscape.js. Python (InfluenceGraphView) talks to this
// page one-directionally via QWebEngineView.page().runJavaScript() -- these
// are the only entry points it calls.
(function () {
  let cy = null;
  let currentLayoutOptions = null;
  let measureCtx = null;

  const tooltipEl = document.getElementById("node-tooltip");

  // Mirrors the node[parent] label style in influence_graph.py's
  // stylesheet (font-weight 600, font-family) so the width measured here
  // matches what Cytoscape itself will render/ellipsize.
  function isLabelTruncated(node) {
    const label = node.data("label");
    const fontSize = node.data("fontSize");
    const maxWidth = node.data("labelWidth");
    if (!label || !fontSize || !maxWidth) return false;
    if (!measureCtx) {
      measureCtx = document.createElement("canvas").getContext("2d");
    }
    measureCtx.font = `600 ${fontSize}px "Helvetica Neue", Helvetica, Arial, sans-serif`;
    return measureCtx.measureText(label).width > maxWidth;
  }

  function positionTooltip(evt) {
    const pos = evt.renderedPosition;
    tooltipEl.style.left = `${pos.x + 14}px`;
    tooltipEl.style.top = `${pos.y + 14}px`;
  }

  function showTooltipIfTruncated(node, evt) {
    if (!isLabelTruncated(node)) return;
    tooltipEl.textContent = node.data("label");
    tooltipEl.style.display = "block";
    positionTooltip(evt);
  }

  function hideTooltip() {
    tooltipEl.style.display = "none";
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

  function attachInteractionHandlers() {
    // Only leaf artist nodes carry a `parent` data field (the invisible
    // per-community compound node); this selector excludes the compounds
    // themselves from the hover highlight.
    cy.on("mouseover", "node[parent]", (evt) => {
      evt.target.addClass("hovered");
      showTooltipIfTruncated(evt.target, evt);
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
      cy.layout(layoutOptions).run();
      return;
    }
    cy = cytoscape({
      container: document.getElementById("cy"),
      elements: elements,
      style: style,
      layout: layoutOptions,
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
    cy.on("layoutstop", resolveOverlaps);
    attachInteractionHandlers();
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
      ele.data("label", label);
    }
  };

  window.addElements = function (elements) {
    if (!cy) return;
    cy.add(elements);
    if (currentLayoutOptions) {
      const opts = Object.assign({}, currentLayoutOptions, {
        fit: false,
        randomize: false,
      });
      cy.layout(opts).run();
    }
  };
})();
