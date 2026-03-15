/**
 * Claude Memory — Application JavaScript
 *
 * Handles navigation, search, fact inspect, and session views.
 * All rendered text is HTML-escaped to prevent XSS.
 */

(function () {
    "use strict";

    // --- Utility: HTML escaping for XSS prevention ---

    var ESC_MAP = {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
    };

    function escapeHtml(str) {
        if (typeof str !== "string") return "";
        return str.replace(/[&<>"']/g, function (ch) {
            return ESC_MAP[ch];
        });
    }

    window.escapeHtml = escapeHtml;

    // --- State ---

    var currentMode = "search";
    var lastSearchQuery = "";
    var lastSearchResults = null;
    var viewStack = []; // For back navigation

    // --- Navigation routing ---

    var navLinks = document.querySelectorAll(".nav-link");
    var sections = document.querySelectorAll(".section");

    function navigateTo(sectionName) {
        navLinks.forEach(function (link) {
            if (link.dataset.section === sectionName) {
                link.classList.add("active");
            } else {
                link.classList.remove("active");
            }
        });

        sections.forEach(function (section) {
            var id = section.id.replace("section-", "");
            if (id === sectionName) {
                section.classList.add("active");
            } else {
                section.classList.remove("active");
            }
        });
    }

    navLinks.forEach(function (link) {
        link.addEventListener("click", function (e) {
            e.preventDefault();
            navigateTo(this.dataset.section);
            // Reset views when navigating via nav
            if (this.dataset.section === "sessions") {
                loadSessionList();
            }
        });
    });

    // --- Mode toggle (Search / Ask) ---

    var modeTabs = document.querySelectorAll(".mode-tab");

    modeTabs.forEach(function (tab) {
        tab.addEventListener("click", function () {
            currentMode = this.dataset.mode;
            modeTabs.forEach(function (t) {
                t.classList.remove("active");
            });
            this.classList.add("active");
        });
    });

    window.getSearchMode = function () {
        return currentMode;
    };

    // --- Search ---

    var searchInput = document.getElementById("search-input");
    var searchBtn = document.getElementById("search-btn");
    var resultsContainer = document.getElementById("search-results");

    // --- Ask mode state ---
    var askAbortController = null;

    function handleSearch() {
        var query = searchInput ? searchInput.value.trim() : "";
        if (!query) {
            if (resultsContainer) {
                resultsContainer.innerHTML =
                    '<div class="empty-state">Enter a query to search your memory.</div>';
            }
            return;
        }

        lastSearchQuery = query;

        // Route to Ask mode if active
        if (currentMode === "ask") {
            handleAsk(query);
            return;
        }

        if (resultsContainer) {
            resultsContainer.innerHTML =
                '<div class="empty-state">Searching...</div>';
        }

        var semanticCheckbox = document.getElementById("include-semantic");
        var searchType = (semanticCheckbox && semanticCheckbox.checked) ? "all" : "fts";
        fetch("/api/search?q=" + encodeURIComponent(query) + "&limit=20&type=" + searchType)
            .then(function (resp) {
                return resp.json();
            })
            .then(function (data) {
                lastSearchResults = data;
                renderSearchResults(data);
            })
            .catch(function (err) {
                if (resultsContainer) {
                    resultsContainer.innerHTML =
                        '<div class="error-message">Search failed: ' +
                        escapeHtml(err.message) +
                        "</div>";
                }
            });
    }

    // --- Ask mode: streaming LLM synthesis via SSE ---

    function handleAsk(query) {
        if (!resultsContainer) return;

        // Abort any previous ask stream
        if (askAbortController) {
            askAbortController.abort();
        }
        askAbortController = new AbortController();

        // Show loading indicator
        resultsContainer.innerHTML =
            '<div class="ask-container">' +
            '<div class="ask-loading" id="ask-loading">' +
            '<div class="loading-spinner"></div> Thinking...</div>' +
            '<div class="ask-answer" id="ask-answer"></div>' +
            '<div class="ask-sources" id="ask-sources"></div>' +
            "</div>";

        var answerEl = document.getElementById("ask-answer");
        var loadingEl = document.getElementById("ask-loading");
        var sourcesEl = document.getElementById("ask-sources");
        var receivedFirstToken = false;

        var url =
            "/api/ask?q=" + encodeURIComponent(query);

        fetch(url, { signal: askAbortController.signal })
            .then(function (response) {
                if (!response.ok) {
                    throw new Error("Ask request failed: " + response.status);
                }

                var reader = response.body.getReader();
                var decoder = new TextDecoder();
                var buffer = "";

                function processChunk() {
                    return reader.read().then(function (result) {
                        if (result.done) return;

                        buffer += decoder.decode(result.value, {
                            stream: true,
                        });

                        // Parse SSE events from buffer
                        var lines = buffer.split("\n");
                        buffer = "";

                        var currentEvent = null;
                        var currentData = [];

                        for (var i = 0; i < lines.length; i++) {
                            var line = lines[i];

                            if (line.indexOf("event: ") === 0) {
                                // If we had a pending event, process it first
                                if (currentEvent !== null || currentData.length > 0) {
                                    processSSEEvent(
                                        currentEvent || "message",
                                        currentData.join("\n"),
                                        answerEl,
                                        loadingEl,
                                        sourcesEl
                                    );
                                    if (!receivedFirstToken && (currentEvent === "token")) {
                                        receivedFirstToken = true;
                                    }
                                }
                                currentEvent = line.substring(7).trim();
                                currentData = [];
                            } else if (line.indexOf("data: ") === 0) {
                                currentData.push(line.substring(6));
                            } else if (line.trim() === "") {
                                if (
                                    currentEvent !== null ||
                                    currentData.length > 0
                                ) {
                                    processSSEEvent(
                                        currentEvent || "message",
                                        currentData.join("\n"),
                                        answerEl,
                                        loadingEl,
                                        sourcesEl
                                    );
                                    if (!receivedFirstToken && (currentEvent === "token")) {
                                        receivedFirstToken = true;
                                    }
                                    currentEvent = null;
                                    currentData = [];
                                }
                            } else {
                                // Incomplete line - put back in buffer
                                buffer = lines.slice(i).join("\n");
                                break;
                            }
                        }

                        // If we still have a pending event at end of chunk
                        // keep it in buffer for next chunk
                        if (currentEvent !== null || currentData.length > 0) {
                            var remaining = "";
                            if (currentEvent !== null) {
                                remaining += "event: " + currentEvent + "\n";
                            }
                            for (var j = 0; j < currentData.length; j++) {
                                remaining += "data: " + currentData[j] + "\n";
                            }
                            buffer = remaining + buffer;
                        }

                        return processChunk();
                    });
                }

                return processChunk();
            })
            .catch(function (err) {
                if (err.name === "AbortError") return;
                if (resultsContainer) {
                    resultsContainer.innerHTML =
                        '<div class="error-message">Ask failed: ' +
                        escapeHtml(err.message) +
                        "</div>";
                }
            });
    }

    function processSSEEvent(eventType, data, answerEl, loadingEl, sourcesEl) {
        if (eventType === "token") {
            // Hide loading indicator on first token
            if (loadingEl && loadingEl.style.display !== "none") {
                loadingEl.style.display = "none";
            }
            // Append token text to answer
            if (answerEl) {
                answerEl.textContent += data;
            }
        } else if (eventType === "error") {
            // Show error message
            if (loadingEl) {
                loadingEl.style.display = "none";
            }
            if (answerEl) {
                answerEl.innerHTML =
                    '<div class="error-message">' +
                    escapeHtml(data) +
                    "</div>";
            }
        } else if (eventType === "sources") {
            // Render source citations
            try {
                var sources = JSON.parse(data);
                renderAskSources(sources, sourcesEl);
            } catch (e) {
                // Ignore parse errors
            }
        } else if (eventType === "done") {
            // Stream complete — nothing else to do
        }
    }

    function renderAskSources(sources, container) {
        if (!container) return;

        var html = [];
        var hasSources = false;

        // Fact citations
        if (sources.facts && sources.facts.length > 0) {
            hasSources = true;
            html.push('<div class="sources-section">');
            html.push('<h4 class="sources-title">Source Facts</h4>');
            sources.facts.forEach(function (fact) {
                html.push(
                    '<div class="source-citation source-fact" data-fact-id="' +
                        escapeHtml(String(fact.id)) +
                        '">'
                );
                html.push(
                    '<span class="badge badge-id">#' +
                        escapeHtml(String(fact.id)) +
                        "</span> "
                );
                html.push(
                    '<span class="badge badge-category">' +
                        escapeHtml(fact.category || "") +
                        "</span> "
                );
                if (fact.confidence != null) {
                    html.push(
                        '<span class="badge badge-confidence">conf ' +
                            escapeHtml(fact.confidence.toFixed(1)) +
                            "</span> "
                    );
                }
                html.push(
                    "<span>" + escapeHtml(fact.fact || "") + "</span>"
                );
                html.push("</div>");
            });
            html.push("</div>");
        }

        // Message citations
        if (sources.messages && sources.messages.length > 0) {
            hasSources = true;
            html.push('<div class="sources-section">');
            html.push('<h4 class="sources-title">Source Messages</h4>');
            sources.messages.forEach(function (msg) {
                html.push('<div class="source-citation source-message">');
                var ts = escapeHtml(
                    (msg.timestamp || "").substring(0, 16)
                );
                var proj = escapeHtml(msg.project || "no-project");
                var role = escapeHtml(msg.role || "?");
                html.push(
                    '<div class="result-meta">[' +
                        ts +
                        "] " +
                        proj +
                        " (" +
                        role +
                        ")</div>"
                );
                html.push(
                    '<div class="result-snippet">' +
                        escapeHtml(msg.content || "") +
                        "</div>"
                );
                html.push("</div>");
            });
            html.push("</div>");
        }

        if (!hasSources) return;

        container.innerHTML = html.join("");

        // Attach click handlers for fact citations -> fact inspect
        var factCitations = container.querySelectorAll(".source-fact");
        factCitations.forEach(function (card) {
            card.style.cursor = "pointer";
            card.addEventListener("click", function () {
                var factId = this.dataset.factId;
                if (factId) {
                    showFactInspect(factId);
                }
            });
        });
    }

    function renderSearchResults(data) {
        if (!resultsContainer) return;

        var html = [];
        var totalResults =
            (data.facts || []).length +
            (data.messages || []).length +
            (data.sessions || []).length +
            (data.semantic || []).length;

        if (totalResults === 0) {
            resultsContainer.innerHTML =
                '<div class="empty-state">No results found for "' +
                escapeHtml(data.query || "") +
                '". Try different keywords or a broader search.</div>';
            return;
        }

        // Timing badge
        html.push(
            '<div class="results-meta">Found ' +
                totalResults +
                " results in " +
                escapeHtml(String(data.timing_ms || 0)) +
                "ms</div>"
        );

        // Facts section
        if (data.facts && data.facts.length > 0) {
            html.push('<div class="result-group">');
            html.push('<h3 class="result-group-title">Facts</h3>');
            data.facts.forEach(function (fact) {
                html.push(
                    '<div class="result-card result-fact" data-fact-id="' +
                        escapeHtml(String(fact.id)) +
                        '">'
                );
                html.push(
                    '<span class="badge badge-id">#' +
                        escapeHtml(String(fact.id)) +
                        "</span> "
                );
                html.push(
                    '<span class="badge badge-category">' +
                        escapeHtml(fact.category || "") +
                        "</span> "
                );
                html.push(
                    '<span class="badge badge-confidence">conf ' +
                        escapeHtml(
                            fact.confidence != null
                                ? fact.confidence.toFixed(1)
                                : "?"
                        ) +
                        "</span> "
                );
                html.push(
                    "<span>" + escapeHtml(fact.fact || "") + "</span>"
                );
                if (fact.compressed_details) {
                    html.push(
                        '<div class="compressed-note">[compressed: ' +
                            escapeHtml(fact.compressed_details) +
                            "]</div>"
                    );
                }
                html.push("</div>");
            });
            html.push("</div>");
        }

        // Messages section
        if (data.messages && data.messages.length > 0) {
            html.push('<div class="result-group">');
            html.push('<h3 class="result-group-title">Messages</h3>');
            data.messages.forEach(function (msg) {
                html.push('<div class="result-card result-message">');
                var ts = escapeHtml(
                    (msg.timestamp || "").substring(0, 16)
                );
                var proj = escapeHtml(msg.project || "no-project");
                var role = escapeHtml(msg.role || "?");
                html.push(
                    '<div class="result-meta">[' +
                        ts +
                        "] " +
                        proj +
                        " (" +
                        role +
                        ")</div>"
                );
                html.push(
                    '<div class="result-snippet">' +
                        escapeHtml(msg.content || "") +
                        "</div>"
                );
                html.push("</div>");
            });
            html.push("</div>");
        }

        // Sessions section
        if (data.sessions && data.sessions.length > 0) {
            html.push('<div class="result-group">');
            html.push('<h3 class="result-group-title">Sessions</h3>');
            data.sessions.forEach(function (sess) {
                html.push(
                    '<div class="result-card result-session" data-session-id="' +
                        escapeHtml(sess.session_id || "") +
                        '">'
                );
                var date = escapeHtml(
                    (sess.last_msg || "").substring(0, 10)
                );
                var proj = escapeHtml(sess.project || "no-project");
                html.push(
                    '<div class="result-meta">' +
                        date +
                        " &middot; " +
                        proj +
                        " &middot; " +
                        escapeHtml(String(sess.msg_count || 0)) +
                        " msgs</div>"
                );
                html.push(
                    '<div class="result-snippet">' +
                        escapeHtml(sess.snippets || "") +
                        "</div>"
                );
                html.push("</div>");
            });
            html.push("</div>");
        }

        // Semantic results section
        if (data.semantic && data.semantic.length > 0) {
            html.push('<div class="result-group">');
            html.push(
                '<h3 class="result-group-title">Semantic Matches</h3>'
            );
            data.semantic.forEach(function (msg) {
                html.push('<div class="result-card result-semantic">');
                var ts = escapeHtml(
                    (msg.timestamp || "").substring(0, 16)
                );
                var proj = escapeHtml(msg.project || "no-project");
                var role = escapeHtml(msg.role || "?");
                var score = msg.score != null ? msg.score.toFixed(3) : "?";
                html.push(
                    '<div class="result-meta">[' +
                        ts +
                        "] " +
                        proj +
                        " (" +
                        role +
                        ") score=" +
                        escapeHtml(score) +
                        "</div>"
                );
                html.push(
                    '<div class="result-snippet">' +
                        escapeHtml(msg.content || "") +
                        "</div>"
                );
                html.push("</div>");
            });
            html.push("</div>");
        }

        resultsContainer.innerHTML = html.join("");

        // Attach click handlers for fact inspect
        var factCards = resultsContainer.querySelectorAll(".result-fact");
        factCards.forEach(function (card) {
            card.style.cursor = "pointer";
            card.addEventListener("click", function () {
                var factId = this.dataset.factId;
                if (factId) {
                    showFactInspect(factId);
                }
            });
        });

        // Attach click handlers for session detail
        var sessionCards =
            resultsContainer.querySelectorAll(".result-session");
        sessionCards.forEach(function (card) {
            card.style.cursor = "pointer";
            card.addEventListener("click", function () {
                var sessionId = this.dataset.sessionId;
                if (sessionId) {
                    showSessionDetail(sessionId);
                }
            });
        });
    }

    if (searchBtn) {
        searchBtn.addEventListener("click", handleSearch);
    }

    if (searchInput) {
        searchInput.addEventListener("keydown", function (e) {
            if (e.key === "Enter") {
                handleSearch();
            }
        });
    }

    // --- Fact Inspect View ---

    function showFactInspect(factId) {
        viewStack.push("search-results");
        if (!resultsContainer) return;

        resultsContainer.innerHTML =
            '<div class="empty-state">Loading fact #' +
            escapeHtml(String(factId)) +
            "...</div>";

        fetch("/api/facts/" + encodeURIComponent(factId))
            .then(function (resp) {
                if (!resp.ok) {
                    throw new Error("Fact not found");
                }
                return resp.json();
            })
            .then(function (data) {
                renderFactInspect(data);
            })
            .catch(function (err) {
                resultsContainer.innerHTML =
                    '<div class="error-message">Failed to load fact: ' +
                    escapeHtml(err.message) +
                    "</div>";
            });
    }

    function renderFactInspect(fact) {
        if (!resultsContainer) return;

        var html = [];

        // Back button
        html.push(
            '<button class="btn-back" id="back-to-search">&larr; Back to results</button>'
        );

        // Fact header
        html.push('<div class="inspect-card">');
        html.push(
            "<h3>Fact #" + escapeHtml(String(fact.id)) + "</h3>"
        );
        html.push(
            '<div class="inspect-field"><strong>Category:</strong> ' +
                escapeHtml(fact.category || "") +
                "</div>"
        );
        html.push(
            '<div class="inspect-field"><strong>Confidence:</strong> ' +
                escapeHtml(
                    fact.confidence != null
                        ? fact.confidence.toFixed(1)
                        : "?"
                ) +
                "</div>"
        );
        html.push(
            '<div class="inspect-field"><strong>Project:</strong> ' +
                escapeHtml(fact.project || "general") +
                "</div>"
        );
        html.push(
            '<div class="inspect-field"><strong>Extracted:</strong> ' +
                escapeHtml((fact.timestamp || "unknown").substring(0, 16)) +
                "</div>"
        );
        html.push(
            '<div class="inspect-field"><strong>Fact:</strong> ' +
                escapeHtml(fact.fact || "") +
                "</div>"
        );

        if (fact.compressed_details) {
            html.push(
                '<div class="inspect-field compressed-note"><strong>Compressed details:</strong> ' +
                    escapeHtml(fact.compressed_details) +
                    "</div>"
            );
        }
        html.push("</div>");

        // Source message
        if (fact.source_message) {
            var msg = fact.source_message;
            html.push('<div class="inspect-card">');
            html.push("<h3>Source Message</h3>");
            html.push(
                '<div class="inspect-field result-meta">[' +
                    escapeHtml((msg.timestamp || "").substring(0, 16)) +
                    "] (" +
                    escapeHtml(msg.role || "") +
                    ")</div>"
            );
            html.push(
                '<div class="inspect-field source-content">' +
                    escapeHtml(msg.content || "") +
                    "</div>"
            );
            html.push("</div>");
        }

        // Sibling facts
        if (fact.siblings && fact.siblings.length > 0) {
            html.push('<div class="inspect-card">');
            html.push("<h3>Other Facts from Same Session</h3>");
            fact.siblings.forEach(function (s) {
                html.push(
                    '<div class="inspect-sibling result-card result-fact" data-fact-id="' +
                        escapeHtml(String(s.id)) +
                        '">'
                );
                html.push(
                    '<span class="badge badge-id">#' +
                        escapeHtml(String(s.id)) +
                        "</span> "
                );
                html.push(
                    '<span class="badge badge-category">' +
                        escapeHtml(s.category || "") +
                        "</span> "
                );
                html.push("<span>" + escapeHtml(s.fact || "") + "</span>");
                html.push("</div>");
            });
            html.push("</div>");
        }

        // Entities
        if (fact.entities && fact.entities.length > 0) {
            html.push('<div class="inspect-card">');
            html.push("<h3>Entities from Same Session</h3>");
            fact.entities.forEach(function (e) {
                html.push(
                    '<div class="inspect-entity">' +
                        escapeHtml(e.name || "") +
                        " (" +
                        escapeHtml(e.entity_type || "") +
                        ", " +
                        escapeHtml(String(e.mention_count || 0)) +
                        "x)</div>"
                );
            });
            html.push("</div>");
        }

        resultsContainer.innerHTML = html.join("");

        // Back button handler
        var backBtn = document.getElementById("back-to-search");
        if (backBtn) {
            backBtn.addEventListener("click", function () {
                viewStack.pop();
                if (lastSearchResults) {
                    renderSearchResults(lastSearchResults);
                } else {
                    resultsContainer.innerHTML =
                        '<div class="empty-state">Enter a query to search your memory.</div>';
                }
            });
        }

        // Click handlers for sibling facts
        var siblingCards =
            resultsContainer.querySelectorAll(".inspect-sibling");
        siblingCards.forEach(function (card) {
            card.style.cursor = "pointer";
            card.addEventListener("click", function () {
                var factId = this.dataset.factId;
                if (factId) {
                    showFactInspect(factId);
                }
            });
        });
    }

    // --- Session Detail View ---

    function showSessionDetail(sessionId) {
        viewStack.push("search-results");
        if (!resultsContainer) return;

        resultsContainer.innerHTML =
            '<div class="empty-state">Loading session...</div>';

        fetch(
            "/api/sessions/" +
                encodeURIComponent(sessionId) +
                "?limit=100"
        )
            .then(function (resp) {
                if (!resp.ok) {
                    throw new Error("Session not found");
                }
                return resp.json();
            })
            .then(function (data) {
                renderSessionDetail(data);
            })
            .catch(function (err) {
                resultsContainer.innerHTML =
                    '<div class="error-message">Failed to load session: ' +
                    escapeHtml(err.message) +
                    "</div>";
            });
    }

    function renderSessionDetail(data) {
        if (!resultsContainer) return;

        var html = [];

        // Back button
        html.push(
            '<button class="btn-back" id="back-to-search">&larr; Back to results</button>'
        );

        html.push(
            "<h3>Session: " +
                escapeHtml((data.session_id || "").substring(0, 12)) +
                "...</h3>"
        );
        html.push(
            '<div class="result-meta">Project: ' +
                escapeHtml(data.project || "no-project") +
                " &middot; " +
                escapeHtml(String((data.messages || []).length)) +
                " messages</div>"
        );

        (data.messages || []).forEach(function (msg) {
            var roleClass =
                msg.role === "user" ? "msg-user" : "msg-assistant";
            html.push(
                '<div class="session-message ' + roleClass + '">'
            );
            html.push(
                '<div class="msg-header">' +
                    escapeHtml((msg.timestamp || "").substring(0, 16)) +
                    " &middot; " +
                    escapeHtml(msg.role || "") +
                    "</div>"
            );
            html.push(
                '<div class="msg-content">' +
                    escapeHtml(msg.content || "") +
                    "</div>"
            );
            html.push("</div>");
        });

        resultsContainer.innerHTML = html.join("");

        // Back button handler
        var backBtn = document.getElementById("back-to-search");
        if (backBtn) {
            backBtn.addEventListener("click", function () {
                viewStack.pop();
                if (lastSearchResults) {
                    renderSearchResults(lastSearchResults);
                } else {
                    resultsContainer.innerHTML =
                        '<div class="empty-state">Enter a query to search your memory.</div>';
                }
            });
        }
    }

    // --- Sessions List Section ---

    function loadSessionList(project) {
        var container = document.getElementById("sessions-content");
        if (!container) return;

        var url = "/api/sessions?limit=20";
        if (project) {
            url += "&project=" + encodeURIComponent(project);
        }

        container.innerHTML = '<div class="empty-state">Loading sessions...</div>';

        fetch(url)
            .then(function (resp) {
                return resp.json();
            })
            .then(function (data) {
                renderSessionList(data.sessions || [], container);
            })
            .catch(function (err) {
                container.innerHTML =
                    '<div class="error-message">Failed to load sessions: ' +
                    escapeHtml(err.message) +
                    "</div>";
            });
    }

    function renderSessionList(sessions, container) {
        if (!container) return;

        var html = [];

        // Project filter
        html.push('<div class="session-filter">');
        html.push(
            '<input type="text" id="session-project-filter" placeholder="Filter by project..." class="filter-input">'
        );
        html.push(
            '<button id="session-filter-btn" class="btn-primary btn-sm">Filter</button>'
        );
        html.push("</div>");

        if (sessions.length === 0) {
            html.push(
                '<div class="empty-state">No sessions found.</div>'
            );
        } else {
            sessions.forEach(function (sess) {
                html.push(
                    '<div class="result-card session-list-item" data-session-id="' +
                        escapeHtml(sess.session_id || "") +
                        '">'
                );
                var date = escapeHtml(
                    (sess.last_msg || "").substring(0, 10)
                );
                var proj = escapeHtml(sess.project || "no-project");
                html.push(
                    '<div class="result-meta">' +
                        date +
                        " &middot; " +
                        proj +
                        " &middot; " +
                        escapeHtml(String(sess.msg_count || 0)) +
                        " msgs</div>"
                );
                html.push(
                    '<div class="result-snippet">' +
                        escapeHtml(sess.snippets || "") +
                        "</div>"
                );
                html.push("</div>");
            });
        }

        container.innerHTML = html.join("");

        // Session click handlers
        var items = container.querySelectorAll(".session-list-item");
        items.forEach(function (item) {
            item.style.cursor = "pointer";
            item.addEventListener("click", function () {
                var sessionId = this.dataset.sessionId;
                if (sessionId) {
                    showSessionDetailInSection(sessionId, container);
                }
            });
        });

        // Filter handler
        var filterBtn = document.getElementById("session-filter-btn");
        var filterInput = document.getElementById(
            "session-project-filter"
        );
        if (filterBtn && filterInput) {
            filterBtn.addEventListener("click", function () {
                loadSessionList(filterInput.value.trim() || null);
            });
            filterInput.addEventListener("keydown", function (e) {
                if (e.key === "Enter") {
                    loadSessionList(filterInput.value.trim() || null);
                }
            });
        }
    }

    function showSessionDetailInSection(sessionId, container) {
        if (!container) return;

        container.innerHTML =
            '<div class="empty-state">Loading session...</div>';

        fetch(
            "/api/sessions/" +
                encodeURIComponent(sessionId) +
                "?limit=100"
        )
            .then(function (resp) {
                if (!resp.ok) throw new Error("Session not found");
                return resp.json();
            })
            .then(function (data) {
                var html = [];
                html.push(
                    '<button class="btn-back" id="back-to-sessions">&larr; Back to sessions</button>'
                );
                html.push(
                    "<h3>Session: " +
                        escapeHtml(
                            (data.session_id || "").substring(0, 12)
                        ) +
                        "...</h3>"
                );
                html.push(
                    '<div class="result-meta">Project: ' +
                        escapeHtml(data.project || "no-project") +
                        " &middot; " +
                        escapeHtml(
                            String((data.messages || []).length)
                        ) +
                        " messages</div>"
                );

                (data.messages || []).forEach(function (msg) {
                    var roleClass =
                        msg.role === "user"
                            ? "msg-user"
                            : "msg-assistant";
                    html.push(
                        '<div class="session-message ' +
                            roleClass +
                            '">'
                    );
                    html.push(
                        '<div class="msg-header">' +
                            escapeHtml(
                                (msg.timestamp || "").substring(0, 16)
                            ) +
                            " &middot; " +
                            escapeHtml(msg.role || "") +
                            "</div>"
                    );
                    html.push(
                        '<div class="msg-content">' +
                            escapeHtml(msg.content || "") +
                            "</div>"
                    );
                    html.push("</div>");
                });

                container.innerHTML = html.join("");

                var backBtn = document.getElementById(
                    "back-to-sessions"
                );
                if (backBtn) {
                    backBtn.addEventListener("click", function () {
                        loadSessionList();
                    });
                }
            })
            .catch(function (err) {
                container.innerHTML =
                    '<div class="error-message">Failed to load session: ' +
                    escapeHtml(err.message) +
                    "</div>";
            });
    }

    // --- Facts Management Section ---

    var factsState = {
        offset: 0,
        limit: 20,
        total: 0,
        category: "",
        project: "",
        minConfidence: "",
        maxConfidence: "",
        sort: "timestamp",
        order: "desc",
    };

    function loadFacts() {
        var container = document.getElementById("facts-content");
        if (!container) return;

        container.innerHTML =
            '<div class="empty-state">Loading facts...</div>';

        var url =
            "/api/facts?offset=" +
            factsState.offset +
            "&limit=" +
            factsState.limit;

        if (factsState.category) {
            url += "&category=" + encodeURIComponent(factsState.category);
        }
        if (factsState.project) {
            url += "&project=" + encodeURIComponent(factsState.project);
        }
        if (factsState.minConfidence !== "") {
            url += "&min_confidence=" + encodeURIComponent(factsState.minConfidence);
        }
        if (factsState.maxConfidence !== "") {
            url += "&max_confidence=" + encodeURIComponent(factsState.maxConfidence);
        }
        url += "&sort=" + encodeURIComponent(factsState.sort);
        url += "&order=" + encodeURIComponent(factsState.order);

        fetch(url)
            .then(function (resp) {
                return resp.json();
            })
            .then(function (data) {
                factsState.total = data.total;
                renderFacts(data.facts, container);
                renderFactsPagination();
            })
            .catch(function (err) {
                container.innerHTML =
                    '<div class="error-message">Failed to load facts: ' +
                    escapeHtml(err.message) +
                    "</div>";
            });
    }

    function renderFacts(facts, container) {
        if (!container) return;

        if (facts.length === 0) {
            container.innerHTML =
                '<div class="empty-state">No facts found matching filters.</div>';
            return;
        }

        var html = [];

        facts.forEach(function (fact) {
            html.push(
                '<div class="fact-card" id="fact-card-' +
                    escapeHtml(String(fact.id)) +
                    '">'
            );

            // Header: badges
            html.push('<div class="fact-card-header">');
            html.push(
                '<span class="badge badge-id">#' +
                    escapeHtml(String(fact.id)) +
                    "</span>"
            );
            html.push(
                '<span class="badge badge-category">' +
                    escapeHtml(fact.category || "") +
                    "</span>"
            );
            html.push(
                '<span class="badge badge-confidence">conf ' +
                    escapeHtml(
                        fact.confidence != null
                            ? fact.confidence.toFixed(2)
                            : "?"
                    ) +
                    "</span>"
            );
            if (fact.project) {
                html.push(
                    '<span class="badge" style="background:#e3f2fd;color:#1565c0">' +
                        escapeHtml(fact.project) +
                        "</span>"
                );
            }
            if (fact.timestamp) {
                html.push(
                    '<span class="result-meta" style="margin-left:auto">' +
                        escapeHtml(fact.timestamp.substring(0, 16)) +
                        "</span>"
                );
            }
            html.push("</div>");

            // Fact text
            html.push(
                '<div class="fact-card-text" id="fact-text-' +
                    escapeHtml(String(fact.id)) +
                    '">' +
                    escapeHtml(fact.fact || "") +
                    "</div>"
            );

            if (fact.compressed_details) {
                html.push(
                    '<div class="compressed-note">[compressed: ' +
                        escapeHtml(fact.compressed_details) +
                        "]</div>"
                );
            }

            // Actions
            html.push('<div class="fact-card-actions">');

            // Edit button
            html.push(
                '<button class="btn-edit" data-fact-id="' +
                    escapeHtml(String(fact.id)) +
                    '" data-fact-text="' +
                    escapeHtml(fact.fact || "").replace(/"/g, "&quot;") +
                    '">Edit</button>'
            );

            // Confidence slider
            html.push('<div class="confidence-slider-container">');
            html.push(
                '<input type="range" class="confidence-slider" min="0" max="1" step="0.05" ' +
                    'value="' +
                    escapeHtml(String(fact.confidence != null ? fact.confidence : 0.5)) +
                    '" data-fact-id="' +
                    escapeHtml(String(fact.id)) +
                    '">'
            );
            html.push(
                '<span class="confidence-value" id="conf-val-' +
                    escapeHtml(String(fact.id)) +
                    '">' +
                    escapeHtml(
                        fact.confidence != null
                            ? fact.confidence.toFixed(2)
                            : "0.50"
                    ) +
                    "</span>"
            );
            html.push("</div>");

            // Delete button
            html.push(
                '<button class="btn-delete" data-fact-id="' +
                    escapeHtml(String(fact.id)) +
                    '">Delete</button>'
            );

            html.push("</div>"); // actions
            html.push("</div>"); // card
        });

        container.innerHTML = html.join("");

        // Attach event handlers
        attachFactEventHandlers(container);
    }

    function attachFactEventHandlers(container) {
        // Edit buttons
        var editBtns = container.querySelectorAll(".btn-edit");
        editBtns.forEach(function (btn) {
            btn.addEventListener("click", function () {
                var factId = this.dataset.factId;
                var factText = this.dataset.factText;
                startFactEdit(factId, factText);
            });
        });

        // Confidence sliders
        var sliders = container.querySelectorAll(".confidence-slider");
        sliders.forEach(function (slider) {
            // Update display value on input
            slider.addEventListener("input", function () {
                var factId = this.dataset.factId;
                var valEl = document.getElementById("conf-val-" + factId);
                if (valEl) {
                    valEl.textContent = parseFloat(this.value).toFixed(2);
                }
            });
            // Auto-save on change (mouse release)
            slider.addEventListener("change", function () {
                var factId = this.dataset.factId;
                var newConf = parseFloat(this.value);
                updateFactConfidence(factId, newConf);
            });
        });

        // Delete buttons
        var deleteBtns = container.querySelectorAll(".btn-delete");
        deleteBtns.forEach(function (btn) {
            btn.addEventListener("click", function () {
                var factId = this.dataset.factId;
                showDeleteConfirmation(factId);
            });
        });
    }

    function startFactEdit(factId, currentText) {
        var textEl = document.getElementById("fact-text-" + factId);
        if (!textEl) return;

        // Replace text with textarea
        var html =
            '<textarea class="fact-edit-textarea" id="edit-textarea-' +
            escapeHtml(factId) +
            '">' +
            escapeHtml(currentText) +
            "</textarea>" +
            '<div style="display:flex;gap:0.5rem">' +
            '<button class="btn-save" id="save-edit-' +
            escapeHtml(factId) +
            '">Save</button>' +
            '<button class="btn-cancel" id="cancel-edit-' +
            escapeHtml(factId) +
            '">Cancel</button>' +
            "</div>";

        textEl.innerHTML = html;

        // Save handler
        var saveBtn = document.getElementById("save-edit-" + factId);
        if (saveBtn) {
            saveBtn.addEventListener("click", function () {
                var textarea = document.getElementById(
                    "edit-textarea-" + factId
                );
                if (textarea) {
                    var newText = textarea.value.trim();
                    if (newText) {
                        saveFactText(factId, newText);
                    }
                }
            });
        }

        // Cancel handler
        var cancelBtn = document.getElementById("cancel-edit-" + factId);
        if (cancelBtn) {
            cancelBtn.addEventListener("click", function () {
                textEl.textContent = currentText;
            });
        }

        // Focus the textarea
        var textarea = document.getElementById("edit-textarea-" + factId);
        if (textarea) {
            textarea.focus();
        }
    }

    function saveFactText(factId, newText) {
        fetch("/api/facts/" + encodeURIComponent(factId), {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ fact: newText }),
        })
            .then(function (resp) {
                if (!resp.ok) throw new Error("Failed to save");
                return resp.json();
            })
            .then(function (data) {
                // Update the text element
                var textEl = document.getElementById(
                    "fact-text-" + factId
                );
                if (textEl) {
                    textEl.textContent = data.fact;
                }
                // Update the edit button data attribute
                var card = document.getElementById("fact-card-" + factId);
                if (card) {
                    var editBtn = card.querySelector(".btn-edit");
                    if (editBtn) {
                        editBtn.dataset.factText = data.fact;
                    }
                }
            })
            .catch(function (err) {
                alert("Failed to save fact: " + err.message);
            });
    }

    function updateFactConfidence(factId, newConfidence) {
        fetch("/api/facts/" + encodeURIComponent(factId), {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ confidence: newConfidence }),
        })
            .then(function (resp) {
                if (!resp.ok) throw new Error("Failed to update confidence");
                return resp.json();
            })
            .then(function (data) {
                // Update badge
                var card = document.getElementById("fact-card-" + factId);
                if (card) {
                    var confBadge = card.querySelector(".badge-confidence");
                    if (confBadge) {
                        confBadge.textContent =
                            "conf " + data.confidence.toFixed(2);
                    }
                }
            })
            .catch(function (err) {
                alert("Failed to update confidence: " + err.message);
            });
    }

    function showDeleteConfirmation(factId) {
        // Create overlay
        var overlay = document.createElement("div");
        overlay.className = "confirm-overlay";
        overlay.innerHTML =
            '<div class="confirm-dialog">' +
            "<p>Are you sure you want to delete fact #" +
            escapeHtml(factId) +
            "?</p>" +
            '<div class="confirm-dialog-actions">' +
            '<button class="btn-cancel" id="confirm-cancel">Cancel</button>' +
            '<button class="btn-delete" id="confirm-delete" style="padding:0.4rem 1rem;font-size:0.9rem">Delete</button>' +
            "</div>" +
            "</div>";

        document.body.appendChild(overlay);

        // Cancel
        document
            .getElementById("confirm-cancel")
            .addEventListener("click", function () {
                document.body.removeChild(overlay);
            });

        // Confirm delete
        document
            .getElementById("confirm-delete")
            .addEventListener("click", function () {
                document.body.removeChild(overlay);
                deleteFact(factId);
            });

        // Close on overlay click
        overlay.addEventListener("click", function (e) {
            if (e.target === overlay) {
                document.body.removeChild(overlay);
            }
        });
    }

    function deleteFact(factId) {
        fetch("/api/facts/" + encodeURIComponent(factId), {
            method: "DELETE",
        })
            .then(function (resp) {
                if (!resp.ok) throw new Error("Failed to delete");
                return resp.json();
            })
            .then(function () {
                // Remove the card from DOM immediately
                var card = document.getElementById("fact-card-" + factId);
                if (card) {
                    card.remove();
                }
                // Update total count
                factsState.total = Math.max(0, factsState.total - 1);
                renderFactsPagination();

                // If the page is now empty and not the first page, go back
                var container = document.getElementById("facts-content");
                if (
                    container &&
                    container.querySelectorAll(".fact-card").length === 0 &&
                    factsState.offset > 0
                ) {
                    factsState.offset = Math.max(
                        0,
                        factsState.offset - factsState.limit
                    );
                    loadFacts();
                }
            })
            .catch(function (err) {
                alert("Failed to delete fact: " + err.message);
            });
    }

    function renderFactsPagination() {
        var paginationEl = document.getElementById("facts-pagination");
        if (!paginationEl) return;

        var totalPages = Math.ceil(factsState.total / factsState.limit);
        var currentPage =
            Math.floor(factsState.offset / factsState.limit) + 1;

        if (totalPages <= 1) {
            paginationEl.innerHTML =
                '<span class="pagination-info">' +
                escapeHtml(String(factsState.total)) +
                " facts</span>";
            return;
        }

        var html = [];
        html.push(
            '<button class="btn-page" id="facts-prev"' +
                (currentPage <= 1 ? " disabled" : "") +
                ">&larr; Prev</button>"
        );
        html.push(
            '<span class="pagination-info">Page ' +
                escapeHtml(String(currentPage)) +
                " of " +
                escapeHtml(String(totalPages)) +
                " (" +
                escapeHtml(String(factsState.total)) +
                " facts)</span>"
        );
        html.push(
            '<button class="btn-page" id="facts-next"' +
                (currentPage >= totalPages ? " disabled" : "") +
                ">Next &rarr;</button>"
        );

        paginationEl.innerHTML = html.join("");

        // Prev button
        var prevBtn = document.getElementById("facts-prev");
        if (prevBtn && currentPage > 1) {
            prevBtn.addEventListener("click", function () {
                factsState.offset = Math.max(
                    0,
                    factsState.offset - factsState.limit
                );
                loadFacts();
            });
        }

        // Next button
        var nextBtn = document.getElementById("facts-next");
        if (nextBtn && currentPage < totalPages) {
            nextBtn.addEventListener("click", function () {
                factsState.offset += factsState.limit;
                loadFacts();
            });
        }
    }

    // Populate project dropdown from DB
    function populateFactsProjectFilter() {
        fetch("/api/facts?limit=200&sort=timestamp&order=desc")
            .then(function (resp) {
                return resp.json();
            })
            .then(function (data) {
                var projects = {};
                (data.facts || []).forEach(function (f) {
                    if (f.project) {
                        projects[f.project] = true;
                    }
                });
                var select = document.getElementById(
                    "facts-project-filter"
                );
                if (select) {
                    var keys = Object.keys(projects).sort();
                    keys.forEach(function (p) {
                        var opt = document.createElement("option");
                        opt.value = p;
                        opt.textContent = p;
                        select.appendChild(opt);
                    });
                }
            })
            .catch(function () {
                // Ignore errors — the filter just won't have project options
            });
    }

    // Wire up facts filter/sort controls
    function initFactsControls() {
        var categoryFilter = document.getElementById(
            "facts-category-filter"
        );
        var projectFilter = document.getElementById(
            "facts-project-filter"
        );
        var minConfInput = document.getElementById(
            "facts-min-confidence"
        );
        var maxConfInput = document.getElementById(
            "facts-max-confidence"
        );
        var sortField = document.getElementById("facts-sort-field");
        var sortOrderBtn = document.getElementById("facts-sort-order");

        if (categoryFilter) {
            categoryFilter.addEventListener("change", function () {
                factsState.category = this.value;
                factsState.offset = 0;
                loadFacts();
            });
        }

        if (projectFilter) {
            projectFilter.addEventListener("change", function () {
                factsState.project = this.value;
                factsState.offset = 0;
                loadFacts();
            });
        }

        if (minConfInput) {
            minConfInput.addEventListener("change", function () {
                factsState.minConfidence = this.value;
                factsState.offset = 0;
                loadFacts();
            });
        }

        if (maxConfInput) {
            maxConfInput.addEventListener("change", function () {
                factsState.maxConfidence = this.value;
                factsState.offset = 0;
                loadFacts();
            });
        }

        if (sortField) {
            sortField.addEventListener("change", function () {
                factsState.sort = this.value;
                factsState.offset = 0;
                loadFacts();
            });
        }

        if (sortOrderBtn) {
            sortOrderBtn.addEventListener("click", function () {
                if (factsState.order === "desc") {
                    factsState.order = "asc";
                    this.innerHTML = "&#x25B2; ASC";
                } else {
                    factsState.order = "desc";
                    this.innerHTML = "&#x25BC; DESC";
                }
                factsState.offset = 0;
                loadFacts();
            });
        }
    }

    // Initialize facts controls and load on nav
    initFactsControls();
    populateFactsProjectFilter();

    // Update nav click handler to load facts when navigating to facts section
    navLinks.forEach(function (link) {
        if (link.dataset.section === "facts") {
            link.addEventListener("click", function () {
                factsState.offset = 0;
                loadFacts();
            });
        }
    });

    // --- CLAUDE.md Editor ---

    var claudeMdEditor = document.getElementById("claude-md-editor");
    var claudeMdPreview = document.getElementById("claude-md-preview");
    var claudeMdSave = document.getElementById("claude-md-save");
    var claudeMdPath = document.getElementById("claude-md-path");
    var claudeMdStatus = document.getElementById("claude-md-status");
    var claudeMdLoaded = false;
    var claudeMdDebounceTimer = null;

    /**
     * Simple markdown to HTML renderer using regex replacements.
     * Handles: headings, bold, italic, inline code, code blocks,
     * unordered lists, ordered lists, tables, horizontal rules, paragraphs.
     */
    function renderMarkdown(md) {
        if (!md) return '<p class="empty-state" style="padding:1rem">No content</p>';

        var html = md;

        // Fenced code blocks first (```...```)
        html = html.replace(/```([a-zA-Z]*)\n([\s\S]*?)```/g, function (_, lang, code) {
            return '<pre><code>' + escapeHtml(code.replace(/\n$/, '')) + '</code></pre>';
        });

        // Split into lines for block-level processing
        var lines = html.split('\n');
        var result = [];
        var inList = false;
        var listType = '';
        var inTable = false;
        var tableRows = [];

        for (var i = 0; i < lines.length; i++) {
            var line = lines[i];

            // Skip lines inside code blocks (already processed)
            if (line.indexOf('<pre><code>') !== -1) {
                // Find the closing tag
                var blockEnd = line.indexOf('</code></pre>');
                if (blockEnd !== -1) {
                    result.push(line);
                    continue;
                }
                result.push(line);
                while (i + 1 < lines.length && lines[i + 1].indexOf('</code></pre>') === -1) {
                    i++;
                    result.push(lines[i]);
                }
                if (i + 1 < lines.length) {
                    i++;
                    result.push(lines[i]);
                }
                continue;
            }

            // Table rows
            if (line.match(/^\|(.+)\|$/)) {
                if (!inTable) {
                    inTable = true;
                    tableRows = [];
                }
                // Skip separator rows (|---|---|)
                if (line.match(/^\|[\s\-:|]+\|$/)) {
                    continue;
                }
                tableRows.push(line);
                continue;
            } else if (inTable) {
                // End table
                result.push(renderTable(tableRows));
                tableRows = [];
                inTable = false;
            }

            // Horizontal rule
            if (line.match(/^(\*{3,}|-{3,}|_{3,})$/)) {
                if (inList) {
                    result.push(listType === 'ul' ? '</ul>' : '</ol>');
                    inList = false;
                }
                result.push('<hr>');
                continue;
            }

            // Headings
            var headingMatch = line.match(/^(#{1,6})\s+(.+)$/);
            if (headingMatch) {
                if (inList) {
                    result.push(listType === 'ul' ? '</ul>' : '</ol>');
                    inList = false;
                }
                var level = headingMatch[1].length;
                result.push('<h' + level + '>' + renderInline(headingMatch[2]) + '</h' + level + '>');
                continue;
            }

            // Unordered list items
            var ulMatch = line.match(/^(\s*)[-*+]\s+(.+)$/);
            if (ulMatch) {
                if (!inList || listType !== 'ul') {
                    if (inList) result.push(listType === 'ul' ? '</ul>' : '</ol>');
                    result.push('<ul>');
                    inList = true;
                    listType = 'ul';
                }
                result.push('<li>' + renderInline(ulMatch[2]) + '</li>');
                continue;
            }

            // Ordered list items
            var olMatch = line.match(/^(\s*)\d+\.\s+(.+)$/);
            if (olMatch) {
                if (!inList || listType !== 'ol') {
                    if (inList) result.push(listType === 'ul' ? '</ul>' : '</ol>');
                    result.push('<ol>');
                    inList = true;
                    listType = 'ol';
                }
                result.push('<li>' + renderInline(olMatch[2]) + '</li>');
                continue;
            }

            // Close open list on non-list line
            if (inList) {
                result.push(listType === 'ul' ? '</ul>' : '</ol>');
                inList = false;
            }

            // Empty line
            if (line.trim() === '') {
                continue;
            }

            // Paragraph
            result.push('<p>' + renderInline(line) + '</p>');
        }

        // Close any open list or table
        if (inList) {
            result.push(listType === 'ul' ? '</ul>' : '</ol>');
        }
        if (inTable && tableRows.length > 0) {
            result.push(renderTable(tableRows));
        }

        return result.join('\n');
    }

    /**
     * Render inline markdown: bold, italic, inline code, links.
     */
    function renderInline(text) {
        // Inline code (must come before bold/italic to avoid conflicts)
        text = text.replace(/`([^`]+)`/g, '<code>' + '$1' + '</code>');
        // Bold + italic
        text = text.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
        // Bold
        text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        text = text.replace(/__(.+?)__/g, '<strong>$1</strong>');
        // Italic
        text = text.replace(/\*(.+?)\*/g, '<em>$1</em>');
        text = text.replace(/_(.+?)_/g, '<em>$1</em>');
        return text;
    }

    /**
     * Render a simple markdown table from pipe-delimited rows.
     */
    function renderTable(rows) {
        if (rows.length === 0) return '';
        var html = '<table>';
        for (var i = 0; i < rows.length; i++) {
            var cells = rows[i].split('|').filter(function (c, idx, arr) {
                return idx > 0 && idx < arr.length - 1;
            });
            var tag = (i === 0) ? 'th' : 'td';
            html += '<tr>';
            for (var j = 0; j < cells.length; j++) {
                html += '<' + tag + '>' + renderInline(cells[j].trim()) + '</' + tag + '>';
            }
            html += '</tr>';
        }
        html += '</table>';
        return html;
    }

    function loadClaudeMd() {
        if (!claudeMdEditor) return;

        fetch("/api/claude-md")
            .then(function (resp) { return resp.json(); })
            .then(function (data) {
                claudeMdEditor.value = data.content || "";
                if (claudeMdPath) {
                    claudeMdPath.textContent = data.path || "~/.claude/CLAUDE.md";
                    if (data.exists === false) {
                        claudeMdPath.textContent += " (new file)";
                    }
                }
                updateClaudeMdPreview();
                claudeMdLoaded = true;
            })
            .catch(function (err) {
                if (claudeMdStatus) {
                    claudeMdStatus.textContent = "Failed to load: " + err.message;
                    claudeMdStatus.className = "save-status error";
                }
            });
    }

    function updateClaudeMdPreview() {
        if (!claudeMdPreview || !claudeMdEditor) return;
        claudeMdPreview.innerHTML = renderMarkdown(claudeMdEditor.value);
    }

    function saveClaudeMd() {
        if (!claudeMdEditor) return;

        var content = claudeMdEditor.value;
        if (claudeMdStatus) {
            claudeMdStatus.textContent = "Saving...";
            claudeMdStatus.className = "save-status";
        }

        fetch("/api/claude-md", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ content: content }),
        })
            .then(function (resp) {
                if (!resp.ok) throw new Error("Save failed");
                return resp.json();
            })
            .then(function (data) {
                if (claudeMdStatus) {
                    claudeMdStatus.textContent = "Saved (" + data.bytes + " bytes)";
                    claudeMdStatus.className = "save-status success";
                    setTimeout(function () {
                        claudeMdStatus.textContent = "";
                    }, 3000);
                }
            })
            .catch(function (err) {
                if (claudeMdStatus) {
                    claudeMdStatus.textContent = "Error: " + err.message;
                    claudeMdStatus.className = "save-status error";
                }
            });
    }

    // Wire up editor events
    if (claudeMdEditor) {
        claudeMdEditor.addEventListener("input", function () {
            if (claudeMdDebounceTimer) clearTimeout(claudeMdDebounceTimer);
            claudeMdDebounceTimer = setTimeout(updateClaudeMdPreview, 150);
        });
    }

    if (claudeMdSave) {
        claudeMdSave.addEventListener("click", saveClaudeMd);
    }

    // --- Memory Context Preview ---

    var tokenSlider = document.getElementById("token-budget-slider");
    var tokenValue = document.getElementById("token-budget-value");
    var contextProjectFilter = document.getElementById("context-project-filter");
    var contextRegenerate = document.getElementById("context-regenerate");
    var contextOutput = document.getElementById("context-preview-output");
    var contextStatus = document.getElementById("context-status");
    var contextDebounceTimer = null;
    var contextPreviewLoaded = false;

    function loadContextPreview() {
        if (!contextOutput) return;

        var maxTokens = tokenSlider ? tokenSlider.value : "2000";
        var project = contextProjectFilter ? contextProjectFilter.value : "";

        contextOutput.textContent = "Loading context preview...";
        if (contextStatus) {
            contextStatus.textContent = "";
        }

        var url = "/api/context-preview?max_tokens=" + encodeURIComponent(maxTokens);
        if (project) {
            url += "&project=" + encodeURIComponent(project);
        }

        fetch(url)
            .then(function (resp) { return resp.json(); })
            .then(function (data) {
                if (data.error) {
                    contextOutput.textContent = "Error: " + data.error;
                    if (contextStatus) {
                        contextStatus.textContent = "Error generating preview";
                    }
                } else {
                    contextOutput.textContent = data.content || "(empty output)";
                    if (contextStatus) {
                        contextStatus.textContent = "~" + data.tokens_estimate + " tokens estimated";
                    }
                }
            })
            .catch(function (err) {
                contextOutput.textContent = "Failed to load: " + err.message;
                if (contextStatus) {
                    contextStatus.textContent = "Error";
                }
            });
    }

    function populateContextProjectFilter() {
        if (!contextProjectFilter) return;

        fetch("/api/projects")
            .then(function (resp) { return resp.json(); })
            .then(function (data) {
                var projects = data.projects || [];
                projects.forEach(function (p) {
                    var opt = document.createElement("option");
                    opt.value = p;
                    opt.textContent = p;
                    contextProjectFilter.appendChild(opt);
                });
            })
            .catch(function () {
                // Ignore — filter just won't have options
            });
    }

    // Wire up context preview controls
    if (tokenSlider) {
        tokenSlider.addEventListener("input", function () {
            if (tokenValue) tokenValue.textContent = this.value;
        });
        tokenSlider.addEventListener("change", function () {
            if (contextDebounceTimer) clearTimeout(contextDebounceTimer);
            contextDebounceTimer = setTimeout(loadContextPreview, 300);
        });
    }

    if (contextProjectFilter) {
        contextProjectFilter.addEventListener("change", function () {
            loadContextPreview();
        });
    }

    if (contextRegenerate) {
        contextRegenerate.addEventListener("click", loadContextPreview);
    }

    populateContextProjectFilter();

    // Load content on section navigation
    navLinks.forEach(function (link) {
        if (link.dataset.section === "claude-md") {
            link.addEventListener("click", function () {
                if (!claudeMdLoaded) {
                    loadClaudeMd();
                }
            });
        }
        if (link.dataset.section === "context-preview") {
            link.addEventListener("click", function () {
                if (!contextPreviewLoaded) {
                    loadContextPreview();
                    contextPreviewLoaded = true;
                }
            });
        }
    });
})();
