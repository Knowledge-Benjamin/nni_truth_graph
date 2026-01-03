import React, { useState } from "react";

function SearchBar({ onSearch }) {
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const handleSubmit = async () => {
    if (!query.trim()) return;

    setLoading(true);
    setError(null);

    try {
      const API_BASE_URL =
        import.meta.env.VITE_API_URL || "http://localhost:3000";
      const response = await fetch(`${API_BASE_URL}/api/query/natural`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || "Search failed");
      }

      onSearch(data);
    } catch (err) {
      console.error("Search error:", err);
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="search-wrapper">
      <div className="search-input-group">
        <input
          type="text"
          className="search-input"
          placeholder="Ask anything: 'Show me vaccine facts from 2024'"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyPress={(e) => e.key === "Enter" && handleSubmit()}
        />
        <button
          className="search-btn"
          onClick={handleSubmit}
          disabled={loading}
        >
          {loading ? "Thinking..." : "Search"}
        </button>
      </div>
      {error && (
        <p
          style={{
            color: "var(--danger-color)",
            marginTop: "10px",
            fontSize: "14px",
            paddingLeft: "10px",
          }}
        >
          {error}
        </p>
      )}
    </div>
  );
}

export default SearchBar;
