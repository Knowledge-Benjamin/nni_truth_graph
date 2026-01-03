import React, { useState, useEffect } from "react";
import CytoscapeComponent from "react-cytoscapejs";

const TruthGraph = ({ elements, onNodeClick }) => {
  const [cy, setCy] = useState(null);
  const [popup, setPopup] = useState(null);

  useEffect(() => {
    if (cy) {
      cy.on("tap", "node", (evt) => {
        const node = evt.target;
        const data = node.data();

        // Trigger Expansion
        if (onNodeClick) {
          onNodeClick(data.id);
        }

        // Calculate position
        const renderedPosition = node.renderedPosition();

        setPopup({
          data: data,
          x: renderedPosition.x,
          y: renderedPosition.y,
        });
      });

      cy.on("tap", (evt) => {
        if (evt.target === cy) {
          setPopup(null);
        }
      });
    }
  }, [cy]);

  const layout = {
    name: "cose",
    animate: true,
    nodeDimensionsIncludeLabels: true,
    fit: true,
    padding: 50,
    randomize: false,
    componentSpacing: 200,
    nodeRepulsion: function (node) {
      return 450000;
    }, // Reduced from 2M to 450k
    idealEdgeLength: function (edge) {
      return 100;
    },
    edgeElasticity: function (edge) {
      return 100;
    },
    nestingFactor: 5,
    gravity: 0.25,
    numIter: 1000,
    initialTemp: 200,
    coolingFactor: 0.95,
    minTemp: 1.0,
  };

  const style = [
    {
      selector: "node",
      style: {
        "background-color": "#2a2a2a",
        label: "data(label)",
        color: "#ececec",
        "text-valign": "center",
        "text-halign": "center",
        "text-outline-width": 0,
        width: "label",
        height: "label",
        padding: "16px",
        shape: "round-rectangle",
        "text-wrap": "wrap",
        "text-max-width": "200px",
        "font-family": "Inter, sans-serif",
        "font-size": "12px",
        "border-width": 2,
        "border-color": "#444",
      },
    },
    {
      selector: 'node[type="Article"]',
      style: {
        "background-color": "#0074D9",
        shape: "ellipse",
        width: 100,
        height: 100,
        "font-size": "16px",
        "font-weight": "bold",
        "border-color": "#0053a0",
      },
    },
    {
      selector: 'node[type="Fact"]',
      style: {
        "background-color": "#111",
        "border-color": "#0074D9",
        "border-width": 2,
        shape: "round-rectangle",
        padding: "20px",
      },
    },
    {
      selector: 'node[type="VerifiedSource"]',
      style: {
        "background-color": "#01FF70", // Bright Green
        shape: "ellipse",
        width: 70,
        height: 70,
        "border-width": 4,
        "border-color": "#01FF70",
        "font-weight": "bold",
        color: "#fff",
      },
    },
    {
      selector: 'node[type="SupportSource"]',
      style: {
        "background-color": "#2ECC40", // Green
        shape: "ellipse",
        width: 60,
        height: 60,
        "border-width": 2,
        "border-color": "#2ECC40",
        color: "#ddd",
      },
    },
    {
      selector: 'node[type="ContradictSource"]',
      style: {
        "background-color": "#FF4136", // Red
        shape: "ellipse",
        width: 60,
        height: 60,
        "border-width": 2,
        "border-color": "#FF4136",
        color: "#ddd",
      },
    },
    {
      selector: 'node[type="Source"]',
      style: {
        "background-color": "#AAAAAA", // Gray (Neutral)
        shape: "ellipse",
        width: 40,
        height: 40,
        "border-width": 0,
        color: "#000",
      },
    },
    {
      selector: "edge",
      style: {
        width: 2,
        "line-color": "#555",
        "target-arrow-color": "#555",
        "target-arrow-shape": "triangle",
        "curve-style": "bezier",
        label: "data(label)",
        "font-size": "10px",
        color: "#888",
        "text-background-opacity": 1,
        "text-background-color": "#1a1a1a",
        "text-background-padding": 3,
        "text-background-shape": "round-rectangle",
      },
    },
  ];

  return (
    <div style={{ position: "relative" }}>
      <div
        style={{
          border: "1px solid #333",
          height: "800px",
          margin: "20px 0",
          borderRadius: "12px",
          overflow: "hidden",
          background: "#0d0d0d",
          boxShadow: "0 4px 20px rgba(0,0,0,0.5)",
        }}
      >
        <CytoscapeComponent
          elements={CytoscapeComponent.normalizeElements(elements)}
          style={{ width: "100%", height: "100%" }}
          layout={layout}
          stylesheet={style}
          cy={(cyInstance) => setCy(cyInstance)}
        />
      </div>

      {popup && (
        <div
          style={{
            position: "absolute",
            top: 50,
            left: "50%",
            transform: "translateX(-50%)",
            background: "rgba(20, 20, 20, 0.95)",
            border: "1px solid #444",
            padding: "20px",
            borderRadius: "8px",
            zIndex: 100,
            color: "#fff",
            width: "300px",
            boxShadow: "0 10px 30px rgba(0,0,0,0.8)",
          }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "10px",
            }}
          >
            <h3 style={{ margin: 0, fontSize: "16px", color: "#0074D9" }}>
              {popup.data.type}
            </h3>
            <button
              onClick={() => setPopup(null)}
              style={{
                background: "none",
                border: "none",
                color: "#666",
                cursor: "pointer",
              }}
            >
              X
            </button>
          </div>

          {popup.data.type === "Fact" && (
            <div>
              <p style={{ fontSize: "14px", marginBottom: "10px" }}>
                {popup.data.details.text}
              </p>
              <div style={{ fontSize: "12px", color: "#888" }}>
                <p>
                  Confidence:{" "}
                  <strong
                    style={{
                      color:
                        popup.data.details.confidence > 80
                          ? "#2ECC40"
                          : "#FFDC00",
                    }}
                  >
                    {popup.data.details.confidence}%
                  </strong>
                </p>
                <p>Last Updated: {popup.data.details.lastUpdated}</p>
              </div>
            </div>
          )}

          {popup.data.type.includes("Source") && (
            <div>
              <p style={{ fontSize: "14px", fontWeight: "bold" }}>
                {popup.data.label}
              </p>
              <p
                style={{
                  fontSize: "12px",
                  color: "#ccc",
                  margin: "5px 0",
                  fontStyle: "italic",
                }}
              >
                "{popup.data.snippet}"
              </p>
              <p style={{ fontSize: "12px", color: "#aaa" }}>
                Published: {popup.data.date}
              </p>
              <a
                href={popup.data.url}
                target="_blank"
                rel="noopener noreferrer"
                style={{ color: "#0074D9", fontSize: "12px" }}
              >
                Visit Source
              </a>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default TruthGraph;
