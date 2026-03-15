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
        if (resultsContainer) {
            resultsContainer.innerHTML =
                '<div class="empty-state">Searching...</div>';
        }

        fetch("/api/search?q=" + encodeURIComponent(query) + "&limit=20")
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
})();
