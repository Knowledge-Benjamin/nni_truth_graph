/**
 * Living Truth Graph — Core API Routes
 *
 * All graph queries flow through Neo4j.
 * Human Review resolution also writes back to PostgreSQL.
 */

const { Router } = require('express');
const rateLimit = require('express-rate-limit');
const { authenticateAdmin, requireAdmin } = require('./auth');
const router = Router();

// ─── Rate Limiters ───────────────────────────────────────────────────────────
const searchLimiter = rateLimit({
    windowMs: 1 * 60 * 1000, // 1 minute
    max: 10, // 10 requests per minute
    message: { error: 'Too many search requests. Please try again in 1 minute.' },
    standardHeaders: true,
    legacyHeaders: false,
});


// ─── Helpers ─────────────────────────────────────────────────────────────────

function neo4j(req) { return req.app.locals.neo4j; }
function pgPool(req) { return req.app.locals.pgPool; }

function toPlain(obj) {
    // Convert neo4j integer types to JS numbers
    if (obj === null || obj === undefined) return obj;
    if (typeof obj.toNumber === 'function') return obj.toNumber();
    if (typeof obj === 'object' && !Array.isArray(obj)) {
        return Object.fromEntries(
            Object.entries(obj).map(([k, v]) => [k, toPlain(v)])
        );
    }
    if (Array.isArray(obj)) return obj.map(toPlain);
    return obj;
}

async function getSystemSettings(pool) {
    const client = await pool.connect();
    try {
        const { rows } = await client.query('SELECT key, value FROM system_settings');
        return rows.reduce((acc, row) => {
            acc[row.key] = row.value;
            return acc;
        }, {});
    } catch(e) {
        return {};
    } finally {
        client.release();
    }
}

// ─── GET /api/search?q=...&type=claim|entity ─────────────────────────────────
router.get('/search', searchLimiter, async (req, res) => {
    const { q, type = 'all' } = req.query;
    if (!q) return res.status(400).json({ error: 'q parameter required' });

    const session = neo4j(req).session();
    try {
        const results = {};

        if (type === 'all' || type === 'entity') {
            const er = await session.run(`
        MATCH (e:Entity)
        WHERE toLower(e.name) CONTAINS toLower($q)
        RETURN e.name AS name, e.mention_count AS mentions
        ORDER BY e.mention_count DESC LIMIT 10
      `, { q });
            results.entities = er.records.map(r => toPlain(r.toObject()));
        }

        if (type === 'all' || type === 'claim') {
            const cr = await session.run(`
        MATCH (c:Claim)
        WHERE toLower(c.subject) CONTAINS toLower($q)
           OR toLower(c.object)  CONTAINS toLower($q)
        RETURN c.id AS id, c.subject AS subject, c.predicate AS predicate,
               c.object AS object, c.epistemic_score AS score,
               c.is_current AS is_current, c.lifecycle AS lifecycle
        ORDER BY c.epistemic_score DESC LIMIT 20
      `, { q });
            results.claims = cr.records.map(r => toPlain(r.toObject()));
        }

        // Check if an entity search was performed and yielded results
        const isEntitySearch = (type === 'all' || type === 'entity');
        if (results.entities && results.entities.length > 0 && isEntitySearch) {
            // Track popularity to prioritize LLM article generation
            try {
                // Use a new session for this update to avoid interfering with the read session
                const updateSession = neo4j(req).session();
                await updateSession.run(`
                    MATCH (e:Entity {name: $name})
                    SET e.search_count = coalesce(e.search_count, 0) + 1
                    // Don't auto-regen an article just because it was searched,
                    // but if it's already missing/stale, it will naturally float to the top
                    // of the article_worker's priority queue due to high search_count.
                `, { name: results.entities[0].name });
                await updateSession.close();
            } catch(e) { /* ignore tracking errors */ }
        }

        res.json(results);
    } catch (e) {
        console.error(e);
        res.status(500).json({ error: String(e) });
    } finally {
        await session.close();
    }
});

// ─── Get Entity Article (Living Wikipedia) ──────────────────────────────────
router.get('/entity/:name/article', async (req, res) => {
    const session = neo4j(req).session(); // Use neo4j(req) consistent with other routes
    try {
        const result = await session.run(`
            MATCH (e:Entity) WHERE toLower(e.name) = toLower($name)
            RETURN e.article AS article,
                   e.article_references AS refs,
                   e.article_generated_at AS generated_at,
                   e.article_stale AS stale,
                   e.article_claim_count AS claim_count
        `, { name: req.params.name });

        if (result.records.length === 0) {
            return res.status(404).json({ error: 'Entity not found in graph' });
        }

        const record = result.records[0];
        const article = record.get('article');

        if (!article) {
            return res.status(202).json({
                status: 'generating',
                message: 'This entity is in the queue for article generation. Check back soon.'
            });
        }

        let references = [];
        try {
            references = JSON.parse(record.get('refs') || '[]');
        } catch(e) { console.error('Error parsing article references', e); }

        res.json({
            status: 'ready',
            article: article,
            references: references,
            metadata: {
                generated_at: record.get('generated_at'),
                stale: record.get('stale'),
                claim_count_at_generation: record.get('claim_count')
            }
        });
    } catch (error) {
        console.error('Error fetching article:', error);
        res.status(500).json({ error: 'Failed to fetch article' });
    } finally {
        await session.close();
    }
});

// ─── GET /api/claim/:id ──────────────────────────────────────────────────────
router.get('/claim/:id', async (req, res) => {
    const { id } = req.params;
    const session = neo4j(req).session();
    try {
        const result = await session.run(`
            MATCH (c:Claim {id: $id})
            OPTIONAL MATCH (c)-[:HAS_SUBJECT]->(subj:Entity)
            OPTIONAL MATCH (c)-[:HAS_OBJECT]->(obj:Entity)
            OPTIONAL MATCH (c)-[:SUPPORTED_BY]->(ev:Evidence)
            RETURN c AS claim, subj AS subject, obj AS object, collect(ev) AS evidences
        `, { id });

        if (!result.records.length) return res.status(404).json({ error: 'Claim not found' });

        const rec = result.records[0];
        res.json({
            claim: toPlain(rec.get('claim').properties),
            subject: rec.get('subject') ? toPlain(rec.get('subject').properties) : null,
            object: rec.get('object') ? toPlain(rec.get('object').properties) : null,
            evidences: rec.get('evidences').map(e => toPlain(e.properties))
        });
    } catch (e) {
        console.error(e);
        res.status(500).json({ error: String(e) });
    } finally {
        await session.close();
    }
});

// ─── Groq Key Rotation Pool ───────────────────────────────────────────────────
const _groqKeys = () => {
    const keys = [];
    if (process.env.GROQ_API_KEY && process.env.GROQ_API_KEY.trim()) {
        keys.push(process.env.GROQ_API_KEY.trim());
    }
    for (const [key, val] of Object.entries(process.env)) {
        if (key.startsWith('GROQ_API_KEY_') && val && val.trim() !== '') {
            keys.push(val.trim());
        }
    }
    return [...new Set(keys)];
};

let _groqKeyIdx = 0;
function getNextGroqKey() {
    const keys = _groqKeys();
    if (!keys.length) throw new Error('No GROQ_API_KEY configured.');
    const key = keys[_groqKeyIdx % keys.length];
    _groqKeyIdx++;
    return key;
}

// ─── POST /api/chat (Supreme AI Agent — ReAct Loop) ──────────────────────────
router.post('/chat', async (req, res) => {
    const { messages, context } = req.body;
    // context = { current_entity: string, viewport_entities: string[] }
    if (!messages || !Array.isArray(messages) || messages.length === 0) {
        return res.status(400).json({ error: 'messages array required' });
    }

    const session = neo4j(req).session();
    try {
        // ── 1. Tool Definitions ────────────────────────────────────────────────
        const tools = [
            {
                type: "function",
                function: {
                    name: "search_graph",
                    description: "Full-text keyword search across all claims in Truth. Use simple 2-5 keywords. Always try this first.",
                    parameters: {
                        type: "object",
                        properties: {
                            keywords: { type: "string", description: "2-5 keywords, e.g. 'NASA Artemis lunar' or 'Elon Musk Tesla CEO'" }
                        },
                        required: ["keywords"]
                    }
                }
            },
            {
                type: "function",
                function: {
                    name: "read_entity_article",
                    description: "Read the synthesized Wikipedia-style narrative article for a named entity. Returns rich prose context. Best for 'who is X?' or 'what is X?' questions. Call after search_graph identifies the right entity name.",
                    parameters: {
                        type: "object",
                        properties: {
                            entity_name: { type: "string", description: "Entity name as it appears in the graph, e.g. 'NASA' or 'Elon Musk'" }
                        },
                        required: ["entity_name"]
                    }
                }
            },
            {
                type: "function",
                function: {
                    name: "explore_entity",
                    description: "Load all raw atomic claims connected to a named entity. Use for very specific or recent facts that may not appear in the article.",
                    parameters: {
                        type: "object",
                        properties: {
                            entity_name: { type: "string", description: "Exact entity name, e.g. 'NASA' or 'Artemis Program'" }
                        },
                        required: ["entity_name"]
                    }
                }
            },
            {
                type: "function",
                function: {
                    name: "yield_final_answer",
                    description: "Deliver the final verified answer to the user. ALWAYS call this last. Include UI actions to drive the graph interface.",
                    parameters: {
                        type: "object",
                        properties: {
                            answer: {
                                type: "string",
                                description: "Concise, verified answer. 2-4 sentences. First person. Ground in what the graph says. If no data, be honest: 'Truth does not have verified information on that yet.'"
                            },
                            focal_entity: {
                                type: "string",
                                description: "The primary entity to highlight on the graph canvas."
                            },
                            context_entities: {
                                type: "array",
                                items: { type: "string" },
                                description: "Secondary related entity names to show as graph context."
                            },
                            actions: {
                                type: "array",
                                description: "Ordered list of UI actions to execute on the graph interface.",
                                items: {
                                    type: "object",
                                    properties: {
                                        type: {
                                            type: "string",
                                            enum: ["LOAD_ENTITY", "OPEN_INSPECTOR", "SET_TAB", "CLOSE_INSPECTOR"]
                                        },
                                        entity: { type: "string" },
                                        tab: { type: "string", enum: ["article", "facts", "timeline"] }
                                    },
                                    required: ["type"]
                                }
                            },
                            reference_ids: {
                                type: "array",
                                items: { type: "string" },
                                description: "UUIDs of claims or references that back this answer (from article refs or claim IDs)."
                            }
                        },
                        required: ["answer", "focal_entity", "actions"]
                    }
                }
            }
        ];

        // ── 2. Session Context String ──────────────────────────────────────────
        const ctxEntity = context?.current_entity || null;
        const ctxViewport = (context?.viewport_entities || []).slice(0, 8).join(', ');
        const contextBlock = ctxEntity
            ? `\nSESSION CONTEXT: The user currently has "${ctxEntity}" loaded on the graph canvas. Visible related entities: [${ctxViewport}]. For follow-up questions without an explicit subject, assume the subject is "${ctxEntity}".`
            : '';

        // ── 3. System Prompt ───────────────────────────────────────────────────
        const systemPrompt = `You are the Truth Intelligence Agent — a precision fact-navigator and verifier built on a real-time Neo4j knowledge graph.
${contextBlock}

TOOL STRATEGY:
1. search_graph(keywords) — find the right entity name (2-5 broad keywords).
2. read_entity_article(entity_name) — get the synthesized narrative. PREFER this for "who/what is X?" questions.
3. explore_entity(entity_name) — get raw atomic facts for precise or time-specific queries.
4. yield_final_answer(...) — ALWAYS the last call.

FOLLOW-UP HANDLING: If the user's question has no explicit subject AND session context has a current_entity, run read_entity_article(current_entity) first — do NOT search_graph again for the same entity.

STRICT ANSWER RULES — violations are unacceptable:
- Answer ONLY the exact question asked. Nothing else.
- NEVER add "The graph also contains...", "Additionally...", "It is worth noting...", or any padding sentences.
- Lead with the core fact in the first sentence.
- Maximum 3 sentences. Be surgical.
- Cite inline when from article: "NASA launched Artemis [¹]". Superscript numbers only.
- If NO data found after 2 tool loops: "Truth does not have verified information on that yet." Then stop.
- NEVER hallucinate. Every sentence must come from a tool result.

ACTION RULES (always include in yield_final_answer):
- Entity found → LOAD_ENTITY, then OPEN_INSPECTOR.
- "who/what is X?" → SET_TAB: "article" after OPEN_INSPECTOR.
- Specific fact query → SET_TAB: "facts".
- "close" / "hide" → CLOSE_INSPECTOR only.
- Follow-up on same entity → LOAD_ENTITY (refresh) + OPEN_INSPECTOR.

META-QUESTIONS ("what can I ask?"): yield_final_answer immediately with 2 example questions. actions: [].`;

        // ── 4. Build LLM Message History ──────────────────────────────────────
        const cleanHistory = messages
            .filter(m => m && m.content)
            .map(m => {
                let text = m.content;
                if (typeof text !== 'string') text = JSON.stringify(text);
                const safeRole = (m.role === 'user' || m.role === 'assistant') ? m.role : 'user';
                return { role: safeRole, content: text };
            });

        let llmMessages = [
            { role: 'system', content: systemPrompt },
            ...cleanHistory
        ];

        let finalAnswer = null;
        let focalEntity = null;
        let contextEntities = [];
        let agentActions = [];
        let referenceIds = [];
        // We'll collect article refs during tool execution for later resolution
        const articleRefsMap = {}; // entity_name → refs array
        let loops = 0;
        const MAX_LOOPS = 6;

        // ── 5. ReAct Loop ─────────────────────────────────────────────────────
        while (loops < MAX_LOOPS && !finalAnswer) {
            loops++;

            let groqRes, data;
            try {
                const groqKey = getNextGroqKey();
                groqRes = await fetch('https://api.groq.com/openai/v1/chat/completions', {
                    method: 'POST',
                    headers: { 'Authorization': `Bearer ${groqKey}`, 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        model: 'llama-3.3-70b-versatile',
                        messages: llmMessages,
                        tools: tools,
                        tool_choice: "auto",
                        parallel_tool_calls: false,
                        temperature: 0.1,
                        max_tokens: 1200
                    })
                });

                data = await groqRes.json();

                if (!groqRes.ok) {
                    // Intercept the notorious Llama-3 XML tool format error on Groq
                    if (data?.error?.code === 'tool_use_failed' && data?.error?.failed_generation) {
                        console.warn("[Agent] Intercepted Groq tool_use_failed XML bug. Parsing manually.");
                        const xmlMatch = data.error.failed_generation.match(/<function=(\w+)(.*?)>(?:<\/function>)?/s);
                        if (xmlMatch) {
                            data = {
                                choices: [{
                                    message: {
                                        role: "assistant",
                                        content: null,
                                        tool_calls: [{
                                            id: `call_recovered_${Date.now()}`,
                                            type: "function",
                                            function: { name: xmlMatch[1], arguments: xmlMatch[2] || "{}" }
                                        }]
                                    }
                                }]
                            };
                        } else {
                            throw new Error(`Groq API error: ${JSON.stringify(data.error)}`);
                        }
                    } else {
                        throw new Error(`Groq API error: ${JSON.stringify(data.error)}`);
                    }
                }
            } catch (err) {
                throw err;
            }

            const assistantMsg = data.choices[0].message;
            llmMessages.push(assistantMsg);

            if (!assistantMsg.tool_calls || assistantMsg.tool_calls.length === 0) {
                finalAnswer = assistantMsg.content || "I couldn't deduce an answer from the graph.";
                break;
            }

            for (const toolCall of assistantMsg.tool_calls) {
                const toolName = toolCall.function.name;
                let args;
                try {
                    args = JSON.parse(toolCall.function.arguments);
                } catch (e) {
                    llmMessages.push({ role: 'tool', tool_call_id: toolCall.id, name: toolName, content: 'Error: could not parse tool arguments. Please try again.' });
                    continue;
                }

                let toolResult = "";

                // ── Tool: search_graph ─────────────────────────────────
                if (toolName === 'search_graph') {
                    try {
                        const keywords = String(args.keywords || '').trim();
                        if (!keywords) {
                            toolResult = "Error: keywords cannot be empty.";
                        } else {
                            const ftResult = await session.executeRead(tx => tx.run(`
                                CALL db.index.fulltext.queryNodes('claim_fulltext', $query)
                                YIELD node AS c, score
                                RETURN c.subject AS subject, c.predicate AS predicate,
                                       c.object AS object, c.quote_context AS quote_context,
                                       c.article_title AS article_title, c.source_name AS source,
                                       c.epistemic_score AS epistemic_score, c.id AS id, score
                                ORDER BY score DESC LIMIT 10
                            `, { query: keywords }));

                            const records = ftResult.records.map(r => toPlain(r.toObject()));
                            if (records.length === 0) {
                                toolResult = `search_graph("${keywords}") returned 0 results. Try different or broader keywords.`;
                                console.log(`[Agent] search_graph("${keywords}") → 0 results`);
                            } else {
                                toolResult = JSON.stringify(records);
                                console.log(`[Agent] search_graph("${keywords}") → ${records.length} results`);
                            }
                        }
                    } catch (err) {
                        toolResult = `Error in search_graph: ${err.message}. Try different keywords.`;
                        console.error("[Agent] search_graph error:", err.message);
                    }

                // ── Tool: read_entity_article ──────────────────────────
                } else if (toolName === 'read_entity_article') {
                    try {
                        const entityName = String(args.entity_name || '').trim();
                        if (!entityName) {
                            toolResult = "Error: entity_name cannot be empty.";
                        } else {
                            const artResult = await session.executeRead(tx => tx.run(`
                                MATCH (e:Entity)
                                WHERE toLower(e.name) CONTAINS toLower($name)
                                RETURN e.name AS name,
                                       e.article AS article,
                                       e.article_references AS refs,
                                       e.article_generated_at AS generated_at
                                LIMIT 1
                            `, { name: entityName }));

                            if (artResult.records.length === 0) {
                                toolResult = `No entity matching "${entityName}" found in the graph. Try search_graph first to identify the correct entity name.`;
                            } else {
                                const rec = toPlain(artResult.records[0].toObject());
                                if (!rec.article) {
                                    toolResult = `Entity "${rec.name || entityName}" exists but its article is still being generated. Use explore_entity instead.`;
                                } else {
                                    // Store refs for later resolution
                                    try {
                                        const refs = JSON.parse(rec.refs || '[]');
                                        articleRefsMap[rec.name || entityName] = refs;
                                    } catch(e) { /* ignore parse errors */ }

                                    // Truncate article to 3500 chars to stay within context budget
                                    const articleText = rec.article.length > 3500
                                        ? rec.article.substring(0, 3500) + '... [truncated]'
                                        : rec.article;

                                    toolResult = JSON.stringify({
                                        entity: rec.name || entityName,
                                        generated_at: rec.generated_at,
                                        article: articleText
                                    });
                                    console.log(`[Agent] read_entity_article("${entityName}") → ${articleText.length} chars`);
                                }
                            }
                        }
                    } catch (err) {
                        toolResult = `Error in read_entity_article: ${err.message}.`;
                        console.error("[Agent] read_entity_article error:", err.message);
                    }

                // ── Tool: explore_entity ───────────────────────────────
                } else if (toolName === 'explore_entity') {
                    try {
                        const entityName = String(args.entity_name || '').trim();
                        if (!entityName) {
                            toolResult = "Error: entity_name cannot be empty.";
                        } else {
                            const exploredResult = await session.executeRead(tx => tx.run(`
                                MATCH (e:Entity)
                                WHERE toLower(e.name) CONTAINS toLower($name)
                                WITH e LIMIT 1
                                OPTIONAL MATCH (c:Claim)-[:HAS_SUBJECT]->(e)
                                RETURN e.name AS entity, c.predicate AS predicate,
                                       c.object AS object, c.quote_context AS quote_context,
                                       c.epistemic_score AS score, c.id AS id
                                LIMIT 20
                            `, { name: entityName }));

                            const records = exploredResult.records.map(r => toPlain(r.toObject()));
                            if (records.length === 0 || !records[0].entity) {
                                toolResult = `explore_entity("${entityName}") found no entity. Try search_graph with their name as keywords first.`;
                            } else {
                                toolResult = JSON.stringify(records);
                                console.log(`[Agent] explore_entity("${entityName}") → ${records.length} claims`);
                            }
                        }
                    } catch (err) {
                        toolResult = `Error in explore_entity: ${err.message}.`;
                        console.error("[Agent] explore_entity error:", err.message);
                    }

                // ── Tool: yield_final_answer ───────────────────────────
                } else if (toolName === 'yield_final_answer') {
                    finalAnswer = args.answer || "I couldn't find a clear answer in the graph.";
                    focalEntity = args.focal_entity || null;
                    contextEntities = Array.isArray(args.context_entities) ? args.context_entities : [];
                    agentActions = Array.isArray(args.actions) ? args.actions : [];
                    referenceIds = Array.isArray(args.reference_ids) ? args.reference_ids : [];
                    llmMessages.push({ role: 'tool', tool_call_id: toolCall.id, name: toolName, content: "Answer delivered." });
                    break;
                } else {
                    toolResult = `Unknown tool: ${toolName}.`;
                }

                if (toolName !== 'yield_final_answer') {
                    llmMessages.push({
                        role: 'tool',
                        tool_call_id: toolCall.id,
                        name: toolName,
                        content: toolResult
                    });
                }
            }
        }

        if (!finalAnswer) {
            finalAnswer = "I reached my reasoning limit and couldn't fully process the answer. Please try rephrasing.";
        }

        // ── 6. Resolve Reference IDs to Full Source Objects ────────────────────
        // Collect all refs from all articles fetched during this loop
        const allRefs = Object.values(articleRefsMap).flat();
        const resolvedRefs = referenceIds.length > 0
            ? allRefs.filter(r => referenceIds.includes(r.id))
            : allRefs.slice(0, 5); // If AI didn't specify ref IDs, return top 5 from article

        // ── 7. Build Default Actions if AI forgot ─────────────────────────────
        if (agentActions.length === 0 && focalEntity) {
            agentActions = [
                { type: 'LOAD_ENTITY', entity: focalEntity },
                { type: 'OPEN_INSPECTOR', entity: focalEntity, tab: 'article' }
            ];
        }

        res.json({
            answer: finalAnswer,
            focal_entity: focalEntity,
            context_entities: contextEntities,
            actions: agentActions,
            references: resolvedRefs
        });

    } catch (e) {
        console.error("[/api/chat] Error:", e.message);
        res.status(500).json({ error: e.message });
    } finally {
        await session.close();
    }
});

// ─── GET /api/entity/:name ────────────────────────────────────────────────────



// ─── GET /api/entity/:name ────────────────────────────────────────────────────
router.get('/entity/:name', async (req, res) => {
    const session = neo4j(req).session();
    try {
        const r = await session.run(`
      MATCH (e:Entity {name: $name})
      OPTIONAL MATCH (c:Claim)-[:HAS_SUBJECT|HAS_OBJECT]->(e)
      RETURN e.name AS entity, e.mention_count AS mentions,
             collect({
               id:              c.id,
               subject:         c.subject,
               predicate:       c.predicate,
               object:          c.object,
               temporal:        c.temporal,
               lifecycle:       c.lifecycle,
               is_current:      c.is_current,
               valid_from:      toString(c.valid_from),
               valid_until:     toString(c.valid_until),
               quote_context:   c.quote_context,
               article_title:   c.article_title,
               source_url:      c.source_url,
               source_name:     c.source_name,
               publish_date:    c.publish_date
             }) AS claims
    `, { name: req.params.name });

        if (!r.records.length)
            return res.status(404).json({ error: 'Entity not found' });

        res.json(toPlain(r.records[0].toObject()));
    } catch (e) {
        console.error(e);
        res.status(500).json({ error: String(e) });
    } finally {
        await session.close();
    }
});

// ─── GET /api/claim/:id ───────────────────────────────────────────────────────
router.get('/claim/:id', async (req, res) => {
    const session = neo4j(req).session();
    try {
        const r = await session.run(`
      MATCH (c:Claim {id: $id})
      OPTIONAL MATCH (c)-[:SUPPORTED_BY]->(ev:Evidence)
      OPTIONAL MATCH (c)-[:FIRST_REPORTED_BY]->(orig:Source)
      OPTIONAL MATCH (c)-[:EXTRACTED_FROM]->(a:Article)-[:PUBLISHED_BY]->(src:Source)
      OPTIONAL MATCH (c)-[:SUPERSEDES]->(old:Claim)
      OPTIONAL MATCH (newer:Claim)-[:SUPERSEDES]->(c)
      OPTIONAL MATCH (c)-[:CONFIRMED_BY]->(conf:Source)
      RETURN c {
        .*,
        valid_from:  toString(c.valid_from),
        valid_until: toString(c.valid_until)
      } AS claim,
      collect(DISTINCT ev {.*})        AS evidence,
      orig.name                        AS original_source,
      src.name                         AS published_by,
      src.epistemic_trust              AS source_trust,
      a.title                          AS article_title,
      old  { .id, .subject, .predicate, .object, .valid_from, .valid_until } AS supersedes,
      newer { .id, .subject, .predicate, .object, .valid_from }              AS superseded_by,
      collect(DISTINCT conf.name)      AS confirmed_by
    `, { id: req.params.id });

        if (!r.records.length)
            return res.status(404).json({ error: 'Claim not found' });

        res.json(toPlain(r.records[0].toObject()));
    } catch (e) {
        console.error(e);
        res.status(500).json({ error: String(e) });
    } finally {
        await session.close();
    }
});

// ─── GET /api/timeline/:subject/:predicate ────────────────────────────────────
router.get('/timeline/:subject/:predicate', async (req, res) => {
    const { subject, predicate } = req.params;
    const session = neo4j(req).session();
    try {
        const r = await session.run(`
      MATCH (tl:Timeline {subject: $subject, predicate: $predicate})
      MATCH (tl)-[:CONTAINS]->(c:Claim)
      RETURN c.id AS id, c.subject AS subject, c.predicate AS predicate,
             c.epistemic_score AS score, c.is_current AS is_current,
             c.lifecycle AS lifecycle,
             toString(c.valid_from)  AS valid_from,
             toString(c.valid_until) AS valid_until,
             c.quote_context AS quote_context,
             c.article_title AS article_title,
             c.source_url AS source_url,
             c.source_name AS source_name,
             c.publish_date AS publish_date
      ORDER BY c.valid_from ASC
    `, { subject, predicate });

        res.json({
            subject, predicate,
            timeline: r.records.map(r => toPlain(r.toObject()))
        });
    } catch (e) {
        console.error(e);
        res.status(500).json({ error: String(e) });
    } finally {
        await session.close();
    }
});

// ─── GET /api/contradictions ──────────────────────────────────────────────────
router.get('/contradictions', requireAdmin, async (req, res) => {
    const skip = parseInt(req.query.skip || '0');
    const limit = parseInt(req.query.limit || '50');
    const session = neo4j(req).session();
    try {
        const countRes = await session.run(`
            MATCH (cv:Controversy {open: true})
            RETURN count(cv) AS total
        `);
        const total = countRes.records[0].get('total').toNumber();

        const r = await session.run(`
      MATCH (cv:Controversy {open: true})
      OPTIONAL MATCH (cv)-[:INCLUDES]->(c:Claim)
      WITH cv, collect({
        id: c.id, object: c.object,
        score: c.epistemic_score, lifecycle: c.lifecycle
      }) AS competing_claims
      RETURN cv.subject AS subject, cv.predicate AS predicate,
             cv.claim_count AS claim_count,
             toString(cv.created_at) AS created_at,
             competing_claims
      ORDER BY created_at DESC SKIP toInteger($skip) LIMIT toInteger($limit)
    `, { skip, limit });

        res.json({
            skip,
            limit,
            total,
            items: r.records.map(rec => toPlain(rec.toObject()))
        });
    } catch (e) {
        console.error(e);
        res.status(500).json({ error: String(e) });
    } finally {
        await session.close();
    }
});

// ─── GET /api/admin/articles ─────────────────────────────────────────────────
router.get('/admin/articles', requireAdmin, async (req, res) => {
    const session = neo4j(req).session();
    try {
        const metricsResult = await session.run(`
            CALL { MATCH (e:Entity) RETURN count(e) AS total_entities }
            CALL { MATCH (e:Entity) WHERE e.article IS NOT NULL RETURN count(e) AS articles_generated }
            CALL { MATCH (c:Claim) WHERE c.status IN ['GRAPH_COMMITTED', 'AUTO_APPROVE'] RETURN count(c) AS total_claims }
            CALL { MATCH (e:Entity) WHERE e.article IS NOT NULL AND e.article_stale = true RETURN count(e) AS stale_articles }
            CALL { MATCH (e:Entity) WHERE e.article IS NULL AND e.mention_count > 0 RETURN count(e) AS missing_articles }
            
            RETURN total_entities, articles_generated, total_claims, stale_articles, missing_articles
        `);
        
        const m = metricsResult.records[0];
        const overview = {
            total_entities: m.get('total_entities').toNumber(),
            articles_generated: m.get('articles_generated').toNumber(),
            total_claims: m.get('total_claims').toNumber(),
            stale_articles: m.get('stale_articles').toNumber(),
            missing_articles: m.get('missing_articles').toNumber(),
            completion_rate: m.get('total_entities').toNumber() > 0 
                ? Math.round((m.get('articles_generated').toNumber() / m.get('total_entities').toNumber()) * 100) 
                : 0
        };

        const entitiesResult = await session.run(`
            MATCH (e:Entity)
            RETURN e.name AS name,
                   e.mention_count AS mentions,
                   e.search_count AS popularity,
                   e.article IS NOT NULL AS has_article,
                   coalesce(e.article_stale, false) AS is_stale,
                   e.article_claim_count AS facts_used
            ORDER BY 
                   (coalesce(e.search_count, 0) * 10) + coalesce(e.mention_count, 0) DESC
            LIMIT 100
        `);

        const entities = entitiesResult.records.map(rec => ({
            name: rec.get('name'),
            mentions: rec.get('mentions')?.toNumber() || 0,
            popularity: rec.get('popularity')?.toNumber() || 0,
            has_article: rec.get('has_article'),
            is_stale: rec.get('is_stale'),
            facts_used: rec.get('facts_used')?.toNumber() || 0
        }));

        res.json({ overview, entities });
    } catch (e) {
        console.error('[admin/articles]', e);
        res.status(500).json({ error: String(e) });
    } finally {
        await session.close();
    }
});

// ─── GET /api/human-review?page=1&limit=20 ───────────────────────────────────
router.get('/human-review', requireAdmin, async (req, res) => {
    const page = parseInt(req.query.page || '1');
    const limit = parseInt(req.query.limit || '20');
    const offset = (page - 1) * limit;

    try {
        const client = await pgPool(req).connect();
        try {
            const { rows } = await client.query(`
        SELECT ec.id, ec.subject, ec.predicate, ec.object_entity,
               ec.temporal_anchor, ec.extraction_confidence,
               ec.epistemic_score, ec.lifecycle,
               ec.valid_from, ec.valid_until,
               ra.title AS article_title, ru.url AS article_url,
               s.name AS source_name, s.epistemic_trust_score,
               cp.neo4j_stance, cp.internet_original_source
        FROM extracted_claims ec
        JOIN raw_articles ra ON ec.article_id = ra.id
        JOIN raw_urls ru     ON ra.url_id = ru.id
        JOIN sources s       ON ru.source_id = s.id
        LEFT JOIN claim_provenance cp ON cp.claim_id = ec.id
        WHERE ec.status = 'HUMAN_REVIEW'
        ORDER BY ec.epistemic_score DESC
        LIMIT $1 OFFSET $2
      `, [limit, offset]);

            const count = await client.query(
                "SELECT COUNT(*) FROM extracted_claims WHERE status = 'HUMAN_REVIEW'"
            );

            const session = neo4j(req).session();
            try {
                for (const row of rows) {
                    if (row.lifecycle === 'DISPUTED') {
                        // Find the controversy and the other claim
                        const result = await session.run(`
                            MATCH (c1:Claim {id: $id})<-[:INCLUDES]-(cv:Controversy)-[:INCLUDES]->(c2:Claim)
                            WHERE c1 <> c2
                            RETURN c2.subject AS subject, c2.predicate AS predicate, c2.object AS object,
                                   c2.source_name AS source_name, c2.source_url AS source_url, 
                                   c2.epistemic_score AS score
                        `, { id: String(row.id) });
                        
                        if (result.records.length > 0) {
                            const rec = result.records[0];
                            row.controversy_context = {
                                subject: rec.get('subject'),
                                predicate: rec.get('predicate'),
                                object: rec.get('object'),
                                source_name: rec.get('source_name'),
                                source_url: rec.get('source_url'),
                                score: rec.get('score')
                            };
                        }
                    }
                }
            } catch (err) {
                console.error("Neo4j Controversy Fetch Error:", err);
            } finally {
                await session.close();
            }

            res.json({
                page, limit,
                total: parseInt(count.rows[0].count),
                items: rows
            });
        } finally {
            client.release();
        }
    } catch (e) {
        console.error(e);
        res.status(500).json({ error: String(e) });
    }
});

// ─── POST /api/human-review/:id/resolve ──────────────────────────────────────
// Body: { decision: "APPROVE" | "REJECT" | "RETRACT", note: "..." }
router.post('/human-review/:id/resolve', requireAdmin, async (req, res) => {
    const claimId = req.params.id;
    const { decision, note } = req.body;

    if (!['APPROVE', 'REJECT', 'RETRACT'].includes(decision))
        return res.status(400).json({ error: 'decision must be APPROVE, REJECT, or RETRACT' });

    const client = await pgPool(req).connect();

    try {
        await client.query('BEGIN');

        const statusMap = {
            APPROVE: 'GRAPH_COMMITTED', REJECT: 'AUTO_REJECT',
            RETRACT: 'RETRACTED'
        };
        const lifecycleMap = {
            APPROVE: 'ACTIVE', REJECT: 'REJECTED',
            RETRACT: 'RETRACTED'
        };

        // 1. Update PostgreSQL
        await client.query(`
      UPDATE extracted_claims
      SET status    = $1,
          lifecycle = $2
      WHERE id = $3
    `, [statusMap[decision], lifecycleMap[decision], claimId]);

        // 2. Insert into the Graph Outbox for Neo4j background processing
        // This ensures the Neo4j write and Postgres state-change succeed or fail as a single atomic unit.
        await client.query(`
      INSERT INTO graph_outbox (claim_id, decision, note)
      VALUES ($1, $2, $3)
    `, [claimId, decision, note || null]);

        await client.query('COMMIT');
        res.json({ success: true, claimId, decision, note, message: "Transaction queued for graph outbox." });
    } catch (e) {
        await client.query('ROLLBACK');
        console.error(e);
        res.status(500).json({ error: String(e) });
    } finally {
        client.release();
    }
});

// ─── GET /api/sources ─────────────────────────────────────────────────────────
router.get('/sources', async (req, res) => {
    try {
        const client = await pgPool(req).connect();
        try {
            const { rows } = await client.query(`
        SELECT id, name, url, domain, category,
               ROUND(epistemic_trust_score::numeric, 3) AS trust_score,
               trust_updated_at,
               COUNT(ru.id) AS articles_ingested
        FROM sources s
        LEFT JOIN raw_urls ru ON ru.source_id = s.id
        GROUP BY s.id, s.name, s.url, s.domain, s.category,
                 s.epistemic_trust_score, s.trust_updated_at
        ORDER BY s.epistemic_trust_score DESC
        LIMIT 100
      `);
            res.json(rows);
        } finally {
            client.release();
        }
    } catch (e) {
        console.error(e);
        res.status(500).json({ error: String(e) });
    }
});

// ─── GET /api/entities ────────────────────────────────────────────────────────
// Paginated list for ER Human-in-the-loop interface
router.get('/entities', async (req, res) => {
    const skip = parseInt(req.query.skip || '0');
    const limit = parseInt(req.query.limit || '50');
    const session = neo4j(req).session();
    try {
        const countRes = await session.run('MATCH (e:Entity) RETURN count(e) AS total');
        const total = countRes.records[0].get('total').toNumber();

        const r = await session.run(`
            MATCH (e:Entity)
            RETURN e.name AS name, e.mention_count AS mentions
            ORDER BY e.mention_count DESC, e.name ASC
            SKIP toInteger($skip) LIMIT toInteger($limit)
        `, { skip, limit });

        res.json({
            skip, limit, total,
            items: r.records.map(rec => toPlain(rec.toObject()))
        });
    } catch (e) {
        console.error(e);
        res.status(500).json({ error: String(e) });
    } finally {
        await session.close();
    }
});

// ─── POST /api/entities/merge ─────────────────────────────────────────────────
// Body: { targetEntity: "Apple Inc.", sourceEntities: ["Apple", "Apple (Company)"] }
router.post('/entities/merge', async (req, res) => {
    const { targetEntity, sourceEntities } = req.body;
    if (!targetEntity || !Array.isArray(sourceEntities) || !sourceEntities.length) {
        return res.status(400).json({ error: 'Need targetEntity (str) and sourceEntities (array).' });
    }

    const session = neo4j(req).session();
    try {
        // 1. Ensure target entity exists (or create it)
        await session.run(`MERGE (t:Entity {name: $targetEntity})`, { targetEntity });

        // 2. Rewrite all HAS_SUBJECT and HAS_OBJECT relationships pointing to sources
        // then delete the source entity nodes
        const result = await session.run(`
            MATCH (s:Entity) WHERE s.name IN $sourceEntities
            MATCH (t:Entity {name: $targetEntity})
            
            // Move HAS_SUBJECT
            OPTIONAL MATCH (c1:Claim)-[rel1:HAS_SUBJECT]->(s)
            CALL { WITH c1, t, rel1 MERGE (c1)-[:HAS_SUBJECT]->(t) }
            
            // Move HAS_OBJECT
            OPTIONAL MATCH (c2:Claim)-[rel2:HAS_OBJECT]->(s)
            CALL { WITH c2, t, rel2 MERGE (c2)-[:HAS_OBJECT]->(t) }
            
            // Delete old relationships and the source nodes themselves
            DETACH DELETE s
            
            RETURN count(s) AS merged_count
        `, { targetEntity, sourceEntities });

        res.json({ success: true, merged: result.records[0].get('merged_count').toNumber() });
    } catch (e) {
        console.error("Entity merge failed:", e);
        res.status(500).json({ error: String(e) });
    } finally {
        await session.close();
    }
});

// ─── GET /api/stats ───────────────────────────────────────────────────────────
router.get('/stats', async (req, res) => {
    const session = neo4j(req).session();
    const timeout = setTimeout(() => {
        console.warn('[/api/stats] Request timeout after 10s');
        if (!res.headersSent) {
            res.status(504).json({ error: 'Database query timeout' });
        }
    }, 10000);

    try {
        const neo4jStats = await session.run(`
      CALL { MATCH (c:Claim) RETURN count(c) AS claims }
      CALL { MATCH (e:Entity) RETURN count(e) AS entities }
      CALL { MATCH (s:Source) RETURN count(s) AS sources }
      CALL { MATCH (a:Article) RETURN count(a) AS articles }
      CALL { MATCH (cv:Controversy {open: true}) RETURN count(cv) AS open_controversies }
      CALL { MATCH (c2:Claim {is_current: true}) RETURN count(c2) AS active_claims }
      RETURN claims, entities, sources, articles, open_controversies, active_claims
    `);

        const client = await pgPool(req).connect();
        const pgStats = await client.query(`
      SELECT
        COUNT(*) FILTER (WHERE status = 'HUMAN_REVIEW')   AS human_review_pending,
        COUNT(*) FILTER (WHERE lifecycle = 'SUPERSEDED')  AS superseded_claims,
        COUNT(*) FILTER (WHERE lifecycle = 'STALE')       AS stale_claims,
        COUNT(*) FILTER (WHERE lifecycle = 'DISPUTED')    AS disputed_claims,
        COUNT(*) FILTER (WHERE pipeline_stage = 'COMPLETE') AS complete
      FROM extracted_claims
    `);
        client.release();

        clearTimeout(timeout);
        const neo4j_row = toPlain(neo4jStats.records[0]?.toObject() || {});
        const pg_row = pgStats.rows[0];

        res.json({
            graph: neo4j_row,
            pipeline: pg_row,
            generated_at: new Date().toISOString()
        });
    } catch (e) {
        clearTimeout(timeout);
        console.error('[/api/stats] Error:', e.message);
        if (!res.headersSent) {
            res.status(500).json({ error: String(e) });
        }
    } finally {
        await session.close();
    }
});

// ─── GET /api/graph/neighborhood/:name ───────────────────────────────────────
// Returns a typed graph { focal, nodes, edges } for the ExplorerPane.
// ?show_all=true  includes non-current claims (superseded, retracted)
// ?limit=N        max claims to return (default 60)
router.get('/graph/neighborhood/:name', async (req, res) => {
    const { name } = req.params;
    const showAll = req.query.show_all === 'true';
    const limit = Math.min(parseInt(req.query.limit || '60'), 200);

    const session = neo4j(req).session();
    try {
        const result = await session.run(`
            MATCH (focal:Entity {name: $name})

            // 1-hop: Claims that have this entity as subject or object
            OPTIONAL MATCH (c:Claim)-[:HAS_SUBJECT|HAS_OBJECT]->(focal)
            WHERE $showAll = true OR c.is_current = true

            // Linked entities via the claim
            OPTIONAL MATCH (c)-[:HAS_SUBJECT]->(subj:Entity)
            OPTIONAL MATCH (c)-[:HAS_OBJECT]->(obj:Entity)

            // Evidence attached to each claim
            OPTIONAL MATCH (c)-[:SUPPORTED_BY]->(ev:Evidence)

            // Visual Media attached to each claim
            OPTIONAL MATCH (c)-[m_rel:SUPPORTED_BY]->(m:Media)

            // Stance edges between claims
            OPTIONAL MATCH (c)-[stance:EVOLVES|CORROBORATED_BY|CONTRADICTS]->(other:Claim)
            WHERE other IS NULL OR $showAll = true OR other.is_current = true

            // Ontology Edges: Pure structural graph connections missing from "Claims"
            OPTIONAL MATCH (focal)-[e_rel:CONTAINS|PART_OF|IS_A|SUBCLASS_OF]-(linked_e:Entity)

            WITH focal,
                 collect(DISTINCT properties(c))    AS claims,
                 collect(DISTINCT properties(subj)) AS subjects,
                 collect(DISTINCT properties(obj))  AS objects,
                 collect(DISTINCT properties(ev))   AS evidences,
                 collect(DISTINCT {
                     claim_id:               c.id,
                     url:                    m.url,
                     phash:                  m.phash,
                     synthetic_probability:  m_rel.synthetic_probability,
                     cross_modal_similarity: m_rel.cross_modal_similarity
                 }) AS media_items,
                 collect(DISTINCT {
                     fromId:      c.id,
                     toId:        other.id,
                     type:        type(stance),
                     similarity:  stance.similarity
                 }) AS stanceEdges,
                 collect(DISTINCT {
                     fromName: startNode(e_rel).name,
                     toName:   endNode(e_rel).name,
                     type:     type(e_rel)
                 }) AS entityEdges,
                 collect(DISTINCT properties(linked_e)) AS structured_entities

            RETURN focal   { .name, .mention_count }                         AS focal,
                   claims  [0..$limit]                                       AS claims,
                   subjects,
                   objects,
                   evidences,
                   media_items,
                   stanceEdges,
                   entityEdges,
                   structured_entities
        `, { name, showAll, limit });

        if (!result.records.length || !result.records[0].get('focal')) {
            return res.status(404).json({ error: `Entity '${name}' not found` });
        }

        const rec = result.records[0];
        const focalRaw = toPlain(rec.get('focal'));
        const claimsRaw = toPlain(rec.get('claims') || []);
        const subjectsRaw = toPlain(rec.get('subjects') || []);
        const objectsRaw = toPlain(rec.get('objects') || []);
        const evidRaw = toPlain(rec.get('evidences') || []);
        const mediaRaw = toPlain(rec.get('media_items') || []).filter(m => m.url);
        const stanceRaw = toPlain(rec.get('stanceEdges') || []);
        const entityEdgesRaw = toPlain(rec.get('entityEdges') || []);
        const strEntitiesRaw = toPlain(rec.get('structured_entities') || []);

        // ── Build typed node list ─────────────────────────────────────────
        const nodesMap = new Map();
        const edgesList = [];

        // Focal entity node
        nodesMap.set(`e:${focalRaw.name}`, {
            id: `e:${focalRaw.name}`,
            type: 'Entity',
            role: 'focal',
            label: focalRaw.name,
            mention_count: focalRaw.mention_count || 0,
        });

        // Claim nodes + their HAS_SUBJECT / HAS_OBJECT / SUPPORTED_BY edges
        for (const c of claimsRaw) {
            if (!c || !c.id) continue;
            const claimNodeId = `c:${c.id}`;

            nodesMap.set(claimNodeId, {
                id: claimNodeId,
                type: 'Claim',
                label: c.predicate || '',
                subject: c.subject || '',
                predicate: c.predicate || '',
                object: c.object || '',
                temporal: c.temporal || '',
                spatial: c.spatial || '',
                score: c.epistemic_score || 0,
                lifecycle: c.lifecycle || 'ACTIVE',
                is_current: c.is_current,
                quote_context: c.quote_context || '',
                article_title: c.article_title || '',
                source_url: c.source_url || '',
                source_name: c.source_name || '',
                publish_date: c.publish_date || '',
            });

            // Edge: Entity → Claim (HAS_SUBJECT means this entity is the subject)
            edgesList.push({
                id: `hs-${c.id}`,
                source: `e:${c.subject}`,
                target: claimNodeId,
                type: 'HAS_SUBJECT',
                label: '',
            });
            // Edge: Claim → Entity (HAS_OBJECT)
            edgesList.push({
                id: `ho-${c.id}`,
                source: claimNodeId,
                target: `e:${c.object}`,
                type: 'HAS_OBJECT',
                label: '',
            });
        }

        // Linked entity nodes (subjects + objects + structured_entities, deduplicated)
        const allLinkedEntities = [...subjectsRaw, ...objectsRaw, ...strEntitiesRaw].filter(Boolean);
        for (const ent of allLinkedEntities) {
            if (!ent || !ent.name) continue;
            const eId = `e:${ent.name}`;
            if (!nodesMap.has(eId)) {
                nodesMap.set(eId, {
                    id: eId,
                    type: 'Entity',
                    role: 'linked',
                    label: ent.name,
                    mention_count: ent.mention_count || 0,
                });
            }
        }

        // Ontology Universal Hierarchy Edges
        for (const eRel of entityEdgesRaw) {
            if (!eRel || !eRel.fromName || !eRel.toName || !eRel.type) continue;
            const src = `e:${eRel.fromName}`;
            const tgt = `e:${eRel.toName}`;
            // Only add if both sides of the hierarchy are visible to the user
            if (nodesMap.has(src) && nodesMap.has(tgt)) {
                edgesList.push({
                    id: `hier-${src}-${tgt}-${eRel.type}`,
                    source: src,
                    target: tgt,
                    type: eRel.type,
                    label: eRel.type.replace(/_/g, ' ')
                });
            }
        }

        // Evidence nodes
        for (const ev of evidRaw) {
            if (!ev || !ev.claim_id) continue;
            const evId = `ev:${ev.claim_id}`;
            if (!nodesMap.has(evId)) {
                nodesMap.set(evId, {
                    id: evId,
                    type: 'Evidence',
                    label: 'Evidence',
                    raw_text: ev.raw_text || '',
                    article_title: ev.article_title || '',
                    published_by: ev.published_by || '',
                    epistemic_conf: ev.epistemic_conf || 0,
                });
                edgesList.push({
                    id: `sb-${ev.claim_id}`,
                    source: `c:${ev.claim_id}`,
                    target: evId,
                    type: 'SUPPORTED_BY',
                    label: '',
                });
            }
        }

        // Media nodes
        for (const m of mediaRaw) {
            if (!m || !m.claim_id || !m.url) continue;
            const mediaId = `m:${m.claim_id}:${m.url}`;
            if (!nodesMap.has(mediaId)) {
                nodesMap.set(mediaId, {
                    id: mediaId,
                    type: 'Media',
                    label: 'Media',
                    url: m.url,
                    phash: m.phash,
                    synthetic_probability: m.synthetic_probability || 0,
                    cross_modal_similarity: m.cross_modal_similarity || 0,
                });
                edgesList.push({
                    id: `msb-${m.claim_id}-${m.url}`,
                    source: `c:${m.claim_id}`,
                    target: mediaId,
                    type: 'SUPPORTED_BY',
                    label: '',
                });
                
                // also stick the media objects on the claim node for easy inspector access
                const cNode = nodesMap.get(`c:${m.claim_id}`);
                if (cNode) {
                    if (!cNode.media_items) cNode.media_items = [];
                    cNode.media_items.push(m);
                }
            }
        }

        // Stance edges between Claims
        for (const s of stanceRaw) {
            if (!s || !s.fromId || !s.toId || !s.type) continue;
            edgesList.push({
                id: `st-${s.fromId}-${s.toId}`,
                source: `c:${s.fromId}`,
                target: `c:${s.toId}`,
                type: s.type,
                similarity: s.similarity || 0,
                label: s.type.toLowerCase().replace(/_/g, ' '),
            });
        }

        const localNodes = Array.from(nodesMap.values());
        const localEdges = edgesList.filter(e => nodesMap.has(e.source) && nodesMap.has(e.target));
        
        localNodes.forEach(n => n.origin = 'internal');
        localEdges.forEach(e => e.origin = 'internal');

        let mergedNodes = [...localNodes];
        let mergedEdges = [...localEdges];

        const scope = req.query.scope || 'internal';
        if (scope === 'external' || scope === 'both') {
            try {
                const settings = await getSystemSettings(req.app.locals.pgPool);
                if (settings.EXTERNAL_B2B_API_URL && settings.EXTERNAL_B2B_API_KEY) {
                    const baseUrl = settings.EXTERNAL_B2B_API_URL.replace(/\/$/, '');
                    const fetchRes = await fetch(`${baseUrl}/graph/neighborhood/${encodeURIComponent(name)}?show_all=${showAll}&limit=${limit}&scope=internal`, {
                        headers: { 'Authorization': `Bearer ${settings.EXTERNAL_B2B_API_KEY}` }
                    });
                    if (fetchRes.ok) {
                        const extData = await fetchRes.json();
                        if (extData && extData.nodes && extData.edges) {
                            if (scope === 'external') {
                                mergedNodes = [];
                                mergedEdges = [];
                            }
                            extData.nodes.forEach(n => {
                                n.id = `${n.id}_ext`;
                                n.origin = 'external';
                                mergedNodes.push(n);
                            });
                            extData.edges.forEach(e => {
                                e.id = `${e.id}_ext`;
                                e.source = `${e.source}_ext`;
                                e.target = `${e.target}_ext`;
                                e.origin = 'external';
                                mergedEdges.push(e);
                            });
                            
                            if (scope === 'both') {
                                const intEntities = localNodes.filter(n => n.type === 'Entity');
                                intEntities.forEach(intE => {
                                    const extE = extData.nodes.find(e => e.id === `${intE.id}_ext`);
                                    if (extE) {
                                        mergedEdges.push({
                                            id: `same_as_${intE.id}`,
                                            source: intE.id,
                                            target: extE.id,
                                            type: 'SAME_AS',
                                            label: 'SAME AS',
                                            origin: 'system'
                                        });
                                    }
                                });
                            }
                        }
                    } else {
                        console.error('[External Graph API] Error:', fetchRes.statusText);
                    }
                }
            } catch (err) {
                console.error('[External Graph API] Fetch failed:', err.message);
            }
        }

        res.json({
            focal: focalRaw.name,
            nodes: mergedNodes,
            edges: mergedEdges,
        });
    } catch (e) {
        console.error('[neighborhood]', e);
        res.status(500).json({ error: String(e) });
    } finally {
        await session.close();
    }
});

module.exports = router;

