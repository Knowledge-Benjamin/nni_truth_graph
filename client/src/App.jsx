import React, { useState } from "react";
import axios from "axios";
import TruthGraph from "./components/TruthGraph";
import SearchBar from "./components/SearchBar";
import FactCard from "./components/FactCard";
import "./App.css";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:3000/api";

function App() {
  const [loading, setLoading] = useState(false);
  const [graphElements, setGraphElements] = useState([]);
  const [searchResults, setSearchResults] = useState([]);
  const [viewMode, setViewMode] = useState("list"); // 'list' | 'graph'
  const [analysis, setAnalysis] = useState(null);

  // --- Search Logic ---
  const handleSearch = (data) => {
    // data = { cypher, explanation, results, analysis }
    console.log("Search results:", data);

    setAnalysis(data.analysis || null);

    // Map Neo4j results to expected Fact format
    const mappedResults = data.results.map((r) => {
      // Logic depends on what the Cypher query returns.
      // Assuming generic return or Fact return.
      // If result is a node, r.f or plain r properties
      const fact = r.f || r.fact || r;
      const val_statement =
        fact.statement ||
        fact["f.statement"] ||
        r["f.statement"] ||
        "Unknown Statement";

      return {
        id: fact.id || "unknown",
        statement: val_statement,
        confidence: fact.confidence || 0.5,
        first_seen: fact.first_seen,
        last_verified: fact.last_verified,
        controversy_score: fact.controversy_score || 0,
      };
    });

    setSearchResults(mappedResults);
    setViewMode("list");
  };

  const handleViewGraph = async (factId) => {
    setLoading(true);
    setGraphElements([]); // Clear previous graph
    try {
      if (!factId) throw new Error("Invalid Fact ID");

      const response = await axios.get(
        `${API_URL}/fact_graph/${encodeURIComponent(factId)}`
      );

      if (response.data && response.data.elements) {
        setGraphElements(response.data.elements);
        setViewMode("graph");
      } else {
        throw new Error("No graph data found");
      }
    } catch (e) {
      console.error("Graph Fetch Error:", e);
      alert("Failed to load graph for this fact. See console.");
    } finally {
      setLoading(false);
    }
  };

  const handleNodeClick = async (nodeId) => {
    // Progressive Reveal: Fetch neighbors and merge
    try {
      console.log("Expanding node:", nodeId);
      const response = await axios.get(
        `${API_URL}/graph/neighbors/${encodeURIComponent(nodeId)}`
      );
      const newElements = response.data.elements || [];

      setGraphElements((prev) => {
        // Merge avoiding duplicates
        const existingIds = new Set(prev.map((el) => el.data.id));
        const uniqueNew = newElements.filter(
          (el) => !existingIds.has(el.data.id)
        );

        // Also dedupe edges by source-target-label combo?
        // Cytoscape handles duplicate IDs by simpler 'first wins' but strict unique IDs are better.
        return [...prev, ...uniqueNew];
      });
    } catch (error) {
      console.error("Expansion failed:", error);
    }
  };

  return (
    <div className="app-container">
      {/* Header */}
      <header className="app-header">
        <h1 className="logo-text">NNI Truth Graph</h1>
        <span className="beta-tag">Beta</span>
      </header>

      {/* Main Content */}
      <main className="main-content">
        <div>
          <div style={{ textAlign: "center", marginBottom: "60px" }}>
            <h2
              style={{
                fontSize: "3rem",
                fontWeight: "800",
                marginBottom: "16px",
                background: "linear-gradient(180deg, #fff, #888)",
                WebkitBackgroundClip: "text",
                WebkitTextFillColor: "transparent",
              }}
            >
              Verify Information Instantly
            </h2>
            <p
              style={{
                color: "var(--text-secondary)",
                fontSize: "1.2rem",
                maxWidth: "600px",
                margin: "0 auto",
              }}
            >
              Ask naturally. Get evidence-backed answers from the global
              Knowledge Graph.
            </p>
          </div>

          <SearchBar onSearch={handleSearch} />

          {viewMode === "list" && (
            <div
              className="search-results"
              style={{
                marginTop: "40px",
                maxWidth: "800px",
                margin: "40px auto",
              }}
            >
              {/* AI Analysis Block */}
              {analysis && (
                <div
                  className="analysis-box"
                  style={{
                    marginBottom: "32px",
                    padding: "0 8px",
                  }}
                >
                  <p
                    style={{
                      margin: 0,
                      lineHeight: "1.6",
                      fontSize: "1.1rem",
                      color: "var(--text-primary)",
                      whiteSpace: "pre-wrap",
                    }}
                  >
                    {analysis}
                  </p>
                </div>
              )}

              {searchResults.length > 0 ? (
                searchResults.map((fact, i) => (
                  <FactCard key={i} fact={fact} onViewGraph={handleViewGraph} />
                ))
              ) : (
                <div
                  style={{
                    textAlign: "center",
                    color: "var(--text-secondary)",
                    marginTop: "50px",
                    fontSize: "1.1rem",
                  }}
                >
                  {/* Placeholder content */}
                  {searchResults.length === 0 && !loading && (
                    <p>No verified facts found. Try a different question.</p>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Shared Graph View */}
        {viewMode === "graph" && (
          <div style={{ marginTop: "30px" }}>
            <button
              onClick={() => setViewMode("list")}
              style={{
                marginBottom: "20px",
                background: "transparent",
                border: "none",
                color: "var(--accent-color)",
                cursor: "pointer",
                fontWeight: 600,
                display: "flex",
                alignItems: "center",
                gap: "8px",
              }}
            >
              ← Back to Results
            </button>
            <TruthGraph
              elements={graphElements}
              onNodeClick={handleNodeClick}
            />
          </div>
        )}
      </main>
    </div>
  );
}

export default App;
