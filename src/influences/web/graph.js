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
      cy.destroy();
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
