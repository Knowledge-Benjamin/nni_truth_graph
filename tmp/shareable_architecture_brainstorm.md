# Brainstorming: Citable & Shareable Graph Architecture

To make the Truth Graph a world-class, authoritative system, every piece of knowledge must be a first-class citizen of the web. Just like Wikipedia relies on shareable URLs (`wikipedia.org/wiki/Apple`), the Truth Graph needs to allow users to link directly to specific Nodes (Entities) or specific Facts (Claims).

Here is a comprehensive, professional architecture to achieve this.

## 1. The Core Concept: Permalinks
Currently, the Truth Graph operates as a Single Page Application (SPA). A user searches for "Bible", the graph updates, but the URL remains `localhost:5173/`. 

To fix this, we need **Dynamic Routing**. The URL must reflect the current state of the application.

### Proposed URL Structures
There are two primary distinct "things" a user will want to share:

**A. Sharing an Entity (The "Wikipedia" view)**
If someone wants to share the entire web of knowledge surrounding a concept.
*   **Format:** `https://truthgraph.com/entity/{entity_slug}`
*   **Example:** `https://truthgraph.com/entity/bible` or `https://truthgraph.com/entity/isaac-newton`
*   **Action:** When a user visits this link, the React app boots, automatically searches the graph for `bible`, and renders the network with the "Bible" node centered.

**B. Sharing a Fact/Claim (The "Citation" view)**
If someone wants to cite a specific, granular truth or argument to settle a debate.
*   **Format:** `https://truthgraph.com/claim/{uuid}`
*   **Example:** `https://truthgraph.com/claim/550e8400-e29b-41d4-a716-446655440000`
*   **Action:** When a user visits this link, the React app boots, highlights the specific edge/claim on the canvas, opens the right-hand Inspector pane automatically, and displays the exact quote, source article, and epistemic score of that claim.

## 2. SEO and Social Previews (Open Graph)
When a user pastes a Truth Graph link into Twitter, iMessage, or Discord, it should unfurl into a beautiful, authoritative preview card.

*   To achieve this, the Node.js backend needs to intercept requests to `/entity/*` and `/claim/*` and inject dynamic `<meta property="og:title">` and `<meta property="og:description">` tags into the HTML before sending it to the client.
*   **Entity Preview Example:**
    *   **Image:** A generated graphic of a network node.
    *   **Title:** "Explore 'Jesus' on Truth Graph"
    *   **Description:** "Discover 4,821 interconnected facts, claims, and sources surrounding Jesus..."
*   **Claim Preview Example:**
    *   **Image:** A green "Verified Fact" badge.
    *   **Title:** "The official language of France is French."
    *   **Description:** "Supported by 3 sources. Epistemic Verification Score: 98%."

## 3. Necessary Redesigns and Engineering Tasks

If you agree with this vision, here is the exact technical execution path:

### Phase 1: URL Routing (React)
1.  **Refactor React Router:** Update `App.jsx` to introduce parameter-based routes:
    *   `<Route path="/entity/:name" element={<ExplorerPane />} />`
    *   `<Route path="/claim/:id" element={<ExplorerPane />} />`
2.  **State Synchronization:** Modify `ExplorerPane.jsx` so that on initial load (`useEffect`), it reads the parameters from the URL (via `useParams()`).
    *   If it sees `entity="Apple"`, it automatically triggers the Neo4j search for Apple instead of waiting for the user to type it in the search bar.
3.  **Update URL on Interaction:** Conversely, when a user clicks a node on the canvas (e.g., clicking "Fruit" from the "Apple" web), the browser URL should silently update to `/entity/fruit` without reloading the page via `window.history.pushState`.

### Phase 2: Citation UI
1.  **"Copy Link" Button:** Add a prominent "Copy Link to Fact" (chain-link icon) button inside the Inspector Pane whenever an edge/claim is clicked.
2.  **"Copy Link" Button (Entity):** Add a "Share this Entity" button next to the search bar to allow easy capturing of the current focal point.

### Phase 3: Metadata & SEO (Backend)
Vite SPAs normally serve a static `index.html`. For rich social sharing, we need Server-Side Rendering (SSR) for the metadata.
1.  Update the Express backend to intercept requests for the React app.
2.  When a request hits `/entity/:name`, the server queries Neo4j for basic stats about that entity.
3.  The server injects those stats into the `<head>` of the Vue/React `index.html` file template (using basic string replacement or a lightweight SSR tool) and serves it.

### Phase 4: Unique Identifiers (Slugs)
Currently, entities are just names with spaces (e.g., `United States of America`). 
*   URLs with spaces turn into ugly encoded strings (`/entity/United%20States%20of%20America`).
*   We need to add a small utility to generate **slugs** (e.g., `united-states-of-america`) and store them on the Neo4j `Entity` nodes during ingestion, allowing clean, readable URLs.

## Summary of Impact
Implementing this architecture will transform the Truth Graph from a private analysis tool into a **public knowledge utility**. Academics, journalists, and everyday users will be able to cite the Truth Graph as the definitive, transparent source for facts, directly driving immense organic traffic and authority to your platform.
