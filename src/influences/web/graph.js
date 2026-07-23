// Thin driver around Cytoscape.js. Python (InfluenceGraphView) talks to this
// page one-directionally via QWebEngineView.page().runJavaScript() -- these
// are the only entry points it calls.
(function () {
  let cy = null;
  let currentLayoutOptions = null;

  function attachInteractionHandlers() {
    // Only leaf artist nodes carry a `parent` data field (the invisible
    // per-community compound node); this selector excludes the compounds
    // themselves from the hover highlight.
    cy.on("mouseover", "node[parent]", (evt) => evt.target.addClass("hovered"));
    cy.on("mouseout", "node[parent]", (evt) => evt.target.removeClass("hovered"));
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
