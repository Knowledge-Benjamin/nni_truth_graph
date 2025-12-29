import React, { useState } from 'react';
import axios from 'axios';
import TruthGraph from './components/TruthGraph';
import SearchBar from './components/SearchBar';
import ClaimCard from './components/ClaimCard';
import './App.css';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:3000/api';

function App() {
  const [activeTab, setActiveTab] = useState('search'); // 'search' | 'submit'
  const [text, setText] = useState('');
  const [loading, setLoading] = useState(false);
  const [graphElements, setGraphElements] = useState([]);
  const [searchResults, setSearchResults] = useState([]);
  const [viewMode, setViewMode] = useState('list'); // 'list' | 'graph'
  const [analysis, setAnalysis] = useState(null);

  // --- Search Logic ---
  const handleSearch = (data) => {
    // data = { cypher, explanation, results, analysis }
    console.log("Search results:", data);

    setAnalysis(data.analysis || null);

    // Map Neo4j results to expected Claim format
    const mappedResults = data.results.map(r => {
      // Logic depends on what the Cypher query returns. 
      // Assuming generic return or Claim return.
      // If result is a node, r.c or plain r properties
      const claim = r.c || r.claim || r;
      const val_statement = claim.statement || claim["c.statement"] || r["c.statement"] || "Unknown Statement";

      return {
        id: claim.id || 'unknown',
        statement: val_statement,
        confidence: claim.confidence || 0.5,
        first_seen: claim.first_seen,
        last_verified: claim.last_verified,
        controversy_score: claim.controversy_score || 0
      };
    });

    setSearchResults(mappedResults);
    setViewMode('list');
  };

  const handleViewGraph = async (claimId) => {
    setLoading(true);
    setGraphElements([]); // Clear previous graph
    try {
      if (!claimId) throw new Error("Invalid Claim ID");

      const response = await axios.get(`${API_URL}/claim_graph/${encodeURIComponent(claimId)}`);

      if (response.data && response.data.elements) {
        setGraphElements(response.data.elements);
        setViewMode('graph');
      } else {
        throw new Error("No graph data found");
      }
    } catch (e) {
      console.error("Graph Fetch Error:", e);
      alert("Failed to load graph for this claim. See console.");
    } finally {
      setLoading(false);
    }
  };

  const handleNodeClick = async (nodeId) => {
    // Progressive Reveal: Fetch neighbors and merge
    try {
      console.log("Expanding node:", nodeId);
      const response = await axios.get(`${API_URL}/graph/neighbors/${encodeURIComponent(nodeId)}`);
      const newElements = response.data.elements || [];

      setGraphElements(prev => {
        // Merge avoiding duplicates
        const existingIds = new Set(prev.map(el => el.data.id));
        const uniqueNew = newElements.filter(el => !existingIds.has(el.data.id));

        // Also dedupe edges by source-target-label combo? 
        // Cytoscape handles duplicate IDs by simpler 'first wins' but strict unique IDs are better.
        return [...prev, ...uniqueNew];
      });
    } catch (error) {
      console.error("Expansion failed:", error);
    }
  };

  // --- Submission Logic ---
  const handleAnalyze = async () => {
    if (!text) return;
    setLoading(true);
    setGraphElements([]);
    try {
      const response = await fetch(`${import.meta.env.VITE_API_URL || 'http://localhost:3000'}/api/query/natural`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          title: "User Submission",
          text: text
        })
      });

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const data = await response.json();
      const extractedClaims = data.data;
      // Also build graph for visualization
      const elements = buildGraphFromClaims(extractedClaims);
      setGraphElements(elements);
      setViewMode('graph');

    } catch (error) {
      console.error("Error analyzing text:", error);
      alert("Analysis failed. See console.");
    } finally {
      setLoading(false);
    }
  };

  // Helper to convert extraction result to Cytoscape elements
  const buildGraphFromClaims = (claims) => {
    const elements = [];
    // Article Node
    elements.push({ data: { id: 'article_1', label: 'Article', type: 'Article' } });

    claims.forEach((claim, idx) => {
      const claimId = `claim_${idx}`;
      const lastUpdated = new Date(claim.last_updated).toLocaleString();

      elements.push({
        data: {
          id: claimId,
          label: claim.statement,
          type: 'Claim',
          details: {
            confidence: (claim.confidence * 100).toFixed(1),
            lastUpdated: lastUpdated,
            text: claim.statement
          }
        }
      });
      elements.push({ data: { source: 'article_1', target: claimId, label: 'MENTIONS' } });

      if (claim.sources) {
        claim.sources.forEach((src, srcIdx) => {
          const sourceId = `src_${idx}_${srcIdx}`;
          let nodeType = 'Source';
          let edgeLabel = 'CITES';
          if (src.rating === 'Verified') { nodeType = 'VerifiedSource'; edgeLabel = 'VERIFIED_BY'; }
          else if (src.stance === 'SUPPORT') { nodeType = 'SupportSource'; edgeLabel = 'SUPPORTS'; }
          else if (src.stance === 'CONTRADICT') { nodeType = 'ContradictSource'; edgeLabel = 'CONTRADICTS'; }

          elements.push({
            data: {
              id: sourceId, label: src.publisher || 'Source', type: nodeType,
              url: src.url, date: src.published_date, snippet: src.snippet
            }
          });
          elements.push({
            data: { source: claimId, target: sourceId, label: edgeLabel }
          });
        });
      }
    });
    return elements;
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
          <div style={{ textAlign: 'center', marginBottom: '60px' }}>
            <h2 style={{ fontSize: '3rem', fontWeight: '800', marginBottom: '16px', background: 'linear-gradient(180deg, #fff, #888)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
              Verify Information Instantly
            </h2>
            <p style={{ color: 'var(--text-secondary)', fontSize: '1.2rem', maxWidth: '600px', margin: '0 auto' }}>
              Ask naturally. Get evidence-backed answers from the global Knowledge Graph.
            </p>
          </div>

          <SearchBar onSearch={handleSearch} />

          {viewMode === 'list' && (
            <div className="search-results" style={{ marginTop: '40px', maxWidth: '800px', margin: '40px auto' }}>

              {/* AI Analysis Block */}
              {analysis && (
                <div className="analysis-box" style={{
                  marginBottom: '32px',
                  padding: '0 8px'
                }}>
                  <p style={{
                    margin: 0,
                    lineHeight: '1.6',
                    fontSize: '1.1rem',
                    color: 'var(--text-primary)',
                    whiteSpace: 'pre-wrap'
                  }}>
                    {analysis}
                  </p>
                </div>
              )}

              {searchResults.length > 0 ? (
                searchResults.map((claim, i) => (
                  <ClaimCard key={i} claim={claim} onViewGraph={handleViewGraph} />
                ))
              ) : (
                <div style={{ textAlign: 'center', color: 'var(--text-secondary)', marginTop: '50px', fontSize: '1.1rem' }}>
                  {/* Placeholder content */}
                  {searchResults.length === 0 && !loading && (
                    <p>No verified claims found. Try a different question.</p>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Shared Graph View */}
        {viewMode === 'graph' && (
          <div style={{ marginTop: '30px' }}>
            <button
              onClick={() => setViewMode('list')}
              style={{
                marginBottom: '20px',
                background: 'transparent',
                border: 'none',
                color: 'var(--accent-color)',
                cursor: 'pointer',
                fontWeight: 600,
                display: 'flex',
                alignItems: 'center',
                gap: '8px'
              }}
            >
              ← Back to Results
            </button>
            <TruthGraph elements={graphElements} onNodeClick={handleNodeClick} />
          </div>
        )}

      </main>
    </div>
  );
}

export default App;
