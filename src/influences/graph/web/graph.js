// Thin driver around Cytoscape.js. Python (InfluenceGraphView) calls only the
// window.* entry points below, one-directionally via runJavaScript().
(function () {
  let cy = null;
  let currentLayoutOptions = null;

  const tooltipEl = document.getElementById("node-tooltip");
  const loadingEl = document.getElementById("loading-overlay");

  // Python shows the scrim before a recompute/relayout; layoutstop hides it.
  window.showLoading = function () {
    if (loadingEl) loadingEl.classList.remove("hidden");
  };
  window.hideLoading = function () {
    if (loadingEl) loadingEl.classList.add("hidden");
  };

  // Display candidates, most to least informative: full name, real aliases
  // (longest first), "C. Aguilera"-style initials, then all initials.
  function aliasCandidates(name, realAliases) {
    const lowerName = name.toLowerCase();
    const aliases = (realAliases || []).filter((a) => a && a.toLowerCase() !== lowerName);

    const words = name.split(" ").filter(Boolean);
    if (words.length <= 1) return [name, ...aliases];
    const last = words[words.length - 1];
    const initials = (list) => list.map((w) => w[0].toUpperCase() + ".").join(" ");
    return [name, ...aliases, `${initials(words.slice(0, -1))} ${last}`, initials(words)];
  }

  // Pick the first candidate whose auto-sized box fits the influence-based
  // target (minWidth/minHeight) within SIZE_ALLOWANCE, so box size tracks
  // influence rather than name length. Falls back to the smallest candidate;
  // the box never shrinks below the target.
  const SIZE_ALLOWANCE = 1.25;

  function fitNodeLabel(node) {
    const fullLabel = node.data("fullLabel");
    if (!fullLabel) return;
    const minW = node.data("minWidth") || 0;
    const minH = node.data("minHeight") || 0;
    const realAliases = node.data("aliases") || [];

    function measure(label) {
      node.data("label", label);
      node.style({ width: "label", height: "label" });
      return { w: node.width(), h: node.height() };
    }

    let best = null;
    for (const candidate of aliasCandidates(fullLabel, realAliases)) {
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

  // fcose has no hard non-overlap constraint, so after every layout push any
  // pairs closer than `padding` apart along their shallower axis. Bounding
  // boxes are cached per pass and pairs are pruned with an x-sorted sweep; a
  // pass with no moves sees fresh, sorted boxes, so the end state is exact.
  function resolveOverlaps() {
    if (!cy) return;
    const nodes = cy.nodes("[parent]");
    const n = nodes.length;
    if (n < 2) return;
    const padding = 4;
    const maxIterations = 80;

    function shift(box, axis, delta) {
      if (axis === "x") {
        box.x1 += delta;
        box.x2 += delta;
      } else {
        box.y1 += delta;
        box.y2 += delta;
      }
    }

    for (let iter = 0; iter < maxIterations; iter++) {
      const boxes = [];
      nodes.forEach((node) => {
        const bb = node.boundingBox({ includeLabels: true });
        boxes.push({ node, x1: bb.x1, x2: bb.x2, y1: bb.y1, y2: bb.y2 });
      });
      boxes.sort((p, q) => p.x1 - q.x1);

      let moved = false;
      for (let i = 0; i < n; i++) {
        const a = boxes[i];
        for (let j = i + 1; j < n; j++) {
          const b = boxes[j];
          // Sorted by x1: every later box starts even further right.
          if (b.x1 - a.x2 >= padding) break;
          const overlapX = Math.min(a.x2, b.x2) - Math.max(a.x1, b.x1);
          const overlapY = Math.min(a.y2, b.y2) - Math.max(a.y1, b.y1);
          if (overlapX <= -padding || overlapY <= -padding) continue;

          moved = true;
          const aPos = a.node.position();
          const bPos = b.node.position();
          const axis = overlapX < overlapY ? "x" : "y";
          const push = ((axis === "x" ? overlapX : overlapY) + padding) / 2;
          const sign = bPos[axis] - aPos[axis] >= 0 ? 1 : -1;
          a.node.position(axis, aPos[axis] - sign * push);
          b.node.position(axis, bPos[axis] + sign * push);
          shift(a, axis, -sign * push);
          shift(b, axis, sign * push);
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
    // node[parent] = artist nodes only, not the community compounds.
    cy.on("mouseover", "node[parent]", (evt) => {
      const node = evt.target;
      node.addClass("hovered");
      // Always show the full name: the label can be an alias.
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
      // Update in place: cy.destroy() blanks the canvas for a frame. Handlers stay bound.
      cy.style(style);
      cy.elements().remove();
      cy.add(elements);
      applyFitNodeLabel(cy.nodes("[parent]"));
      cy.layout(layoutOptions).run();
      return;
    }
    // No constructor layout: nodes must be label-fitted before fcose runs.
    cy = cytoscape({
      container: document.getElementById("cy"),
      elements: elements,
      style: style,
      userZoomingEnabled: true,
      userPanningEnabled: true,
      boxSelectionEnabled: false,
      // Read-only view: lock nodes so a drag pans instead of moving a node.
      autoungrabify: true,
    });
    applyFitNodeLabel(cy.nodes("[parent]"));
    cy.on("layoutstop", () => {
      resolveOverlaps();
      window.hideLoading();
    });
    attachInteractionHandlers();
    cy.layout(layoutOptions).run();
  };

  window.clearGraph = function () {
    if (cy) cy.elements().remove();
    hideTooltip();
  };

  window.fitView = function () {
    if (cy) {
      cy.fit(undefined, 40);
    }
  };

  // Center on one node and pulse its underlay (a stronger "hovered" look).
  window.focusNode = function (id) {
    if (!cy) return;
    const ele = cy.getElementById(id);
    if (!ele || !ele.length) return;
    cy.stop();
    cy.animate({ fit: { eles: ele, padding: 180 } }, { duration: 450, easing: "ease-out-cubic" });
    ele.stop(true);
    ele
      .animate(
        { style: { "underlay-opacity": 0.55, "underlay-padding": 14, "z-index": 999 } },
        { duration: 280, easing: "ease-out-cubic" }
      )
      .animate(
        { style: { "underlay-opacity": 0, "underlay-padding": 0, "z-index": 0 } },
        { duration: 900, easing: "ease-in-cubic" }
      );
  };

  // Renames a community compound, or refits an artist node's label.
  window.setLabel = function (elementId, label) {
    if (!cy) return;
    const ele = cy.getElementById(elementId);
    if (ele && ele.length) {
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
    const added = cy.add(elements);
    applyFitNodeLabel(added.filter("node[parent]"));
    if (currentLayoutOptions) {
      const opts = Object.assign({}, currentLayoutOptions, {
        fit: false,
        randomize: false,
      });
      cy.layout(opts).run();
    }
  };
})();
