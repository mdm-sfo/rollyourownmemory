/**
 * Claude Memory — Base Application JavaScript
 *
 * Handles navigation routing (show/hide sections), mode toggle,
 * and provides utility functions for HTML escaping.
 */

(function () {
    "use strict";

    // --- Utility: HTML escaping for XSS prevention ---

    const ESC_MAP = {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
    };

    /**
     * Escape HTML special characters to prevent XSS.
     * @param {string} str - Raw string to escape
     * @returns {string} HTML-safe string
     */
    function escapeHtml(str) {
        if (typeof str !== "string") return "";
        return str.replace(/[&<>"']/g, function (ch) {
            return ESC_MAP[ch];
        });
    }

    // Expose globally for other scripts
    window.escapeHtml = escapeHtml;

    // --- Navigation routing ---

    const navLinks = document.querySelectorAll(".nav-link");
    const sections = document.querySelectorAll(".section");

    function navigateTo(sectionName) {
        // Update nav active state
        navLinks.forEach(function (link) {
            if (link.dataset.section === sectionName) {
                link.classList.add("active");
            } else {
                link.classList.remove("active");
            }
        });

        // Show/hide sections
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
        });
    });

    // --- Mode toggle (Search / Ask) ---

    var currentMode = "search";
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

    // Expose current mode getter
    window.getSearchMode = function () {
        return currentMode;
    };

    // --- Search form handling (placeholder for future features) ---

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
        // Placeholder: actual search API integration will be added in search-api-and-frontend feature
        if (resultsContainer) {
            resultsContainer.innerHTML =
                '<div class="empty-state">Searching for: ' +
                escapeHtml(query) +
                "</div>";
        }
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
})();
